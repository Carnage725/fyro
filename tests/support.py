"""Shared test helper: build a module's schema with fyro's real migrations (test.md step 6).

Every DB test module creates its tables through makemigrations + migrate instead of
Model.metadata.create_all, so each run also proves the migrations for those model shapes.
"""

import asyncio
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from fyro import Model, migrations
from fyro.conf import get_settings


class MigratedSchema:
    """Migrations for the models registered right now, written to a throwaway directory.

    fyro puts migrations/ beside the settings module, so a one-line settings module (a
    subclass of tests/fixtures/fixture_settings.py) is placed in `root` for the duration.
    """

    def __init__(self, root: Path, database_url: str) -> None:
        self.root = root
        self.database_url = database_url
        self.module = f"schema_settings_{uuid.uuid4().hex}"
        (root / f"{self.module}.py").write_text(
            "from fixture_settings import ProjectSettings as _Base\n\n\n"
            "class ProjectSettings(_Base):\n    pass\n"
        )

    @contextmanager
    def _settings(self):
        previous = os.environ.get("FYRO_SETTINGS")
        sys.path.insert(0, str(self.root))
        os.environ["FYRO_SETTINGS"] = self.module
        get_settings.cache_clear()
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("FYRO_SETTINGS", None)
            else:
                os.environ["FYRO_SETTINGS"] = previous
            sys.path.remove(str(self.root))
            sys.modules.pop(self.module, None)
            get_settings.cache_clear()

    def _clean_slate(self) -> None:
        """Remove leftovers from a crashed earlier run (tables and Alembic's version table)."""

        async def go():
            engine = create_async_engine(self.database_url, poolclass=NullPool)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Model.metadata.drop_all)
                    await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            finally:
                await engine.dispose()

        asyncio.run(go())

    def create_sync(self) -> None:
        self._clean_slate()
        with self._settings():
            assert migrations.makemigrations() is not None, "no migration generated"
            migrations.migrate()
            assert migrations.makemigrations() is None, "migrated schema differs from the models"

    def drop_sync(self) -> None:
        with self._settings():
            migrations.migrate("zero")
        self._clean_slate()

    async def create(self) -> None:
        # migrations.* are sync and refuse to run inside an event loop.
        await asyncio.to_thread(self.create_sync)

    async def drop(self) -> None:
        await asyncio.to_thread(self.drop_sync)
