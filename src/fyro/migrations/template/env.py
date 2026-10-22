"""fyro's Alembic environment. It ships inside fyro; projects never see or edit it."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from fyro import Model, apps
from fyro.conf import get_settings

if context.is_offline_mode():
    raise RuntimeError("fyro runs migrations against the database; offline SQL is not supported.")

settings = get_settings()
apps.registry.populate(settings.installed_apps)


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=Model.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


asyncio.run(main())
