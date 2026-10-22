"""The admin against real Postgres: access, listing, search/sort/filter, writes, users, safety."""

import enum
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import fyro.db
from fyro import Field, Model, apps, create_app
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


class Status(enum.Enum):
    DRAFT = "draft"
    LIVE = "live"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app():
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

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)
            slug: str = Field(max_length=50, unique=True)
            secret: str = Field(default="", write_only=True)
            published: bool = False
            status: Status = Status.DRAFT

        class Note(Model):
            __module__ = "blog.models"
            text: str

            class Meta:
                admin = False

        async with fyro.db.get_engine().begin() as conn:
            await conn.run_sync(Model.metadata.drop_all)
            await conn.run_sync(Model.metadata.create_all)
        yield create_app()
        async with fyro.db.get_engine().begin() as conn:
            await conn.run_sync(Model.metadata.drop_all)
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
            text("TRUNCATE auth_user, auth_session, blog_post, blog_note RESTART IDENTITY")
        )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


async def make_user(username, *, superuser=False):
    from fyro.auth import User
    from fyro.auth.passwords import hash_password

    async with fyro.db.session():
        return await User.objects.create(
            username=username, password=hash_password(PASSWORD), is_superuser=superuser
        )


async def login(client, username="root", *, superuser=True):
    await make_user(username, superuser=superuser)
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200


async def sql(statement):
    async with fyro.db.get_engine().begin() as conn:
        result = await conn.execute(text(statement))
        return result.all() if result.returns_rows else None


async def seed(count, **extra):
    async with fyro.db.get_engine().begin() as conn:
        for n in range(1, count + 1):
            values = {"title": f"Post {n:02d}", "slug": f"p{n}", "published": n % 2 == 0, **extra}
            await conn.execute(
                text(
                    "INSERT INTO blog_post (title, slug, secret, published, status) "
                    "VALUES (:title, :slug, 's', :published, 'draft')"
                ),
                values,
            )


class Page(HTMLParser):
    """Visible text plus table rows from an admin page."""

    def __init__(self, html):
        super().__init__()
        self.text, self.rows, self._row, self._in_cell = [], [], None, False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._in_cell = True
            self._row.append("")

    def handle_endtag(self, tag):
        if tag == "tr" and self._row:
            self.rows.append([cell.strip() for cell in self._row])
            self._row = None
        elif tag == "td":
            self._in_cell = False

    def handle_data(self, data):
        self.text.append(data)
        if self._in_cell and self._row:
            self._row[-1] += data


def titles(html):
    """Post titles in the results table, in display order."""
    return [cell for row in Page(html).rows for cell in row if cell.startswith("Post ")]


# --- access ----------------------------------------------------------------


async def test_anonymous_is_redirected_to_login(client):
    response = await client.get("/admin/blog/post")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?next=" + quote("/admin/blog/post", safe="")


async def test_non_superuser_gets_403_page(client):
    await login(client, "alice", superuser=False)
    response = await client.get("/admin")
    assert response.status_code == 403
    assert "permission" in response.text.lower()


async def test_index_lists_admin_models_only(client):
    await login(client)
    html = (await client.get("/admin")).text
    assert 'href="/admin/blog/post"' in html and 'href="/admin/auth/user"' in html
    assert "/admin/blog/note" not in html  # Meta.admin = False
    assert "/admin/auth/session" not in html


async def test_security_headers_on_admin_pages(client):
    await login(client)
    for path in ("/admin", "/admin/blog/post", "/admin/login"):
        headers = (await client.get(path)).headers
        assert headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "same-origin"


async def test_htmx_is_vendored_and_served(client):
    response = await client.get("/admin/static/htmx.min.js")
    assert response.status_code == 200 and response.text.startswith("var htmx=")


