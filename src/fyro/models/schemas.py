from typing import Any

import pydantic
from pydantic import BaseModel, ConfigDict, create_model

from fyro.models.fields import MISSING, FieldInfo


def _input_field(info: FieldInfo, **overrides: Any) -> Any:
    kwargs = dict(info.constraints)
    if info.max_length is not None:
        kwargs["max_length"] = info.max_length
    if info.default_factory is not None:
        kwargs["default_factory"] = info.default_factory
    elif info.default is not MISSING:
        kwargs["default"] = info.default
    kwargs.update(overrides)
    return pydantic.Field(**kwargs)


def build_schemas(
    model_name: str,
    types: dict[str, Any],
    fields: dict[str, FieldInfo],
    meta: dict[str, Any],
) -> tuple[type[BaseModel], type[BaseModel], type[BaseModel]]:
    """Generate the Create, Read and Update schemas for one model."""
    create_names = [
        n for n, f in fields.items() if not f.read_only and n not in meta.get("create_exclude", ())
    ]
    read_names = [
        n for n, f in fields.items() if not f.write_only and n not in meta.get("read_exclude", ())
    ]
    update_names = [n for n in create_names if n not in meta.get("update_exclude", ())]

    create = meta.get("create_schema") or create_model(
        f"{model_name}Create",
        **{n: (types[n], _input_field(fields[n])) for n in create_names},
    )
    read = meta.get("read_schema") or create_model(
        f"{model_name}Read",
        __config__=ConfigDict(from_attributes=True),
        **{
            n: (types[n], pydantic.Field(description=fields[n].constraints.get("description")))
            for n in read_names
        },
    )
    # Annotation stays T (not T | None): omitting a field is fine, explicit null on a
    # NOT NULL column is rejected. Callers use model_dump(exclude_unset=True).
    update = meta.get("update_schema") or create_model(
        f"{model_name}Update",
        **{
            n: (types[n], _input_field(fields[n], default=None, default_factory=None))
            for n in update_names
        },
    )
    return create, read, update
