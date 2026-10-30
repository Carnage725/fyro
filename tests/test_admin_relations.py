"""The admin with ForeignKeys: pickers, labels, link filters, errors and query counts."""

import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from support import MigratedSchema

import fyro.db
from fyro import Field, ForeignKey, ManyToMany, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import get_settings
from fyro.db import engine as db_engine

pytestmark = pytest.mark.asyncio(loop_scope="module")

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)
PASSWORD = "correct horse battery staple"
HX = {"HX-Request": "true"}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.startswith("FYRO_"):
                mp.delenv(name)
        mp.setenv("FYRO_SETTINGS", "fixture_settings")
        mp.setenv("FYRO_DATABASE_URL", DATABASE_URL)
        mp.syspath_prepend(str(FIXTURES))
        get_settings.cache_clear()
        mp.setattr(apps, "registry", Registry())
        apps.registry.populate(["fyro.auth", "blog"])

        class Author(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=100)

            def __str__(self) -> str:
                return self.name

        class Tag(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=50)

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)
            author: Author = ForeignKey(on_delete="cascade")
            editor: Author | None = ForeignKey(on_delete="set_null", default=None)
            tag: Tag | None = ForeignKey(on_delete="set_null", default=None)
            tags: list[Tag] = ManyToMany()

        class Review(Model):
            __module__ = "blog.models"
            author: Author = ForeignKey(on_delete="restrict")

        schema = MigratedSchema(tmp_path_factory.mktemp("schema"), DATABASE_URL)
        await schema.create()
        yield create_app()
        await schema.drop()
        await db_engine.dispose_engine()
        Model.registry.dispose()
        Model.metadata.clear()
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "fixture_settings") or name.startswith("fyro.auth"):
                del sys.modules[name]


@pytest_asyncio.fixture(loop_scope="module")
async def client(app):
    async with fyro.db.get_engine().begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE auth_user, auth_session, blog_author, blog_tag, blog_post_tags, blog_post, "
                "blog_review RESTART IDENTITY"
            )
        )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        from fyro.auth import User
        from fyro.auth.passwords import hash_password

        async with fyro.db.session():
            await User.objects.create(
                username="root", password=hash_password(PASSWORD), is_superuser=True
            )
        response = await c.post("/api/auth/login", json={"username": "root", "password": PASSWORD})
        assert response.status_code == 200
        yield c


async def sql(*statements):
    async with fyro.db.get_engine().begin() as conn:
        result = None
        for statement in statements:
            result = await conn.execute(text(statement))
        return result.all() if result is not None and result.returns_rows else None


async def seed_authors():
    await sql("INSERT INTO blog_author (name) VALUES ('ana'), ('bob')")


def options(html, name):
    """(value, text, selected) for each <option> of the <select name=...>."""
    select = re.search(rf'<select[^>]*name="{name}".*?</select>', html, re.DOTALL).group(0)
    return [
        (value, label.strip(), bool(selected))
        for value, selected, label in re.findall(
            r'<option value="([^"]*)"\s*(selected)?\s*>([^<]*)</option>', select
        )
    ]


@contextmanager
def count_selects():
    selects: list[str] = []

    def record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    engine = fyro.db.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield selects
    finally:
        event.remove(engine, "before_cursor_execute", record)


# --- form pickers ----------------------------------------------------------


async def test_add_form_has_dropdowns_labelled_by_str(client):
    await seed_authors()
    await sql("INSERT INTO blog_tag (name) VALUES ('news')")
    html = (await client.get("/admin/blog/post/add")).text
    assert options(html, "author_id") == [("1", "ana", False), ("2", "bob", False)]
    assert options(html, "editor_id") == [("", "—", True), ("1", "ana", False), ("2", "bob", False)]
    # no __str__ on Tag: "<Model> #<id>"
    assert options(html, "tag_id") == [("", "—", True), ("1", "Tag #1", False)]
    assert re.search(r'<label for="f-author_id">Author \*</label>', html)


async def test_edit_form_selects_current_row(client):
    await seed_authors()
    await sql("INSERT INTO blog_post (title, author_id) VALUES ('t', 2)")
    html = (await client.get("/admin/blog/post/1")).text
    assert ("2", "bob", True) in options(html, "author_id")


async def test_many_targets_give_a_search_box(client):
    await seed_authors()
    await sql(
        "INSERT INTO blog_tag (name) SELECT 'tag ' || n FROM generate_series(1, 101) AS n",
        "INSERT INTO blog_post (title, author_id, tag_id) VALUES ('t', 1, 7)",
    )
    html = (await client.get("/admin/blog/post/1")).text
    assert 'hx-get="/admin/blog/post/link-options/tag_id"' in html
    assert options(html, "tag_id") == [("", "—", False), ("7", "Tag #7", True)]

    found = (
        await client.get("/admin/blog/post/link-options/tag_id", params={"_search": "tag 10"})
    ).text
    values = re.findall(r'<option value="([^"]*)"', found)
    assert values == ["", "10", "100", "101"]
    by_id = (
        await client.get("/admin/blog/post/link-options/tag_id", params={"_search": "42"})
    ).text
    assert '<option value="42"' in by_id


async def test_link_options_unknown_field_is_404(client):
    assert (await client.get("/admin/blog/post/link-options/title")).status_code == 404


