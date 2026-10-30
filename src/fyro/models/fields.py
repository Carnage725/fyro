from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass(frozen=True)
class FieldInfo:
    default: Any = MISSING
    default_factory: Callable[[], Any] | None = None
    primary_key: bool = False
    max_length: int | None = None
    unique: bool = False
    index: bool = False
    read_only: bool = False
    write_only: bool = False
    # Pydantic-only constraints passed through to the generated schemas.
    constraints: dict[str, Any] = field(default_factory=dict)


def Field(
    default: Any = MISSING,
    *,
    default_factory: Callable[[], Any] | None = None,
    primary_key: bool = False,
    max_length: int | None = None,
    unique: bool = False,
    index: bool = False,
    read_only: bool = False,
    write_only: bool = False,
    ge: Any = None,
    le: Any = None,
    min_length: int | None = None,
    pattern: str | None = None,
    description: str | None = None,
) -> Any:
    """Declare a model field: `title: str = Field(max_length=200)`."""
    constraints = {
        "ge": ge,
        "le": le,
        "min_length": min_length,
        "pattern": pattern,
        "description": description,
    }
    return FieldInfo(
        default=default,
        default_factory=default_factory,
        primary_key=primary_key,
        max_length=max_length,
        unique=unique,
        index=index,
        read_only=read_only,
        write_only=write_only,
        constraints={k: v for k, v in constraints.items() if v is not None},
    )


@dataclass(frozen=True)
class ForeignKeyInfo(FieldInfo):
    on_delete: str | None = None


def ForeignKey(
    *,
    on_delete: str | None = None,
    default: Any = MISSING,
    read_only: bool = False,
    write_only: bool = False,
    description: str | None = None,
) -> Any:
    """Link to another model: `author: User = ForeignKey(on_delete="cascade")` adds `author_id`."""
    return ForeignKeyInfo(
        default=default,
        index=True,  # Postgres does not index foreign keys by itself
        read_only=read_only,
        write_only=write_only,
        constraints={"description": description} if description is not None else {},
        on_delete=on_delete,
    )
