import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from support import MigratedSchema

import fyro.db
from fyro import Field, ForeignKey, Model, apps
from fyro.apps import Registry
from fyro.conf import get_settings
from fyro.db import engine as db_engine

pytestmark = pytest.mark.asyncio(loop_scope="module")

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def m(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.startswith("FYRO_"):
                mp.delenv(name)
        mp.setenv("FYRO_SETTINGS", "fixture_settings")
        mp.setenv("FYRO_DATABASE_URL", DATABASE_URL)
        mp.syspath_prepend(str(FIXTURES))
        get_settings.cache_clear()
        registry = Registry()
        mp.setattr(apps, "registry", registry)
        registry.populate(["blog"])

        class Author(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=100)

        class Tag(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=50)

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)
            author: Author = ForeignKey(on_delete="cascade")
            editor: Author | None = ForeignKey(on_delete="set_null", default=None)

        schema = MigratedSchema(tmp_path_factory.mktemp("schema"), DATABASE_URL)
        await schema.create()
        yield SimpleNamespace(Author=Author, Tag=Tag, Post=Post)
        await schema.drop()
        await db_engine.dispose_engine()
        Model.registry.dispose()
        Model.metadata.clear()
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "fixture_settings"):
                del sys.modules[name]


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def clean_tables(m):
    async with fyro.db.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE blog_post, blog_author, blog_tag RESTART IDENTITY"))


@contextmanager
def count_queries():
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = fyro.db.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


# --- filter / exclude ------------------------------------------------------


async def test_filter_by_id_field_and_by_instance(m):
    async with fyro.db.session():
        ana = await m.Author.objects.create(name="ana")
        bob = await m.Author.objects.create(name="bob")
        await m.Post.objects.create(title="a1", author_id=ana.id)
        await m.Post.objects.create(title="b1", author=bob, editor=ana)

        assert [p.title for p in await m.Post.objects.filter(author_id=ana.id).all()] == ["a1"]
        assert [p.title for p in await m.Post.objects.filter(author=bob).all()] == ["b1"]
        assert [p.title for p in await m.Post.objects.exclude(author=bob).all()] == ["a1"]
        assert [p.title for p in await m.Post.objects.filter(editor=None).all()] == ["a1"]
        assert [p.title for p in await m.Post.objects.filter(editor=ana).all()] == ["b1"]


async def test_filter_with_wrong_model_is_error(m):
    async with fyro.db.session():
        tag = await m.Tag.objects.create(name="t")
        with pytest.raises(TypeError, match=r"Post\.author: expected Author, got Tag"):
            m.Post.objects.filter(author=tag)


# --- create / update -------------------------------------------------------


async def test_create_and_update_with_instance(m):
    async with fyro.db.session():
        ana = await m.Author.objects.create(name="ana")
        bob = await m.Author.objects.create(name="bob")
        post = await m.Post.objects.create(title="t", author=ana)
        assert post.author_id == ana.id
        assert await m.Post.objects.filter(id=post.id).update(author=bob, editor=ana) == 1
        post = await m.Post.objects.get(id=post.id)
        assert (post.author_id, post.editor_id) == (bob.id, ana.id)


# --- select_related --------------------------------------------------------


async def test_select_related_loads_links(m):
    async with fyro.db.session():
        ana = await m.Author.objects.create(name="ana")
        await m.Post.objects.create(title="t", author=ana)
        post = await m.Post.objects.select_related("author", "editor").get(title="t")
    assert post.author.name == "ana"
    assert post.editor is None


async def test_select_related_chains_and_counts(m):
    async with fyro.db.session():
        ana = await m.Author.objects.create(name="ana")
        for title in "cba":
            await m.Post.objects.create(title=title, author=ana)
        qs = m.Post.objects.select_related("author").filter(author=ana).order_by("title")
        rows = await qs.limit(2).all()
        assert [(p.title, p.author.name) for p in rows] == [("a", "ana"), ("b", "ana")]
        assert await qs.count() == 3
        assert (await qs.first()).title == "a"


async def test_select_related_unknown_name_is_error(m):
    with pytest.raises(
        TypeError, match=r"Post\.title: not a ForeignKey\. ForeignKeys: author, editor"
    ):
        m.Post.objects.select_related("title")


async def test_list_with_select_related_is_one_query(m):
    async with fyro.db.session():
        authors = [await m.Author.objects.create(name=f"a{i}") for i in range(5)]
        for i in range(50):
            await m.Post.objects.create(title=f"p{i}", author=authors[i % 5], editor=authors[0])
        with count_queries() as statements:
            rows = await m.Post.objects.select_related("author", "editor").all()
            names = {(p.author.name, p.editor.name) for p in rows}
    assert len(rows) == 50
    assert len(names) == 5
    assert len(statements) == 1


# --- not loaded ------------------------------------------------------------


async def test_unloaded_link_is_clear_error(m):
    async with fyro.db.session():
        ana = await m.Author.objects.create(name="ana")
        post = await m.Post.objects.create(title="t", author=ana)
        post = await m.Post.objects.get(id=post.id)
    with pytest.raises(
        fyro.db.NotLoaded,
        match=r"Post\.author is not loaded\. Use Post\.objects\.select_related\('author'\)",
    ):
        _ = post.author
    assert post.author_id == ana.id
