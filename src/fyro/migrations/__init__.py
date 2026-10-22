"""`makemigrations` and `migrate`: Alembic with fyro's conventions.

No alembic.ini and no env.py in the project: both live in fyro. The project's
`migrations/` folder holds only revision files.
"""

import asyncio
import enum
import importlib
import logging
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.operations import ops
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Column
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from fyro import apps
from fyro.conf import SETTINGS_ENV_VAR, ImproperlyConfigured, get_settings
from fyro.models.fields import MISSING

__all__ = ["makemigrations", "migrate"]

TEMPLATE_DIR = Path(__file__).parent / "template"
# Date prefix so files sort by creation time; Alembic's hex ids stay the revision ids.
FILE_TEMPLATE = "%(year)d_%(month).2d_%(day).2d_%(hour).2d%(minute).2d-%(rev)s_%(slug)s"
# Computed defaults that Postgres can evaluate per row, to fill rows that existed before the
# column did. Any other default_factory is refused: give a constant or make the field optional.
SQL_FOR_FACTORY = {uuid.uuid4: "gen_random_uuid()", datetime.now: "now()", date.today: "CURRENT_DATE"}

logger = logging.getLogger(__name__)


def _ensure_sync() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "fyro.migrations functions are sync. Call them from sync code, "
        "or use `await asyncio.to_thread(...)` inside async code."
    )


def _migrations_dir() -> Path:
    """<project root>/migrations: the directory holding the settings module's top package."""
    get_settings()  # fails first, with a clear message, if FYRO_SETTINGS is wrong
    module_name = os.environ[SETTINGS_ENV_VAR]
    settings_file = Path(importlib.import_module(module_name).__file__)
    return settings_file.parents[module_name.count(".")] / "migrations"


def _config(directory: Path) -> Config:
    def escape(value: Any) -> str:  # Config values go through configparser interpolation
        return str(value).replace("%", "%%")

    cfg = Config()
    cfg.set_main_option("script_location", escape(TEMPLATE_DIR))
    cfg.set_main_option("version_locations", escape(directory))
    cfg.set_main_option("path_separator", "newline")  # a single path; ':' in it stays safe
    cfg.set_main_option("file_template", escape(FILE_TEMPLATE))
    return cfg


def _script(cfg: Config) -> ScriptDirectory:
    script = ScriptDirectory.from_config(cfg)
    heads = sorted(script.get_heads())
    if len(heads) > 1:
        raise ImproperlyConfigured(
            f"The migration history has {len(heads)} heads ({', '.join(heads)}): two "
            "migrations were made from the same parent, usually on different branches. "
            "Set one file's down_revision to the other's revision id."
        )
    return script


def _current_revision() -> str | None:
    async def read() -> str | None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: MigrationContext.configure(sync).get_current_revision()
                )
        finally:
            await engine.dispose()

    return asyncio.run(read())


def _warn_renames(upgrade_ops: ops.UpgradeOps) -> None:
    for table_ops in upgrade_ops.ops:
        if not isinstance(table_ops, ops.ModifyTableOps):
            continue
        dropped = [op.column_name for op in table_ops.ops if isinstance(op, ops.DropColumnOp)]
        added = [op.column.name for op in table_ops.ops if isinstance(op, ops.AddColumnOp)]
        if dropped and added:
            logger.warning(
                "%s: drops %s and adds %s. Alembic cannot detect renames: if this is a "
                "rename, the dropped column's data is lost.",
                table_ops.table_name,
                ", ".join(dropped),
                ", ".join(added),
            )


def _fill_existing_rows(upgrade_ops: ops.UpgradeOps) -> None:
    """Rewrite ops that would fail on a table with rows, so those rows get a value.

    Postgres refuses ADD COLUMN ... NOT NULL, and SET NOT NULL over NULLs, without a value,
    and fyro's defaults live in Python only. A constant is used once as a temporary database
    default (Django's approach) or an UPDATE; a known factory fills rows with its SQL
    equivalent. Anything else is refused, so a migration never fails on existing data.
    """
    models = {
        model.__tablename__: model
        for app in apps.registry.apps.values()
        for model in app.models.values()
    }
    for table_ops in upgrade_ops.ops:
        if not isinstance(table_ops, ops.ModifyTableOps):
            continue
        model = models[table_ops.table_name]
        rewritten: list[Any] = []
        for op in table_ops.ops:
            column = getattr(op, "column", None)
            if isinstance(op, ops.AddColumnOp) and not column.nullable and column.server_default is None:
                rewritten.extend(_fill_added_column(model, op))
            elif isinstance(op, ops.AlterColumnOp) and op.modify_nullable is False:
                rewritten.extend(_fill_nulls(model, op))
            else:
                rewritten.append(op)
        table_ops.ops = rewritten


