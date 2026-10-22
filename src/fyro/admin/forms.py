"""Turn model fields into HTML form widgets, and submitted form data back into schema input."""

import enum
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from fyro.models.base import _unwrap_optional
from fyro.models.fields import MISSING


@dataclass
class FormField:
    name: str
    label: str
    widget: str  # text | textarea | checkbox | number | date | datetime-local | select | password
    value: str = ""
    checked: bool = False
    options: list[tuple[str, str]] = field(default_factory=list)
    required: bool = False
    readonly: bool = False
    help: str = ""
    error: str = ""


def label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def widget_for(tp: Any, info: Any) -> str:
    if tp is bool:
        return "checkbox"
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return "select"
    if tp in (int, float, Decimal):
        return "number"
    if tp is datetime:
        return "datetime-local"
    if tp is date:
        return "date"
    if tp is str and info.max_length is None:
        return "textarea"
    return "text"


def as_input(value: Any) -> str:
    """A Python value as the string an <input> holds."""
    if value is None:
        return ""
    if isinstance(value, enum.Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def display(value: Any) -> str:
    """A Python value as list-table text."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    text = as_input(value)
    return text if len(text) <= 80 else text[:79] + "…"


def build_fields(
    model: Any, schema: type[BaseModel], instance: Any = None, *, editing: bool = False
) -> list[FormField]:
    """Editable fields come from the schema; read_only ones are shown, never submitted."""
    fields: list[FormField] = []
    for name, info in model.__fyro_fields__.items():
        if name in schema.model_fields:
            tp, nullable = _unwrap_optional(schema.model_fields[name].annotation)
            widget = widget_for(tp, info)
            current = getattr(instance, name) if instance is not None else None
            if instance is None and info.default is not MISSING:
                current = info.default
            form_field = FormField(
                name=name,
                label=label(name),
                widget=widget,
                value="" if info.write_only else as_input(current),
                checked=bool(current) if widget == "checkbox" else False,
                options=[(str(m.value), m.name.title()) for m in tp] if widget == "select" else [],
                required=not editing and not nullable and widget != "checkbox"
                and info.default is MISSING and info.default_factory is None,
                help="Leave blank to keep the current value." if editing and info.write_only else "",
            )
            fields.append(form_field)
        elif instance is not None and info.read_only and not info.write_only:
            fields.append(
                FormField(
                    name=name, label=label(name), widget="text",
                    value=display(getattr(instance, name)), readonly=True,
                )
            )
    return fields


def parse(
    model: Any, schema: type[BaseModel], form: dict[str, list[str]], *, editing: bool
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate submitted form data with `schema`. Returns (values, errors by field)."""
    raw: dict[str, Any] = {}
    for name, schema_field in schema.model_fields.items():
        info = model.__fyro_fields__[name]
        tp, nullable = _unwrap_optional(schema_field.annotation)
        if tp is bool:
            raw[name] = name in form  # an unchecked checkbox is simply absent
            continue
        value = form.get(name, [""])[0]
        if value == "":
            if editing and info.write_only:
                continue  # blank means "keep the current value"
            if nullable:
                raw[name] = None
            elif tp is str and editing:
                raw[name] = ""
            continue  # create: let the schema apply its default or report "Field required"
        raw[name] = value
    try:
        validated = schema.model_validate(raw)
    except ValidationError as error:
        return {}, {str(e["loc"][0]) if e["loc"] else "__all__": e["msg"] for e in error.errors()}
    values = validated.model_dump(exclude_unset=True)
    for name, value in values.items():
        if isinstance(value, datetime) and value.tzinfo is None:
            values[name] = value.replace(tzinfo=UTC)  # datetime-local has no zone: treat as UTC
    return values, {}
