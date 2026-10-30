"""Phase 17: lookups (`__gt`, `__in`, ...) in the manager and on the auto API list endpoint."""

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
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
        mp.setattr(apps, "registry", Registry())
        apps.registry.populate(["blog"])

        class Author(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=100)
            secret: str = Field(default="", write_only=True)

        class Tag(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=50)

        class Item(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=100)
            views: int = 0
            price: Decimal | None = None
            day: date | None = None
            author: Author | None = ForeignKey(on_delete="set_null", default=None)
            tags: list[Tag] = ManyToMany()

        schema = MigratedSchema(tmp_path_factory.mktemp("schema"), DATABASE_URL)
        await schema.create()
        yield SimpleNamespace(Author=Author, Tag=Tag, Item=Item, app=create_app())
        await schema.drop()
        await db_engine.dispose_engine()
        Model.registry.dispose()
        Model.metadata.clear()
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "fixture_settings"):
                del sys.modules[name]


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def data(m):
    """ana: Apple (10 views, 1.50, news), Banana (20, NULL price, news+tech); bob: Cherry (30)."""
    async with fyro.db.get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE blog_item_tags, blog_item, blog_author, blog_tag RESTART IDENTITY")
        )
    async with fyro.db.session():
        ana = await m.Author.objects.create(name="ana", secret="s1")
        bob = await m.Author.objects.create(name="bob", secret="s2")
        news = await m.Tag.objects.create(name="news")
        tech = await m.Tag.objects.create(name="tech")
        await m.Item.objects.create(
            name="Apple",
            views=10,
            price=Decimal("1.50"),
            day=date(2026, 1, 1),
            author=ana,
            tags=[news],
        )
        await m.Item.objects.create(name="Banana", views=20, author=ana, tags=[news, tech])
        await m.Item.objects.create(name="Cherry", views=30, price=Decimal(3), author=bob)


async def names(qs):
    return [i.name for i in await qs.order_by("name").all()]


# --- manager ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("lookups", "expected"),
    [
        ({"views__gt": 10}, ["Banana", "Cherry"]),
        ({"views__gte": 20}, ["Banana", "Cherry"]),
        ({"views__lt": 20}, ["Apple"]),
        ({"views__lte": 20}, ["Apple", "Banana"]),
        ({"price__gte": Decimal(2)}, ["Cherry"]),
        ({"day__lt": date(2026, 6, 1)}, ["Apple"]),
        ({"name__in": ["Apple", "Cherry", "Kiwi"]}, ["Apple", "Cherry"]),
        ({"name__in": []}, []),
        ({"price__isnull": True}, ["Banana"]),
        ({"price__isnull": False}, ["Apple", "Cherry"]),
        ({"name__icontains": "AN"}, ["Banana"]),
        ({"name__icontains": "%"}, []),  # % is literal
        ({"name__exact": "Apple"}, ["Apple"]),
        ({"views__gt": 10, "views__lt": 30}, ["Banana"]),
    ],
)
async def test_lookups(m, lookups, expected):
    async with fyro.db.session():
        assert await names(m.Item.objects.filter(**lookups)) == expected


async def test_exclude_with_lookup_keeps_null_rows(m):
    # like Django: excluding price >= 2 keeps the row whose price is unknown
    async with fyro.db.session():
        assert await names(m.Item.objects.exclude(price__gte=2)) == ["Apple", "Banana"]


async def test_one_hop_across_foreign_key(m):
    async with fyro.db.session():
        ana = await m.Author.objects.get(name="ana")
        assert await names(m.Item.objects.filter(author__name="bob")) == ["Cherry"]
        assert await names(m.Item.objects.filter(author__name__icontains="A")) == [
            "Apple",
            "Banana",
        ]
        assert await names(m.Item.objects.exclude(author__name="ana")) == ["Cherry"]
        assert await names(m.Item.objects.filter(author__in=[ana])) == ["Apple", "Banana"]
        assert await names(m.Item.objects.filter(author__isnull=False)) == [
            "Apple",
            "Banana",
            "Cherry",
        ]


async def test_many_to_many_in(m):
    async with fyro.db.session():
        tech = await m.Tag.objects.get(name="tech")
        assert await names(m.Item.objects.filter(tags__in=[tech, 999])) == ["Banana"]
        assert await names(m.Item.objects.exclude(tags__in=[1])) == ["Cherry"]


@pytest.mark.parametrize(
    ("lookups", "message"),
    [
        ({"views__near": 1}, r"Item\.views__near: unknown lookup 'near'\. Lookups: .*icontains"),
        ({"nope__gt": 1}, r"Item\.nope: no such field"),
        ({"author__nope": 1}, r"Author\.nope: no such field"),
        ({"author__name__x__y": 1}, r"Item\.author__name__x__y: .*one ForeignKey"),
        ({"views__icontains": "1"}, r"Item\.views__icontains: icontains needs a text field"),
        ({"tags__gt": 1}, r"Item\.tags__gt: a ManyToMany takes `tags=` or `tags__in=`"),
    ],
)
async def test_lookup_errors(m, lookups, message):
    with pytest.raises(TypeError, match=message):
        m.Item.objects.filter(**lookups)


# --- API -------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="module")
async def client(m):
    transport = ASGITransport(app=m.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def api_names(client, params):
    response = await client.get("/api/blog/item", params=params)
    assert response.status_code == 200, response.text
    return sorted(i["name"] for i in response.json()["items"])


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"views__gte": "20"}, ["Banana", "Cherry"]),
        ({"views__lt": "20", "name__icontains": "pp"}, ["Apple"]),
        ({"price__isnull": "true"}, ["Banana"]),
        ({"day__gte": "2026-01-01"}, ["Apple"]),
        ({"id__in": ["1", "3"]}, ["Apple", "Cherry"]),
        ({"tags": "2"}, ["Banana"]),
        ({"tags__in": ["1", "2"]}, ["Apple", "Banana"]),
        ({"author_id__in": ["2"]}, ["Cherry"]),
    ],
)
async def test_api_lookups(client, params, expected):
    assert await api_names(client, params) == expected


async def test_api_bad_lookup_value_is_422(client):
    assert (await client.get("/api/blog/item", params={"views__gt": "lots"})).status_code == 422


async def test_api_has_no_lookups_across_foreign_keys_or_on_write_only_fields(client):
    spec = (await client.get("/openapi.json")).json()
    params = {p["name"] for p in spec["paths"]["/api/blog/item"]["get"]["parameters"]}
    assert {"views__gt", "views__lte", "name__icontains", "price__isnull", "id__in"} <= params
    assert {"tags", "tags__in"} <= params
    assert not any(p.startswith("author__") for p in params)
    assert "views__icontains" not in params and "name__gt" not in params
    author_params = {p["name"] for p in spec["paths"]["/api/blog/author"]["get"]["parameters"]}
    assert not any(p.startswith("secret") for p in author_params)