# --- saving ----------------------------------------------------------------


async def test_save_with_picked_row(client):
    await seed_authors()
    data = {"title": "t", "author_id": "2", "editor_id": "", "tag_id": "", "tag_ids": []}
    response = await client.post("/admin/blog/post/add", data=data, headers=HX)
    assert response.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT author_id, editor_id FROM blog_post") == [(2, None)]


async def test_save_with_missing_row_shows_field_error(client):
    await seed_authors()
    data = {"title": "t", "author_id": "999", "editor_id": "", "tag_id": "", "tag_ids": []}
    response = await client.post("/admin/blog/post/add", data=data, headers=HX)
    assert response.status_code == 422
    assert "author_id: no Author with id 999." in response.text


# --- list ------------------------------------------------------------------


async def test_list_shows_labels_and_filters_by_link(client):
    await seed_authors()
    await sql(
        "INSERT INTO blog_post (title, author_id, editor_id) VALUES ('a', 1, 2), ('b', 2, NULL)"
    )
    html = (await client.get("/admin/blog/post")).text
    assert '<a href="/admin/blog/post?author_id=1">ana</a>' in html
    assert '<a href="/admin/blog/post?editor_id=2">bob</a>' in html

    filtered = (await client.get("/admin/blog/post", params={"author_id": "2"})).text
    assert ">b<" in filtered and ">a<" not in filtered
    assert re.search(r'class="on"[^>]*>bob</a>', filtered)


async def test_list_queries_do_not_grow_with_rows(client):
    await seed_authors()

    async def selects_for(count):
        await sql("DELETE FROM blog_post")
        await sql(
            "INSERT INTO blog_post (title, author_id, editor_id) "
            f"SELECT 'p' || n, 1, 2 FROM generate_series(1, {count}) AS n"
        )
        with count_selects() as selects:
            assert (await client.get("/admin/blog/post")).status_code == 200
        return len(selects)

    assert await selects_for(3) == await selects_for(40)


# --- delete ----------------------------------------------------------------


async def test_delete_restricted_row_shows_error(client):
    await seed_authors()
    await sql("INSERT INTO blog_review (author_id) VALUES (1)")
    response = await client.post("/admin/blog/author/1/delete", headers=HX)
    assert response.status_code == 409
    assert "still linked from Review.author" in response.text
    response = await client.post(
        "/admin/blog/author/bulk-delete", data={"ids": ["1", "2"], "confirm": "yes"}, headers=HX
    )
    assert response.status_code == 409
    assert await sql("SELECT count(*) FROM blog_author") == [(2,)]


# --- ManyToMany (phase 16) -------------------------------------------------


async def test_many_to_many_is_a_multi_select_that_saves_and_clears(client):
    await seed_authors()
    await sql("INSERT INTO blog_tag (name) VALUES ('news'), ('tech'), ('misc')")
    html = (await client.get("/admin/blog/post/add")).text
    assert re.search(r'<label for="f-tag_ids">Tags</label>', html)
    assert re.search(r'<select[^>]*name="tag_ids"[^>]*multiple', html)
    assert [v for v, _, _ in options(html, "tag_ids")] == ["1", "2", "3"]

    data = {"title": "t", "author_id": "1", "editor_id": "", "tag_id": "", "tag_ids": ["3", "1"]}
    response = await client.post("/admin/blog/post/add", data=data, headers=HX)
    assert response.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT tag_id FROM blog_post_tags ORDER BY tag_id") == [(1,), (3,)]

    edit = (await client.get("/admin/blog/post/1")).text
    assert [(v, sel) for v, _, sel in options(edit, "tag_ids")] == [
        ("1", True),
        ("2", False),
        ("3", True),
    ]
    data["tag_ids"] = []  # nothing selected: the browser sends no tag_ids at all
    response = await client.post(
        "/admin/blog/post/1", data={k: v for k, v in data.items() if v != []}, headers=HX
    )
    assert response.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT count(*) FROM blog_post_tags") == [(0,)]


async def test_many_to_many_missing_row_shows_field_error(client):
    await seed_authors()
    data = {"title": "t", "author_id": "1", "tag_ids": ["999"]}
    response = await client.post("/admin/blog/post/add", data=data, headers=HX)
    assert response.status_code == 422
    assert "tag_ids: no Tag with id 999." in response.text


async def test_many_to_many_with_many_targets_searches_and_keeps_selection(client):
    await seed_authors()
    await sql(
        "INSERT INTO blog_tag (name) SELECT 'tag ' || n FROM generate_series(1, 101) AS n",
        "INSERT INTO blog_post (title, author_id) VALUES ('t', 1)",
        "INSERT INTO blog_post_tags (post_id, tag_id) VALUES (1, 7)",
    )
    html = (await client.get("/admin/blog/post/1")).text
    assert 'hx-get="/admin/blog/post/link-options/tag_ids"' in html
    assert 'hx-include="#f-tag_ids"' in html
    assert options(html, "tag_ids") == [("7", "Tag #7", True)]

    found = await client.get(
        "/admin/blog/post/link-options/tag_ids", params={"_search": "tag 10", "tag_ids": "7"}
    )
    picked = re.findall(r'<option value="([^"]*)"\s*(selected)?', found.text)
    assert picked == [("7", "selected"), ("10", ""), ("100", ""), ("101", "")]
