import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import APIRouter
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
        registry = Registry()
        mp.setattr(apps, "registry", registry)
        registry.populate(["blog"])

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)
            slug: str = Field(max_length=50, unique=True)
            body: str
            secret: str = Field(write_only=True)
            published: bool = False
            subtitle: str | None = None

        class Tag(Model):
            __module__ = "blog.models"
            name: str

            class Meta:
                api = False

        custom = APIRouter()

        @custom.get("/post/stats")
        async def post_stats():
            return {"posts": await Post.objects.count()}

        @custom.get("/missing")
        async def missing():
            return await Post.objects.get(id=999)

        registry.apps["blog"].routes_module = SimpleNamespace(router=custom)

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
            if name.split(".")[0] in ("blog", "fixture_settings"):
                del sys.modules[name]


@pytest_asyncio.fixture(loop_scope="module")
async def client(app):
    async with fyro.db.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE blog_post RESTART IDENTITY"))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def post(n: int, **extra) -> dict:
    return {"title": f"Post {n}", "slug": f"post-{n}", "body": "b", "secret": "s", **extra}


async def seed(client, count: int, **extra) -> None:
    for n in range(1, count + 1):
        assert (await client.post("/api/blog/post", json=post(n, **extra))).status_code == 201


READ = {"id", "title", "slug", "body", "published", "subtitle"}


# --- create / get ----------------------------------------------------------


async def test_create_returns_201_read_without_write_only(client):
    response = await client.post("/api/blog/post", json=post(1))
    assert response.status_code == 201
    body = response.json()
    assert set(body) == READ
    assert body["id"] == 1 and body["published"] is False


async def test_create_validates_with_create_schema(client):
    response = await client.post("/api/blog/post", json=post(1, title="x" * 201))
    assert response.status_code == 422


async def test_get_and_404(client):
    await seed(client, 1)
    response = await client.get("/api/blog/post/1")
    assert response.status_code == 200
    assert response.json()["slug"] == "post-1"
    assert "secret" not in response.json()
    missing = await client.get("/api/blog/post/99")
    assert missing.status_code == 404
    assert "Post" in missing.json()["detail"]


# --- list ------------------------------------------------------------------


async def test_list_page_shape_and_defaults(client):
    await seed(client, 3)
    body = (await client.get("/api/blog/post")).json()
    assert body["total"] == 3 and body["limit"] == 50 and body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [1, 2, 3]
    assert all(set(item) == READ for item in body["items"])


async def test_list_limit_offset_and_total(client):
    await seed(client, 5)
    body = (await client.get("/api/blog/post?limit=2&offset=1")).json()
    assert [item["id"] for item in body["items"]] == [2, 3]
    assert body["total"] == 5 and body["limit"] == 2 and body["offset"] == 1


@pytest.mark.parametrize("query", ["limit=101", "limit=0", "offset=-1"])
async def test_list_rejects_bad_pagination(client, query):
    assert (await client.get(f"/api/blog/post?{query}")).status_code == 422


async def test_list_filters(client):
    await seed(client, 3)
    await client.patch("/api/blog/post/2", json={"published": True})
    body = (await client.get("/api/blog/post?published=true")).json()
    assert [item["id"] for item in body["items"]] == [2]
    assert body["total"] == 1
    body = (await client.get("/api/blog/post?slug=post-3")).json()
    assert [item["id"] for item in body["items"]] == [3]


async def test_write_only_field_is_not_a_filter(client, app):
    await seed(client, 2)
    body = (await client.get("/api/blog/post?secret=nope")).json()
    assert body["total"] == 2
    params = app.openapi()["paths"]["/api/blog/post"]["get"]["parameters"]
    names = {p["name"] for p in params}
    assert "secret" not in names
    assert {"title", "slug", "published", "limit", "offset"} <= names


# --- patch -----------------------------------------------------------------


async def test_patch_partial(client):
    await seed(client, 1)
    response = await client.patch("/api/blog/post/1", json={"title": "New"})
    assert response.status_code == 200
    assert response.json()["title"] == "New"
    assert response.json()["slug"] == "post-1"
    assert (await client.get("/api/blog/post/1")).json()["title"] == "New"


async def test_patch_empty_body_returns_row_unchanged(client):
    await seed(client, 1)
    response = await client.patch("/api/blog/post/1", json={})
    assert response.status_code == 200
    assert response.json()["title"] == "Post 1"


async def test_patch_null_on_not_null_is_422(client):
    await seed(client, 1)
    assert (await client.patch("/api/blog/post/1", json={"title": None})).status_code == 422
    nullable = await client.patch("/api/blog/post/1", json={"subtitle": None})
    assert nullable.status_code == 200


async def test_patch_404(client):
    assert (await client.patch("/api/blog/post/9", json={"title": "x"})).status_code == 404
    assert (await client.patch("/api/blog/post/9", json={})).status_code == 404


# --- delete ----------------------------------------------------------------


async def test_delete_204_then_404(client):
    await seed(client, 1)
    response = await client.delete("/api/blog/post/1")
    assert response.status_code == 204
    assert response.content == b""
    assert (await client.get("/api/blog/post/1")).status_code == 404
    assert (await client.delete("/api/blog/post/1")).status_code == 404


# --- unique violations -----------------------------------------------------


async def test_create_duplicate_unique_is_409(client):
    await seed(client, 1)
    response = await client.post("/api/blog/post", json=post(2, slug="post-1"))
    assert response.status_code == 409
    assert response.json() == {"detail": "Post with this slug already exists."}
    assert (await client.get("/api/blog/post")).json()["total"] == 1


async def test_patch_duplicate_unique_is_409_and_row_unchanged(client):
    await seed(client, 2)
    response = await client.patch("/api/blog/post/2", json={"slug": "post-1", "title": "T"})
    assert response.status_code == 409
    assert response.json() == {"detail": "Post with this slug already exists."}
    row = (await client.get("/api/blog/post/2")).json()
    assert row["slug"] == "post-2" and row["title"] == "Post 2"


# --- routing ---------------------------------------------------------------


async def test_meta_api_false_has_no_routes(client, app):
    assert (await client.get("/api/blog/tag")).status_code == 404
    assert not any(path.startswith("/api/blog/tag") for path in app.openapi()["paths"])


async def test_custom_route_is_mounted_before_crud(client):
    await seed(client, 2)
    response = await client.get("/api/blog/post/stats")
    assert response.status_code == 200
    assert response.json() == {"posts": 2}


async def test_does_not_exist_in_hand_written_route_is_404(client):
    response = await client.get("/api/blog/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "No Post matches {'id': 999}."}


async def test_openapi_lists_crud_routes_and_schemas(app):
    spec = app.openapi()
    paths = spec["paths"]
    assert set(paths["/api/blog/post"]) == {"get", "post"}
    assert set(paths["/api/blog/post/{id}"]) == {"get", "patch", "delete"}
    assert {"PostCreate", "PostRead", "PostUpdate", "PostPage"} <= set(spec["components"]["schemas"])
    assert paths["/api/blog/post"]["get"]["tags"] == ["blog.post"]
