from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, false, func, insert, or_, select, update

from fyro.db import engine


@dataclass(frozen=True)
class QuerySet:
    """An immutable, lazy query. Chain methods return a new QuerySet; async ones hit the DB."""

    model: Any
    where: tuple[Any, ...] = ()
    order: tuple[Any, ...] = ()
    limit_: int | None = None
    offset_: int | None = None

    def _column(self, name: str) -> Any:
        fields = self.model.__fyro_fields__
        if name not in fields:
            raise TypeError(
                f"{self.model.__name__}.{name}: no such field. Fields: {', '.join(fields)}."
            )
        return getattr(self.model, name)

    def filter(self, **lookups: Any) -> "QuerySet":
        conditions = tuple(
            self._column(name).is_(None) if value is None else self._column(name) == value
            for name, value in lookups.items()
        )
        return replace(self, where=self.where + conditions)

    def exclude(self, **lookups: Any) -> "QuerySet":
        if not lookups:
            return self
        # IS DISTINCT FROM keeps NULL rows, like Django's exclude().
        condition = or_(
            *(self._column(name).is_distinct_from(value) for name, value in lookups.items())
        )
        return replace(self, where=(*self.where, condition))

    def search(self, term: str, fields: Iterable[str]) -> "QuerySet":
        """Rows where any of `fields` contains `term`, case-insensitively (% and _ literal)."""
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions = [self._column(name).ilike(f"%{escaped}%", escape="\\") for name in fields]
        condition = or_(*conditions) if conditions else false()  # no text fields: no match
        return replace(self, where=(*self.where, condition))

    def order_by(self, *names: str) -> "QuerySet":
        order = tuple(
            self._column(name[1:]).desc() if name.startswith("-") else self._column(name)
            for name in names
        )
        return replace(self, order=order)

    def limit(self, n: int) -> "QuerySet":
        return replace(self, limit_=n)

    def offset(self, n: int) -> "QuerySet":
        return replace(self, offset_=n)

    def _select(self) -> Any:
        stmt = select(self.model).where(*self.where).order_by(*self.order)
        if self.limit_ is not None:
            stmt = stmt.limit(self.limit_)
        if self.offset_ is not None:
            stmt = stmt.offset(self.offset_)
        return stmt

    def _check_bulk(self, operation: str) -> None:
        if self.order or self.limit_ is not None or self.offset_ is not None:
            raise TypeError(
                f"{self.model.__name__}.objects.{operation}() cannot follow order_by(), "
                "limit() or offset(). Use filter() to pick the rows."
            )

    async def all(self) -> list[Any]:
        return await engine.default_engine.fetch(self._select())

    async def first(self) -> Any | None:
        qs = self
        if not qs.order:
            qs = qs.order_by(*(col.key for col in self.model.__table__.primary_key))
        rows = await qs.limit(1).all()
        return rows[0] if rows else None

    async def get(self, **lookups: Any) -> Any:
        rows = await self.filter(**lookups).limit(2).all()
        if not rows:
            raise self.model.DoesNotExist(f"No {self.model.__name__} matches {lookups}.")
        if len(rows) > 1:
            raise self.model.MultipleObjectsReturned(
                f"More than one {self.model.__name__} matches {lookups}."
            )
        return rows[0]

    async def count(self) -> int:
        # Without ORDER BY: order never changes the count, but Postgres would still sort.
        stmt = select(func.count()).select_from(replace(self, order=())._select().subquery())
        return (await engine.default_engine.fetch(stmt))[0]

    async def create(self, **values: Any) -> Any:
        for name in values:
            self._column(name)
        stmt = insert(self.model).values(**values).returning(self.model)
        return (await engine.default_engine.fetch(stmt))[0]

    async def update(self, **values: Any) -> int:
        self._check_bulk("update")
        for name in values:
            self._column(name)
        return await engine.default_engine.execute(
            update(self.model).where(*self.where).values(**values)
        )

    async def delete(self) -> int:
        self._check_bulk("delete")
        return await engine.default_engine.execute(delete(self.model).where(*self.where))


class Manager:
    """`Model.objects`: a fresh QuerySet on the class, an error on instances."""

    def __get__(self, instance: Any, owner: type) -> QuerySet:
        if instance is not None:
            raise AttributeError(f"Use {owner.__name__}.objects, not an instance.")
        return QuerySet(owner)
