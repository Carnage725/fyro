import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Delete, Insert, Select, Update, text
from support import MigratedSchema

import fyro.db
from fyro import Field, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import get_settings
from fyro.db import engine as db_engine
from fyro.db.session import SessionMiddleware

pytestmark = pytest.mark.asyncio(loop_scope="module")

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def Post(tmp_path_factory):
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

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)
            body: str
            secret: str = Field(write_only=True)
            published: bool = False
            subtitle: str | None = None

        schema = MigratedSchema(tmp_path_factory.mktemp("schema"), DATABASE_URL)
        await schema.create()
        yield Post
        await schema.drop()
        await db_engine.dispose_engine()
        Model.registry.dispose()
        Model.metadata.clear()
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "fixture_settings"):
                del sys.modules[name]


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def clean_table(Post):
    async with fyro.db.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE blog_post RESTART IDENTITY"))


async def make(Post, *titles, **extra):
    return [await Post.objects.create(title=t, body="b", secret="s", **extra) for t in titles]


async def titles(qs):
    return [p.title for p in await qs.all()]


# --- manager ---------------------------------------------------------------


async def test_create_returns_instance_with_defaults(Post):
    async with fyro.db.session():
        post = await Post.objects.create(title="Hi", body="b", secret="s")
    assert isinstance(post, Post)
    assert post.id == 1
    assert post.published is False
    assert post.subtitle is None


async def test_chain_filter_exclude_order_limit_offset(Post):
    async with fyro.db.session():
        await make(Post, "a", "b", "c", "d")
        await Post.objects.filter(title="b").update(published=True)
        assert await titles(Post.objects.filter(published=False).order_by("title")) == [
            "a",
            "c",
            "d",
        ]
        assert await titles(Post.objects.exclude(title="a").order_by("-id")) == ["d", "c", "b"]
        assert await titles(Post.objects.order_by("id").limit(2).offset(1)) == ["b", "c"]


async def test_querysets_are_immutable(Post):
    async with fyro.db.session():
        await make(Post, "a", "b")
        qs = Post.objects.order_by("id")
        filtered = qs.filter(title="a")
        assert filtered is not qs
        assert await titles(qs) == ["a", "b"]
        assert await titles(filtered) == ["a"]


async def test_first(Post):
    async with fyro.db.session():
        assert await Post.objects.first() is None
        await make(Post, "a", "b")
        assert (await Post.objects.first()).title == "a"
        assert (await Post.objects.order_by("-id").first()).title == "b"


async def test_get(Post):
    async with fyro.db.session():
        await make(Post, "a", "dup", "dup")
        assert (await Post.objects.get(id=1)).title == "a"
        with pytest.raises(Post.DoesNotExist) as exc:
            await Post.objects.get(title="missing")
        assert isinstance(exc.value, fyro.db.DoesNotExist)
        with pytest.raises(Post.MultipleObjectsReturned) as exc:
            await Post.objects.get(title="dup")
        assert isinstance(exc.value, fyro.db.MultipleObjectsReturned)


async def test_count(Post):
    async with fyro.db.session():
        await make(Post, "a", "b", "c")
        assert await Post.objects.count() == 3
        assert await Post.objects.filter(title="a").count() == 1
        assert await Post.objects.order_by("id").limit(2).count() == 2


async def test_count_does_not_sort(Post, monkeypatch):
    # perf bug: the count subquery kept ORDER BY, so Postgres sorted every row only to count them
    sql = []
    real = db_engine.default_engine.fetch

    async def fetch(stmt):
        sql.append(str(stmt))
        return await real(stmt)

    monkeypatch.setattr(db_engine.default_engine, "fetch", fetch)
    async with fyro.db.session():
        await make(Post, "b", "a", "c")
        assert await Post.objects.order_by("-title").count() == 3
        assert await Post.objects.order_by("title").offset(1).count() == 2
    counts = [statement for statement in sql if "count(" in statement]
    assert len(counts) == 2 and not any("ORDER BY" in statement for statement in counts)


async def test_update_and_delete_return_rowcounts(Post):
    async with fyro.db.session():
        await make(Post, "a", "b", "c")
        assert await Post.objects.filter(title="a").update(title="z") == 1
        assert await Post.objects.exclude(title="z").delete() == 2
    async with fyro.db.session():
        assert await titles(Post.objects) == ["z"]