def _fill_value(model: Any, name: str) -> tuple[str, Any]:
    """("constant", value) or ("sql", expression) for filling existing rows; refuses otherwise."""
    info = model.__fyro_fields__[name]
    where = f"{model.__name__}.{name}"
    if info.default_factory is None and info.default is not MISSING:
        if info.unique:
            raise ImproperlyConfigured(
                f"{where}: a unique column cannot be filled with one constant default for the "
                "rows already there (they would all get the same value). Add it as optional "
                "(T | None = None) first, fill unique values, then make it required."
            )
        value = info.default.value if isinstance(info.default, enum.Enum) else info.default
        return "constant", value
    sql = SQL_FOR_FACTORY.get(info.default_factory)
    if sql is not None and (not info.unique or info.default_factory is uuid.uuid4):
        return "sql", sql
    raise ImproperlyConfigured(
        f"{where}: the rows already in the table need a value for this required column. Give "
        "it a constant default (e.g. = 0), use default_factory=uuid.uuid4, datetime.now or "
        "date.today (only uuid4 for a unique column), or make it optional (T | None = None)."
    )


def _fill_added_column(model: Any, add: ops.AddColumnOp) -> list[Any]:
    table, column = add.table_name, add.column
    kind, value = _fill_value(model, column.name)
    if kind == "constant":
        return [
            ops.AddColumnOp(
                table, Column(column.name, column.type, nullable=False, server_default=str(value))
            ),
            ops.AlterColumnOp(
                table,
                column.name,
                existing_type=column.type,
                existing_nullable=False,
                modify_server_default=None,
            ),
        ]
    quote = postgresql.dialect().identifier_preparer.quote
    return [
        ops.AddColumnOp(table, Column(column.name, column.type, nullable=True)),
        ops.ExecuteSQLOp(f"UPDATE {quote(table)} SET {quote(column.name)} = {value}"),
        ops.AlterColumnOp(table, column.name, existing_type=column.type, modify_nullable=False),
    ]


def _fill_nulls(model: Any, alter: ops.AlterColumnOp) -> list[Any]:
    """`T | None` -> `T`: fill the NULLs first, then SET NOT NULL."""
    table, name = alter.table_name, alter.column_name
    kind, value = _fill_value(model, name)
    if kind == "constant":
        # Rendered by SQLAlchemy so quoting is always right (e.g. a default of "it's").
        value_type = sa.String() if isinstance(alter.existing_type, sa.Enum) else alter.existing_type
        value = str(
            sa.literal(value, type_=value_type).compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
    quote = postgresql.dialect().identifier_preparer.quote
    return [
        ops.ExecuteSQLOp(
            f"UPDATE {quote(table)} SET {quote(name)} = {value} WHERE {quote(name)} IS NULL"
        ),
        alter,
    ]


def makemigrations(message: str | None = None) -> Path | None:
    """Write a revision for model changes. Returns its path, or None when nothing changed."""
    _ensure_sync()
    directory = _migrations_dir()
    directory.mkdir(exist_ok=True)
    cfg = _config(directory)
    head = _script(cfg).get_current_head()
    if _current_revision() != head:
        raise ImproperlyConfigured(
            "The database is not at the latest migration, so changes cannot be detected. "
            "Run migrate() before makemigrations()."
        )

    def skip_empty(context: Any, revision: Any, directives: list[Any]) -> None:
        upgrade_ops = directives[0].upgrade_ops
        if upgrade_ops.is_empty():
            directives[:] = []
        else:
            _warn_renames(upgrade_ops)
            _fill_existing_rows(upgrade_ops)

    result = command.revision(
        cfg,
        message or ("initial" if head is None else "auto"),
        autogenerate=True,
        process_revision_directives=skip_empty,
    )
    if isinstance(result, list):
        result = result[0] if result else None
    return Path(result.path) if result is not None else None


def migrate(target: str = "head") -> None:
    """Move the database up or down to `target`: a revision id, "head" or "zero" (empty)."""
    _ensure_sync()
    directory = _migrations_dir()
    if not directory.is_dir():
        raise ImproperlyConfigured(
            f"No migrations directory at {directory}. Run makemigrations() first."
        )
    cfg = _config(directory)
    script = _script(cfg)
    if target == "zero":
        command.downgrade(cfg, "base")
        return
    if target == "head":
        command.upgrade(cfg, "head")
        return
    try:
        revision = script.get_revision(target)
    except CommandError:
        revision = None
    if revision is None:
        raise ImproperlyConfigured(
            f"Unknown migration {target!r}. Use a revision id from {directory}, "
            "'head' or 'zero'."
        )
    current = _current_revision()
    applied = (
        {r.revision for r in script.iterate_revisions(current, "base")} if current else set()
    )
    if revision.revision in applied:
        command.downgrade(cfg, revision.revision)
    else:
        command.upgrade(cfg, revision.revision)
