import enum
import inspect
import re
import types
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Union, get_args, get_origin

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fyro import apps, db
from fyro.conf import ImproperlyConfigured
from fyro.models.fields import MISSING, FieldInfo
from fyro.models.manager import Manager, QuerySet
from fyro.models.schemas import build_schemas

COLUMN_TYPES: dict[Any, Any] = {
    int: sa.Integer,
    bool: sa.Boolean,
    float: sa.Float,
    Decimal: sa.Numeric,
    datetime: lambda: sa.DateTime(timezone=True),
    date: sa.Date,
    uuid.UUID: sa.Uuid,
}
SUPPORTED = "int, str, bool, float, Decimal, datetime, date, UUID, Enum, or any of these | None"
EXCLUDE_KEYS = ("create_exclude", "read_exclude", "update_exclude")
SCHEMA_KEYS = ("create_schema", "read_schema", "update_schema")
META_KEYS = ("table_name", "api", "permissions", *EXCLUDE_KEYS, *SCHEMA_KEYS)
PERMISSION_ACTIONS = ("read", "write")
PERMISSION_LEVELS = ("public", "authenticated", "superuser")
# Predictable constraint names: a unique violation maps back to its field (409s), and
# Alembic can diff and drop constraints by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    """`T | None` -> (T, True); anything else -> (tp, False)."""
    if get_origin(tp) in (Union, types.UnionType):
        args = get_args(tp)
        if len(args) == 2 and type(None) in args:
            return next(a for a in args if a is not type(None)), True
    return tp, False


def _column_type(where: str, tp: Any, info: FieldInfo) -> Any:
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        # VARCHAR holding the member value, not a Postgres ENUM type: adding members needs
        # no migration. No CHECK constraint; Pydantic validates.
        return sa.Enum(
            tp,
            native_enum=False,
            values_callable=lambda cls: [str(member.value) for member in cls],
            length=255,
        )
    if tp is str:
        return sa.String(info.max_length) if info.max_length else sa.Text()
    try:
        factory = COLUMN_TYPES.get(tp)
    except TypeError:  # unhashable annotation
        factory = None
    if factory is None:
        raise TypeError(f"{where}: type {tp!r} is not supported. Supported: {SUPPORTED}.")
    return factory()


def _read_meta(cls: type) -> dict[str, Any]:
    meta_cls = cls.__dict__.get("Meta")
    if meta_cls is None:
        return {}
    meta = {k: v for k, v in vars(meta_cls).items() if not k.startswith("__")}
    unknown = sorted(set(meta) - set(META_KEYS))
    if unknown:
        raise TypeError(
            f"{cls.__name__}.Meta: unknown option(s) {unknown}. Allowed: {', '.join(META_KEYS)}."
        )
    for key in SCHEMA_KEYS:
        schema = meta.get(key)
        if schema is not None and not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise TypeError(
                f"{cls.__name__}.Meta.{key} must be a pydantic BaseModel subclass, got {schema!r}."
            )
    permissions = meta.get("permissions", {})
    where = f"{cls.__name__}.Meta.permissions"
    if not isinstance(permissions, dict):
        raise TypeError(f"{where} must be a dict like {{'write': 'superuser'}}, got {permissions!r}.")
    for action, level in permissions.items():
        if action not in PERMISSION_ACTIONS:
            raise TypeError(f"{where}: unknown action {action!r}. Use {', '.join(PERMISSION_ACTIONS)}.")
        if level not in PERMISSION_LEVELS:
            raise TypeError(f"{where}: unknown level {level!r}. Use {', '.join(PERMISSION_LEVELS)}.")
    if not isinstance(meta.get("api", True), bool):
        raise TypeError(f"{cls.__name__}.Meta.api must be True or False, got {meta['api']!r}.")
    return meta


