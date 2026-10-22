from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fyro.conf import get_settings
from fyro.db.session import current_session


class Engine(Protocol):
    """The seam between the query API and the database: Python now, Rust later."""

    async def fetch(self, stmt: Any) -> list[Any]:
        """Run a SELECT or INSERT ... RETURNING and return its rows."""
        ...

    async def execute(self, stmt: Any) -> int:
        """Run an UPDATE or DELETE and return the number of rows affected."""
        ...


class SQLAlchemyEngine:
    async def fetch(self, stmt: Any) -> list[Any]:
        return list((await current_session().scalars(stmt)).all())

    async def execute(self, stmt: Any) -> int:
        result = await current_session().execute(stmt)
        return result.rowcount


default_engine: Engine = SQLAlchemyEngine()

_async_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """The process-wide AsyncEngine, created from settings on first use."""
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(get_settings().database_url)
    return _async_engine


async def dispose_engine() -> None:
    global _async_engine
    if _async_engine is not None:
        engine, _async_engine = _async_engine, None
        await engine.dispose()
