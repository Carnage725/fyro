from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, false, func, insert, or_, select, update
from sqlalchemy.orm import joinedload, selectinload

from fyro.db import engine


@dataclass(frozen=True)
class QuerySet:
    """An immutable, lazy query. Chain methods return a new QuerySet; async ones hit the DB."""

    model: Any
    where: tuple[Any, ...] = ()
    order: tuple[Any, ...] = ()
    limit_: int | None = None
    offset_: int | None = None
    related: tuple[str, ...] = ()

    def _column(self, name: str) -> Any:
        fields = self.model.__fyro_fields__
        if name not in fields:
            raise TypeError(
                f"{self.model.__name__}.{name}: no such field. Fields: {', '.join(fields)}."
            )
        return getattr(self.model, name)

    def _target_id(self, name: str, value: Any) -> Any:
        """An instance of the link's target becomes its primary key; an id passes through."""
        if not hasattr(value, "__table__"):
            return value
        target = self.model.__mapper__.relationships[name].mapper.class_
        if not isinstance(value, target):
            raise TypeError(
                f"{self.model.__name__}.{name}: expected {target.__name__}, "
                f"got {type(value).__name__}."
            )
        return getattr(value, next(iter(target.__table__.primary_key.columns)).key)

    def _resolve(self, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        """`author=user` becomes `author_id=user.id`. ManyToMany values (`tags=[...]` or
        `tag_ids=[...]`) are split off as {"tags": [ids]}."""
        links, many = self.model.__fyro_links__, self.model.__fyro_many__
        by_ids = {ids: name for name, ids in many.items()}
        plain: dict[str, Any] = {}
        linked: dict[str, list[Any]] = {}
        for name, value in values.items():
            name = by_ids.get(name, name)
            if name in many:
                linked[name] = list(dict.fromkeys(self._target_id(name, v) for v in value))
            elif name in links:
                plain[links[name]] = self._target_id(name, value)
            else:
                plain[name] = value
        return plain, linked

    def _has(self, name: str, value: Any) -> Any:
        """Rows linked to `value` through the ManyToMany `name` (an EXISTS subquery)."""
        relation = getattr(self.model, name)
        target_pk = next(iter(relation.property.mapper.class_.__table__.primary_key.columns))
        return relation.any(target_pk == self._target_id(name, value))

    def _lookups(self, lookups: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        many = {n: lookups.pop(n) for n in list(lookups) if n in self.model.__fyro_many__}
        plain, ids = self._resolve(lookups)
        if ids:
            name = next(iter(ids))
            raise TypeError(
                f"{self.model.__name__}.{self.model.__fyro_many__[name]}: filter a ManyToMany "
                f"by one row with `{name}=`."
            )
        return plain, many

    def filter(self, **lookups: Any) -> "QuerySet":
        plain, many = self._lookups(lookups)
        conditions = tuple(
            self._column(name).is_(None) if value is None else self._column(name) == value
            for name, value in plain.items()
        ) + tuple(self._has(name, value) for name, value in many.items())
        return replace(self, where=self.where + conditions)

    def exclude(self, **lookups: Any) -> "QuerySet":
        if not lookups:
            return self
        plain, many = self._lookups(lookups)
        # IS DISTINCT FROM keeps NULL rows, like Django's exclude().
        condition = or_(
            *(self._column(name).is_distinct_from(value) for name, value in plain.items()),
            *(~self._has(name, value) for name, value in many.items()),
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

    def select_related(self, *names: str) -> "QuerySet":
        """Load these ForeignKeys in the same query (a join), so reading them costs nothing."""
        links = self.model.__fyro_links__
        for name in names:
            if name not in links:
                raise TypeError(
                    f"{self.model.__name__}.{name}: not a ForeignKey. "
                    f"ForeignKeys: {', '.join(links) or 'none'}."
                )
        return replace(self, related=self.related + names)

    def prefetch_related(self, *names: str) -> "QuerySet":
        """Load these ManyToManys with one extra query for all rows."""
        many = self.model.__fyro_many__
        for name in names:
            if name not in many:
                raise TypeError(
                    f"{self.model.__name__}.{name}: not a ManyToMany. "
                    f"ManyToMany fields: {', '.join(many) or 'none'}."
                )
        return replace(self, related=self.related + names)

    def limit(self, n: int) -> "QuerySet":
        return replace(self, limit_=n)

    def offset(self, n: int) -> "QuerySet":
        return replace(self, offset_=n)

    def _select(self) -> Any:
        stmt = select(self.model).where(*self.where).order_by(*self.order)
        if self.related:
            many = self.model.__fyro_many__
            stmt = stmt.options(
                *(
                    (selectinload if n in many else joinedload)(getattr(self.model, n))
                    for n in self.related
                )
            )
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
        # Without ORDER BY or joins: neither changes the count, but Postgres would do the work.
        stmt = select(func.count()).select_from(
            replace(self, order=(), related=())._select().subquery()
        )
        return (await engine.default_engine.fetch(stmt))[0]

    def _pk(self) -> Any:
        return next(iter(self.model.__table__.primary_key.columns))

    async def _link(self, name: str, rows: list[Any], targets: list[Any], *, new: bool) -> None:
        """Set the ManyToMany `name` of `rows` to exactly `targets` (join table, Core only)."""
        table = self.model.__mapper__.relationships[name].secondary
        source, target = table.columns
        if not new:
            await engine.default_engine.execute(delete(table).where(source.in_(rows)))
        if rows and targets:
            pairs = [{source.key: row, target.key: t} for row in rows for t in targets]
            await engine.default_engine.execute(insert(table).values(pairs))

    async def create(self, **values: Any) -> Any:
        values, linked = self._resolve(values)
        for name in values:
            self._column(name)
        stmt = insert(self.model).values(**values).returning(self.model)
        row = (await engine.default_engine.fetch(stmt))[0]
        for name, targets in linked.items():
            await self._link(name, [getattr(row, self._pk().key)], targets, new=True)
        return row

    async def update(self, **values: Any) -> int:
        """Update matching rows; a ManyToMany value replaces each row's whole set."""
        self._check_bulk("update")
        values, linked = self._resolve(values)
        for name in values:
            self._column(name)
        # Pick the rows before the UPDATE, which may change what the filter matches.
        rows = (
            await engine.default_engine.fetch(select(self._pk()).where(*self.where))
            if linked
            else []
        )
        count = len(rows)
        if values:
            count = await engine.default_engine.execute(
                update(self.model).where(*self.where).values(**values)
            )
        for name, targets in linked.items():
            await self._link(name, rows, targets, new=False)
        return count

    async def delete(self) -> int:
        self._check_bulk("delete")
        return await engine.default_engine.execute(delete(self.model).where(*self.where))


class Manager:
    """`Model.objects`: a fresh QuerySet on the class, an error on instances."""

    def __get__(self, instance: Any, owner: type) -> QuerySet:
        if instance is not None:
            raise AttributeError(f"Use {owner.__name__}.objects, not an instance.")
        return QuerySet(owner)