class Model(DeclarativeBase):
    """Base class for fyro models. One class gives a table plus Create/Read/Update schemas."""

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

    Create: ClassVar[type[BaseModel]]
    Read: ClassVar[type[BaseModel]]
    Update: ClassVar[type[BaseModel]]
    DoesNotExist: ClassVar[type[db.DoesNotExist]]
    MultipleObjectsReturned: ClassVar[type[db.MultipleObjectsReturned]]
    __fyro_fields__: ClassVar[dict[str, FieldInfo]]
    __fyro_api__: ClassVar[bool]
    __fyro_permissions__: ClassVar[dict[str, str] | None]
    objects: ClassVar[QuerySet] = Manager()  # type: ignore[assignment]

    def __init_subclass__(cls, **kw: Any) -> None:
        name = cls.__name__
        if not cls.__module__.endswith(".models"):
            raise ImproperlyConfigured(
                f"Model {name} is defined in {cls.__module__!r}. "
                "Models must be defined in <app>/models.py."
            )
        label = cls.__module__.rsplit(".", 2)[-2]
        meta = _read_meta(cls)

        fields: dict[str, FieldInfo] = {}
        hints: dict[str, Any] = {}
        columns: dict[str, Any] = {}
        for field_name, tp in inspect.get_annotations(cls, eval_str=True).items():
            if get_origin(tp) is ClassVar:
                continue
            where = f"{name}.{field_name}"
            value = cls.__dict__.get(field_name, MISSING)
            info = value if isinstance(value, FieldInfo) else FieldInfo(default=value)
            if info.read_only and info.write_only:
                raise TypeError(
                    f"{where}: read_only and write_only cannot both be set "
                    "(the field would be in no schema). Remove one."
                )
            base_tp, nullable = _unwrap_optional(tp)
            default = info.default_factory or (None if info.default is MISSING else info.default)
            columns[field_name] = mapped_column(
                _column_type(where, base_tp, info),
                primary_key=info.primary_key,
                nullable=nullable,
                unique=info.unique,
                index=info.index,
                default=default,
            )
            fields[field_name] = info
            hints[field_name] = tp

        if not any(f.primary_key for f in fields.values()):
            if "id" in fields:
                raise TypeError(
                    f"{name}.id: 'id' is reserved for the auto primary key. "
                    "Remove it, or declare it with Field(primary_key=True)."
                )
            fields = {"id": FieldInfo(primary_key=True, read_only=True), **fields}
            hints = {"id": int, **hints}
            columns = {
                "id": mapped_column(sa.BigInteger, primary_key=True, autoincrement=True),
                **columns,
            }

        for key in EXCLUDE_KEYS:
            missing = sorted(set(meta.get(key, ())) - set(fields))
            if missing:
                raise TypeError(
                    f"{name}.Meta.{key}: no field named {missing}. Fields: {', '.join(fields)}."
                )

        # Rewrite plain annotations into Mapped[T] + mapped_column() before SQLAlchemy
        # maps the class. Delete first so columns follow declaration order, id first.
        for field_name in columns:
            if field_name in cls.__dict__:
                delattr(cls, field_name)
        for field_name, column in columns.items():
            setattr(cls, field_name, column)
        cls.__annotations__ = {n: Mapped[tp] for n, tp in hints.items()}
        cls.__tablename__ = meta.get("table_name") or f"{label}_{_snake(name)}"

        super().__init_subclass__(**kw)

        cls.__fyro_fields__ = fields
        cls.__fyro_api__ = meta.get("api", True)
        cls.__fyro_permissions__ = meta.get("permissions")
        cls.Create, cls.Read, cls.Update = build_schemas(name, hints, fields, meta)
        for error in (db.DoesNotExist, db.MultipleObjectsReturned):
            namespace = {"__module__": cls.__module__, "__qualname__": f"{name}.{error.__name__}"}
            setattr(cls, error.__name__, type(error.__name__, (error,), namespace))
        apps.registry.register_model(label, cls)