async def test_none_filters_use_is_null(Post):
    async with fyro.db.session():
        await make(Post, "plain")
        await make(Post, "sub", subtitle="s")
        assert await titles(Post.objects.filter(subtitle=None)) == ["plain"]
        assert await titles(Post.objects.exclude(subtitle=None)) == ["sub"]
        # exclude keeps NULL rows (Django semantics), unlike SQL `subtitle != 's'`
        assert await titles(Post.objects.exclude(subtitle="s")) == ["plain"]


@pytest.mark.parametrize(
    "call",
    [
        lambda P: P.objects.filter(nope=1),
        lambda P: P.objects.exclude(nope=1),
        lambda P: P.objects.order_by("-nope"),
        lambda P: P.objects.create(nope=1),
        lambda P: P.objects.update(nope=1),
    ],
)
async def test_unknown_field_is_type_error(Post, call):
    with pytest.raises(TypeError, match=r"Post\.nope"):
        result = call(Post)
        if hasattr(result, "__await__"):
            await result


async def test_bulk_update_after_limit_is_error(Post):
    with pytest.raises(TypeError, match="limit"):
        await Post.objects.limit(1).update(title="x")


async def test_objects_not_on_instances(Post):
    with pytest.raises(AttributeError, match="Post.objects"):
        Post(title="t").objects  # noqa: B018


async def test_no_session_is_runtime_error(Post):
    with pytest.raises(RuntimeError, match=r"fyro\.db\.session\(\)"):
        await Post.objects.count()


# --- session() -------------------------------------------------------------


async def test_session_commits_on_clean_exit(Post):
    async with fyro.db.session():
        await make(Post, "kept")
    async with fyro.db.session():
        assert await titles(Post.objects) == ["kept"]


async def test_session_rolls_back_on_exception(Post):
    with pytest.raises(ValueError):
        async with fyro.db.session():
            await make(Post, "lost")
            raise ValueError
    async with fyro.db.session():
        assert await Post.objects.count() == 0


async def test_nested_session_reuses_outer(Post):
    with pytest.raises(ValueError):
        async with fyro.db.session():
            async with fyro.db.session():
                await make(Post, "inner")
            raise ValueError
    async with fyro.db.session():
        assert await Post.objects.count() == 0


# --- requests --------------------------------------------------------------


def session_app(Post) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware)

    @app.post("/ok")
    async def ok():
        post = await Post.objects.create(title="ok", body="b", secret="s")
        return {"id": post.id}

    @app.post("/boom")
    async def boom():
        await Post.objects.create(title="boom", body="b", secret="s")
        raise RuntimeError("boom")

    @app.post("/missing")
    async def missing():
        await Post.objects.create(title="missing", body="b", secret="s")
        raise HTTPException(404)

    return app


async def test_request_commits(Post):
    transport = ASGITransport(app=session_app(Post))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ok")
    assert response.status_code == 200
    async with fyro.db.session():
        assert await titles(Post.objects) == ["ok"]


@pytest.mark.parametrize(("path", "status"), [("/boom", 500), ("/missing", 404)])
async def test_request_rolls_back_on_error(Post, path, status):
    transport = ASGITransport(app=session_app(Post), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path)
    assert response.status_code == status
    async with fyro.db.session():
        assert await Post.objects.count() == 0


async def test_create_app_has_session_middleware_and_lifespan(Post):
    app = create_app()
    assert any(m.cls is SessionMiddleware for m in app.user_middleware)
    async with app.router.lifespan_context(app):
        engine = fyro.db.get_engine()
    assert fyro.db.get_engine() is not engine  # disposed on shutdown, recreated lazily


# --- engine seam -----------------------------------------------------------


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def fetch(self, stmt):
        self.calls.append(("fetch", type(stmt)))
        return [0]

    async def execute(self, stmt):
        self.calls.append(("execute", type(stmt)))
        return 0


async def test_manager_only_uses_engine_seam(Post, monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(db_engine, "default_engine", fake)
    await Post.objects.filter(title="x").all()
    await Post.objects.count()
    await Post.objects.create(title="x", body="b", secret="s")
    await Post.objects.filter(title="x").update(title="y")
    await Post.objects.filter(title="x").delete()
    assert fake.calls == [
        ("fetch", Select),
        ("fetch", Select),
        ("fetch", Insert),
        ("execute", Update),
        ("execute", Delete),
    ]
