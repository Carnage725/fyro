import os
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from fyro import Field, Model, apps
from fyro.apps import Registry

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURES))
    fresh = Registry()
    monkeypatch.setattr(apps, "registry", fresh)
    fresh.populate(["blog"])
    yield fresh
    Model.registry.dispose()
    Model.metadata.clear()
    for name in list(sys.modules):
        if name.split(".")[0] == "blog":
            del sys.modules[name]


async def test_create_all_creates_table():
    class Post(Model):
        __module__ = "blog.models"
        title: str = Field(max_length=200)
        body: str
        secret: str = Field(write_only=True)
        published: bool = False

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Model.metadata.drop_all)
            await conn.run_sync(Model.metadata.create_all)
            cols = await conn.run_sync(
                lambda sync_conn: sa.inspect(sync_conn).get_columns("blog_post")
            )
            await conn.run_sync(Model.metadata.drop_all)
    finally:
        await engine.dispose()

    found = {c["name"]: (str(c["type"]), c["nullable"]) for c in cols}
    assert found == {
        "id": ("BIGINT", False),
        "title": ("VARCHAR(200)", False),
        "body": ("TEXT", False),
        "secret": ("TEXT", False),
        "published": ("BOOLEAN", False),
    }