async def test_jetbrains_mono_is_vendored_not_loaded_from_a_cdn(client):
    css = (await client.get("/admin/static/admin.css")).text
    assert 'url("/admin/static/jetbrains-mono.woff2")' in css
    assert "googleapis" not in css and "http" not in css.split("*/", 1)[1]
    font = await client.get("/admin/static/jetbrains-mono.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"


# --- login / logout --------------------------------------------------------


async def test_login_form_then_redirect_to_next(client):
    await make_user("root", superuser=True)
    bad = await client.post(
        "/admin/login", data={"username": "root", "password": "nope", "next": "/admin/blog/post"}
    )
    assert bad.status_code == 401 and "Invalid username or password." in bad.text
    good = await client.post(
        "/admin/login", data={"username": "root", "password": PASSWORD, "next": "/admin/blog/post"}
    )
    assert good.headers["hx-redirect"] == "/admin/blog/post"
    assert (await client.get("/admin/blog/post")).status_code == 200


@pytest.mark.parametrize("next_url", ["//evil.example", "https://evil.example/admin", "/api/x"])
async def test_login_next_cannot_leave_the_admin(client, next_url):
    # bug: open redirect after login
    await make_user("root", superuser=True)
    response = await client.post(
        "/admin/login", data={"username": "root", "password": PASSWORD, "next": next_url}
    )
    assert response.headers["hx-redirect"] == "/admin"


async def test_logout(client):
    await login(client)
    response = await client.post("/admin/logout", headers=HX)
    assert response.headers["hx-redirect"] == "/admin/login"
    assert (await client.get("/admin")).status_code == 303


# --- listing ---------------------------------------------------------------


async def test_list_shows_rows_but_never_write_only_fields(client):
    await login(client)
    await seed(3)
    html = (await client.get("/admin/blog/post")).text
    assert titles(html) == ["Post 01", "Post 02", "Post 03"]
    assert "secret" not in html.lower()


async def test_search_sort_filter_and_paginate(client):
    await login(client)
    await seed(60)
    assert titles((await client.get("/admin/blog/post?search=post 0")).text) == [
        f"Post 0{n}" for n in range(1, 10)
    ]
    assert titles((await client.get("/admin/blog/post?order_by=-title")).text)[0] == "Post 60"
    published = titles((await client.get("/admin/blog/post?published=true")).text)
    assert published[:2] == ["Post 02", "Post 04"] and len(published) == 30
    assert len(titles((await client.get("/admin/blog/post?page=2")).text)) == 10


async def test_search_treats_percent_literally(client):
    await login(client)
    await seed(2)
    await sql("UPDATE blog_post SET title = 'Post 100% done' WHERE id = 1")
    assert titles((await client.get("/admin/blog/post?search=%25")).text) == ["Post 100% done"]


async def test_stored_html_is_escaped(client):
    await login(client)
    await seed(1)
    await sql("UPDATE blog_post SET title = '<script>alert(1)</script>'")
    html = (await client.get("/admin/blog/post")).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --- writes ----------------------------------------------------------------


async def test_add_creates_row(client):
    await login(client)
    response = await client.post(
        "/admin/blog/post/add",
        data={"title": "New", "slug": "new", "secret": "s", "published": "on", "status": "live"},
        headers=HX,
    )
    assert response.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT title, published, status FROM blog_post") == [("New", True, "live")]


async def test_add_validation_error_rerenders_form(client):
    await login(client)
    response = await client.post(
        "/admin/blog/post/add", data={"title": "x" * 201, "slug": "s"}, headers=HX
    )
    assert response.status_code == 422
    assert "at most 200 characters" in response.text
    assert await sql("SELECT count(*) FROM blog_post") == [(0,)]


async def test_add_unique_conflict_shown_on_field(client):
    await login(client)
    await seed(1)
    response = await client.post(
        "/admin/blog/post/add", data={"title": "Dup", "slug": "p1"}, headers=HX
    )
    assert response.status_code == 409
    assert "Post with this slug already exists." in response.text
    assert await sql("SELECT count(*) FROM blog_post") == [(1,)]


async def test_edit_updates_and_blank_write_only_keeps_value(client):
    await login(client)
    await seed(1)
    response = await client.post(
        "/admin/blog/post/1",
        data={"title": "Edited", "slug": "p1", "secret": "", "status": "draft"},
        headers=HX,
    )
    assert response.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT title, secret, published FROM blog_post") == [("Edited", "s", False)]


async def test_delete_after_confirm(client):
    await login(client)
    await seed(2)
    confirm = await client.get("/admin/blog/post/1/delete")
    assert confirm.status_code == 200 and "Post 01" in confirm.text
    response = await client.post("/admin/blog/post/1/delete", headers=HX)
    assert response.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT id FROM blog_post") == [(2,)]


async def test_bulk_delete_confirms_then_deletes_only_selected(client):
    await login(client)
    await seed(4)
    ask = await client.post(
        "/admin/blog/post/bulk-delete", data={"ids": ["1", "3"]}, headers=HX
    )
    assert ask.status_code == 200 and "2 posts" in ask.text.lower()
    assert await sql("SELECT count(*) FROM blog_post") == [(4,)]
    done = await client.post(
        "/admin/blog/post/bulk-delete", data={"ids": ["1", "3"], "confirm": "yes"}, headers=HX
    )
    assert done.headers["hx-redirect"] == "/admin/blog/post"
    assert await sql("SELECT id FROM blog_post ORDER BY id") == [(2,), (4,)]


async def test_writes_without_htmx_header_are_csrf_rejected(client):
    await login(client)
    await seed(1)
    for path, data in (
        ("/admin/blog/post/add", {"title": "x", "slug": "x"}),
        ("/admin/blog/post/1/delete", {}),
        ("/admin/blog/post/bulk-delete", {"ids": ["1"], "confirm": "yes"}),
    ):
        assert (await client.post(path, data=data)).status_code == 403
    assert await sql("SELECT count(*) FROM blog_post") == [(1,)]


async def test_unknown_model_and_row_are_404(client):
    await login(client)
    assert (await client.get("/admin/blog/nope")).status_code == 404
    assert (await client.get("/admin/blog/note")).status_code == 404  # Meta.admin = False
    assert (await client.get("/admin/blog/post/999")).status_code == 404
    assert (await client.get("/admin/blog/post/abc")).status_code == 404


# --- users -----------------------------------------------------------------


async def test_create_user_hashes_password(client):
    await login(client)
    response = await client.post(
        "/admin/auth/user/add",
        data={"username": "bob", "password": "pw-123456", "password2": "pw-123456"},
        headers=HX,
    )
    assert response.headers["hx-redirect"] == "/admin/auth/user"
    ((password, superuser),) = await sql(
        "SELECT password, is_superuser FROM auth_user WHERE username = 'bob'"
    )
    assert password.startswith("$argon2id$") and superuser is False


async def test_create_user_password_mismatch(client):
    await login(client)
    response = await client.post(
        "/admin/auth/user/add",
        data={"username": "bob", "password": "a", "password2": "b"},
        headers=HX,
    )
    assert response.status_code == 422 and "Passwords do not match." in response.text


async def test_user_pages_never_show_the_hash(client):
    await login(client)
    for path in ("/admin/auth/user", "/admin/auth/user/1"):
        assert "$argon2" not in (await client.get(path)).text


@pytest.mark.parametrize(
    "change",
    [{"new_password": "fresh-pass-1"}, {"is_active": None}],
    ids=["password-change", "deactivate"],
)
async def test_password_change_or_deactivate_ends_that_users_sessions(client, app, change):
    await login(client)
    bob = await make_user("bob")
    other = AsyncClient(transport=ASGITransport(app=app), base_url="https://test")
    await other.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    data = {"username": "bob", "email": "", "is_active": "on"}
    data |= {k: v for k, v in change.items() if v is not None}
    if "is_active" in change:
        del data["is_active"]
    response = await client.post(f"/admin/auth/user/{bob.id}", data=data, headers=HX)
    assert response.headers["hx-redirect"] == "/admin/auth/user"
    assert await sql(f"SELECT count(*) FROM auth_session WHERE user_id = {bob.id}") == [(0,)]
    assert (await other.get("/api/auth/me")).status_code == 401
    await other.aclose()


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("post", "/admin/auth/user/1", {"username": "root", "is_superuser": "on"}),  # deactivate self
        ("post", "/admin/auth/user/1", {"username": "root", "is_active": "on"}),  # un-superuser self
        ("post", "/admin/auth/user/1/delete", {}),
        ("post", "/admin/auth/user/bulk-delete", {"ids": ["1"], "confirm": "yes"}),
    ],
    ids=["deactivate-self", "unsuperuser-self", "delete-self", "bulk-delete-self"],
)
async def test_superuser_cannot_lock_themselves_out(client, method, path, data):
    await login(client)  # root is user 1
    response = await client.request(method.upper(), path, data=data, headers=HX)
    assert response.status_code == 422
    assert "yourself" in response.text
    assert await sql("SELECT is_active, is_superuser FROM auth_user WHERE id = 1") == [(True, True)]


# --- the public API gains search and order_by --------------------------------


async def test_api_search_and_order_by(client):
    await seed(12)
    body = (await client.get("/api/blog/post?search=post 1&order_by=-title")).json()
    assert [item["title"] for item in body["items"]] == ["Post 12", "Post 11", "Post 10"]
    assert body["total"] == 3


async def test_api_unknown_order_by_is_422(client):
    response = await client.get("/api/blog/post?order_by=secret")
    assert response.status_code == 422
    assert re.search(r"order_by.*secret", response.text)
