"""Property tests (test.md step 5): invariants that must hold for any input Hypothesis can find."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from support import MigratedSchema

from fyro import Field, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import get_settings
from fyro.db import engine as db_engine

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)
DB = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


def sql(statement: str):
    async def go():
        engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement))
                return result.all() if result.returns_rows else None
        finally:
            await engine.dispose()

    return asyncio.run(go())


class SyncClient:
    """Blocking calls for Hypothesis (sync tests) on top of httpx.AsyncClient."""

    def __init__(self, loop, client):
        self.loop, self.client = loop, client

    def request(self, method, url, **kwargs):
        return self.loop.run_until_complete(self.client.request(method, url, **kwargs))

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.startswith("FYRO_"):
                mp.delenv(name)
        mp.setenv("FYRO_SETTINGS", "fixture_settings")
        mp.setenv("FYRO_DATABASE_URL", DATABASE_URL)
        mp.syspath_prepend(str(FIXTURES))
        mp.setattr(apps, "registry", Registry())
        mp.setattr(db_engine, "_async_engine", None)
        get_settings.cache_clear()
        apps.registry.populate(["blog"])

        class Item(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=50)
            body: str
            count: int
            flag: bool
            note: str | None = None
            secret: str = Field(default="s", write_only=True)

        schema = MigratedSchema(tmp_path_factory.mktemp("schema"), DATABASE_URL)
        schema.create_sync()
        # One private event loop for the module: the app's engine is created and disposed on it.
        loop = asyncio.new_event_loop()
        transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
        async_client = AsyncClient(transport=transport, base_url="http://test")
        yield SyncClient(loop, async_client)
        loop.run_until_complete(async_client.aclose())
        loop.run_until_complete(db_engine.dispose_engine())
        loop.close()
        schema.drop_sync()
        Model.registry.dispose()
        Model.metadata.clear()
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "fixture_settings"):
                del sys.modules[name]


def reset():
    sql("TRUNCATE blog_item RESTART IDENTITY")


TEXT = st.text(max_size=60)
INTS = st.integers(min_value=-(2**40), max_value=2**40)
NUL = "a\x00b"  # Postgres rejects NUL in text: must be a 4xx, never a 500
SURROGATE = "\ud800"  # a lone surrogate cannot be stored as UTF-8: must be a 4xx


# --- invariant 1: anything Create accepts can be stored ----------------------


@DB
@given(title=TEXT, body=TEXT, count=INTS, flag=st.booleans(), note=st.none() | TEXT)
@example(title=NUL, body="b", count=0, flag=False, note=None)
@example(title="t", body=SURROGATE, count=0, flag=False, note=None)
@example(title="t", body="b", count=2**31, flag=False, note=None)
@example(title="t", body="b", count=-(2**31) - 1, flag=False, note=None)
@example(title="t", body="b", count=2**31 - 1, flag=True, note=NUL)
def test_create_never_500_and_round_trips(client, title, body, count, flag, note):
    reset()
    payload = {"title": title, "body": body, "count": count, "flag": flag, "note": note}
    # json.dumps escapes lone surrogates as \ud800, exactly what a hostile client would send
    response = client.post(
        "/api/blog/item", content=json.dumps(payload), headers={"content-type": "application/json"}
    )
    assert response.status_code in (201, 422), response.text
    if response.status_code == 201:
        stored = client.get(f"/api/blog/item/{response.json()['id']}").json()
        assert {k: stored[k] for k in payload} == payload


# --- invariant 2: PATCH changes exactly the fields that were sent -------------


FIELD_VALUES = {
    "title": st.text(max_size=50).filter(lambda s: "\x00" not in s),
    "body": st.text(max_size=60).filter(lambda s: "\x00" not in s),
    "count": st.integers(min_value=-(2**31), max_value=2**31 - 1),
    "flag": st.booleans(),
    "note": st.none() | st.text(max_size=20).filter(lambda s: "\x00" not in s),
}


@DB
@given(changes=st.fixed_dictionaries({}, optional=FIELD_VALUES))
def test_patch_changes_only_sent_fields(client, changes):
    reset()
    original = {"title": "t", "body": "b", "count": 1, "flag": False, "note": "n"}
    item_id = client.post("/api/blog/item", json=original).json()["id"]
    response = client.patch(f"/api/blog/item/{item_id}", json=changes)
    assert response.status_code == 200, response.text
    stored = client.get(f"/api/blog/item/{item_id}").json()
    assert {k: stored[k] for k in original} == original | changes


# --- invariant 3: pages cover every row exactly once -------------------------


@DB
@given(rows=st.integers(min_value=0, max_value=25), limit=st.integers(min_value=1, max_value=10))
def test_pagination_covers_every_row_once(client, rows, limit):
    reset()
    if rows:
        sql(
            "INSERT INTO blog_item (title, body, count, flag, secret) "
            f"SELECT 't' || n, 'b', n, false, 's' FROM generate_series(1, {rows}) n"
        )
    seen = []
    for offset in range(0, rows + limit, limit):
        page = client.get(f"/api/blog/item?limit={limit}&offset={offset}").json()
        assert len(page["items"]) <= limit
        assert page["total"] == rows
        seen += [item["id"] for item in page["items"]]
    assert sorted(seen) == list(range(1, rows + 1)) and len(seen) == len(set(seen))


# --- query parameters and path ids never cause a 500 -------------------------


@DB
@given(value=INTS)
@example(value=2**63)
def test_int_filter_and_path_id_never_500(client, value):
    assert client.get(f"/api/blog/item?count={value}").status_code in (200, 422)
    assert client.get(f"/api/blog/item/{value}").status_code in (200, 404, 422)


@DB
@given(term=TEXT)
@example(term=NUL)
@example(term="%_\\")
def test_search_and_text_filter_never_500(client, term):
    assert client.get("/api/blog/item", params={"search": term}).status_code in (200, 422)
    assert client.get("/api/blog/item", params={"title": term}).status_code in (200, 422)
