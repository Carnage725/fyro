import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, get_args, get_origin

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import create_model
from sqlalchemy.exc import IntegrityError

from fyro import apps
from fyro.conf import ImproperlyConfigured
from fyro.models.base import StorableText, _storable_text, _unwrap_optional
from fyro.models.manager import COMPARISONS

ORDERED = (int, float, Decimal, date, datetime)  # types with __gt/__gte/__lt/__lte filters

UNIQUE_VIOLATION = "23505"  # Postgres SQLSTATE
FOREIGN_KEY_VIOLATION = "23503"
LIST_PARAMETERS = ("limit", "offset", "search", "order_by", "with_total", "expand")


def unique_violation(model: Any, error: IntegrityError) -> tuple[list[str], str]:
    """Columns and message for a unique violation; re-raise any other IntegrityError."""
    if getattr(error.orig, "sqlstate", None) != UNIQUE_VIOLATION:
        raise error
    violated = getattr(error.orig.__cause__, "constraint_name", None)
    table = model.__table__
    columns: list[str] = []
    for constraint in [*table.constraints, *table.indexes]:
        if str(constraint.name) == violated:
            columns = [column.key for column in constraint.columns]
    what = f"this {' and '.join(columns)}" if columns else "these values"
    return columns, f"{model.__name__} with {what} already exists."


def _violated(error: IntegrityError) -> str | None:
    return getattr(error.orig.__cause__, "constraint_name", None)


def is_missing_target(error: IntegrityError) -> bool:
    return getattr(error.orig, "sqlstate", None) == FOREIGN_KEY_VIOLATION


def missing_target(model: Any, error: IntegrityError, values: dict[str, Any]) -> tuple[str, str]:
    """Field and message for a ForeignKey pointing at a missing row; re-raise anything else."""
    if is_missing_target(error):
        for constraint in model.__table__.foreign_key_constraints:
            if constraint.name == _violated(error):
                (column,) = constraint.columns
                target = next(
                    rel.mapper.class_
                    for rel in model.__mapper__.relationships
                    if column in rel.local_columns
                )
                return (
                    column.key,
                    f"{column.key}: no {target.__name__} with id {values.get(column.key)!r}.",
                )
        for name, ids in model.__fyro_many__.items():
            relation = model.__mapper__.relationships[name]
            for constraint in relation.secondary.foreign_key_constraints:
                if constraint.name == _violated(error):
                    # Postgres names the id: 'Key (tag_id)=(99) is not present in table ...'
                    detail = getattr(error.orig.__cause__, "detail", None) or ""
                    found = re.search(r"\)=\((.*?)\)", detail)
                    which = found.group(1) if found else values.get(ids)
                    return ids, f"{ids}: no {relation.mapper.class_.__name__} with id {which}."
    raise error


def _conflict(model: Any, error: IntegrityError, values: dict[str, Any]) -> HTTPException:
    """409 for a unique violation, 422 for a link to a missing row, naming the field;
    re-raise anything else."""
    if is_missing_target(error):
        return HTTPException(422, missing_target(model, error, values)[1])
    return HTTPException(409, unique_violation(model, error)[1])


def still_linked(model: Any, error: IntegrityError) -> str:
    """Message for deleting a row that on_delete="restrict" links still point at; re-raise
    anything else."""
    if is_missing_target(error):
        for app in apps.registry.apps.values():
            for source in app.models.values():
                for link, id_field in source.__fyro_links__.items():
                    (key,) = source.__table__.c[id_field].foreign_keys
                    if key.constraint.name == _violated(error):
                        return (
                            f"This {model.__name__} is still linked from {source.__name__}.{link} "
                            '(on_delete="restrict"). Delete or change those rows first.'
                        )
    raise error


def _levels(model: Any, auth_installed: bool) -> dict[str, str]:
    """Defaults with fyro.auth: anyone reads, logged-in users write. Without it, all public."""
    levels = {"read": "public", "write": "authenticated" if auth_installed else "public"}
    return levels | (model.__fyro_permissions__ or {})


def _field_type(info: Any) -> Any:
    """A schema field's type with its constraints (int range, storable text) still attached."""
    return Annotated[info.annotation, *info.metadata] if info.metadata else info.annotation


def _filter_params(model: Any, Read: Any) -> dict[str, Any]:
    """List query parameters: each readable field and its lookups (`views__gt`, `id__in`,
    `title__icontains`, `price__isnull`), plus `tags` / `tags__in` per ManyToMany.

    Only the model's own readable fields: write_only fields are not in Read, so they can't be
    probed, and lookups across a ForeignKey (`author__email`) are Python-only for the same
    reason: they would expose fields of the target, which may be hidden from the API.
    """
    params: dict[str, Any] = {}
    for field, info in Read.model_fields.items():
        if field not in model.__fyro_fields__:
            continue
        tp = _field_type(info)
        base, nullable = _unwrap_optional(info.annotation)
        if get_origin(base) is Annotated:
            base = get_args(base)[0]
        item = StorableText if base is str else base
        params[field] = (tp | None, None)
        if base is not bool:
            params[f"{field}__in"] = (list[item] | None, None)
        if base in ORDERED:
            params |= {f"{field}__{lookup}": (tp | None, None) for lookup in COMPARISONS}
        if base is str:
            params[f"{field}__icontains"] = (StorableText | None, None)
        if nullable:
            params[f"{field}__isnull"] = (bool | None, None)
    for name, ids in model.__fyro_many__.items():
        if ids in Read.model_fields:
            target = model.__mapper__.relationships[name].mapper.class_
            pk_type = next(iter(target.__table__.primary_key.columns)).type.python_type
            params[name] = (pk_type | None, None)
            params[f"{name}__in"] = (list[pk_type] | None, None)
    return params


def readable_fields(model: Any) -> list[str]:
    return [field for field in model.Read.model_fields if field in model.__fyro_fields__]


def list_query(
    model: Any,
    filters: dict[str, Any],
    search: str | None = None,
    order_by: str | None = None,
) -> Any:
    """The rows a list shows (filters, search, order), shared by the API and the admin.

    Raises ValueError for an order_by that is not a readable field.
    """
    pk = next(iter(model.__table__.primary_key.columns)).key
    qs = model.objects.filter(**filters)
    if search:
        try:
            _storable_text(search)
        except ValueError as error:
            raise ValueError(f"search {error}.") from error
        qs = qs.search(search, model.__fyro_search_fields__)
    if not order_by:
        return qs.order_by(pk)
    field = order_by.removeprefix("-")
    if field not in readable_fields(model):
        raise ValueError(
            f"Unknown order_by field {field!r}. Use one of: {', '.join(readable_fields(model))}."
        )
    return qs.order_by(order_by) if field == pk else qs.order_by(order_by, pk)  # stable pages


def _guard(level: str) -> Callable[[Request], Awaitable[None]]:
    """Dependency enforcing a permission level. It runs before the endpoint loads the row, so a
    caller who may not see the model gets 401/403 even for ids that don't exist."""

    async def check(request: Request) -> None:
        if level == "public":
            return
        # Imported here: fyro.auth is only importable when it is installed.
        from fyro.auth.sessions import current_user

        user = await current_user(request)
        if level == "superuser" and not user.is_superuser:
            raise HTTPException(403, "You do not have permission to do this.")

    return check


def build_router(model: Any, auth_installed: bool = False) -> APIRouter:
    """List, get, create, patch and delete routes for one model, mounted by create_app()."""
    name = model.__name__
    primary_key = list(model.__table__.primary_key.columns)
    if len(primary_key) != 1:
        raise ImproperlyConfigured(
            f"{name} has a composite primary key, which auto CRUD does not support. "
            f"Set Meta.api = False on {name}."
        )
    clashes = [f for f in readable_fields(model) if f in LIST_PARAMETERS]
    if clashes:
        raise ImproperlyConfigured(
            f"{name}.{clashes[0]}: {clashes[0]!r} is a query parameter of the auto CRUD list "
            f"endpoint, so it cannot also filter a field. Rename the field, or set "
            f"Meta.api = False on {name}."
        )
    pk = primary_key[0].key
    # Reuse the schema's narrowed type (int range, storable text) so a bad id is a 422.
    pk_info = model.Read.model_fields.get(pk)
    pk_type = _field_type(pk_info) if pk_info else primary_key[0].type.python_type
    PkParam = Annotated[pk_type, Path(alias=pk)]
    Read, Create, Update = model.Read, model.Create, model.Update
    Filters = create_model(f"{name}Filters", **_filter_params(model, Read))
    # ?expand= links: the id field is readable. A target hidden from the API (Meta.api =
    # False, like fyro.auth's User) is never expanded: its Read schema was not made public.
    many = model.__fyro_many__
    # ManyToManys whose ids are readable: loaded for every read, one extra query per page.
    always = tuple(name for name, ids in many.items() if ids in Read.model_fields)
    links = {
        link: model.__mapper__.relationships[link].mapper.class_
        for link, id_field in model.__fyro_links__.items()
        if id_field in Read.model_fields
    } | {name: model.__mapper__.relationships[name].mapper.class_ for name in always}
    expandable = {link: target for link, target in links.items() if target.__fyro_api__}
    # Expanded links are absent unless asked for: unset fields, dropped by exclude_unset.
    Out = (
        create_model(
            f"{name}ReadExpanded",
            __base__=Read,
            **{
                link: (
                    list[target.Read]
                    if link in many
                    else target.Read | None
                    if model.__table__.c[model.__fyro_links__[link]].nullable
                    else target.Read,
                    None,
                )
                for link, target in expandable.items()
            },
        )
        if expandable
        else Read
    )
    can_expand = {
        link: _guard(_levels(target, auth_installed)["read"]) for link, target in expandable.items()
    }
    Page = create_model(
        f"{name}Page",
        items=(list[Out], ...),
        total=(int | None, ...),
        limit=(int, ...),
        offset=(int, ...),
    )

    async def parse_expand(request: Request, expand: str | None) -> tuple[str, ...]:
        """The links named in ?expand=, after checking the caller may read each target."""
        names = tuple(dict.fromkeys(n.strip() for n in (expand or "").split(",") if n.strip()))
        for link in names:
            if link in links and link not in expandable:
                raise HTTPException(
                    422,
                    f"{link} can't be expanded: {links[link].__name__} is not exposed through "
                    "the API (Meta.api = False).",
                )
            if link not in expandable:
                raise HTTPException(
                    422,
                    f"Unknown expand field {link!r}. Use one of: {', '.join(expandable) or 'none'}.",
                )
            await can_expand[link](request)
        return names

    def loaded(qs: Any, expand: tuple[str, ...] = ()) -> Any:
        """`qs` with expanded ForeignKeys joined and readable ManyToManys prefetched."""
        return qs.select_related(*(n for n in expand if n not in many)).prefetch_related(*always)

    def read(obj: Any, expand: tuple[str, ...] = ()) -> Any:
        item = Read.model_validate(obj, from_attributes=True)
        if Out is Read:
            return item
        expanded: dict[str, Any] = {}
        for link in expand:
            target, schema = getattr(obj, link), expandable[link].Read
            if link in many:
                expanded[link] = [schema.model_validate(t, from_attributes=True) for t in target]
            else:
                expanded[link] = (
                    None if target is None else schema.model_validate(target, from_attributes=True)
                )
        return Out.model_construct(**dict(item), **expanded)

    async def list_(
        request: Request,
        filters: Annotated[Filters, Query()],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        search: str | None = None,
        order_by: str | None = None,
        with_total: bool = True,
        expand: str | None = None,
    ) -> Any:
        names = await parse_expand(request, expand)
        try:
            qs = list_query(model, filters.model_dump(exclude_none=True), search, order_by)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        rows = await loaded(qs, names).limit(limit).offset(offset).all()
        # with_total=false skips the count query, which reads every matching row.
        total = await qs.count() if with_total else None
        items = [read(row, names) for row in rows]
        return Page(items=items, total=total, limit=limit, offset=offset)

    async def get(request: Request, pk_value: PkParam, expand: str | None = None) -> Any:
        names = await parse_expand(request, expand)
        return read(await loaded(model.objects, names).get(**{pk: pk_value}), names)

    async def create(data: Create) -> Any:
        values = data.model_dump()
        try:
            row = await model.objects.create(**values)
        except IntegrityError as error:
            raise _conflict(model, error, values) from error
        # Reloaded when ManyToManys must be read: create() returns the row without them.
        return read(await loaded(model.objects).get(**{pk: getattr(row, pk)}) if always else row)

    async def update(pk_value: PkParam, data: Update) -> Any:
        values = data.model_dump(exclude_unset=True)
        if values:
            try:
                await model.objects.filter(**{pk: pk_value}).update(**values)
            except IntegrityError as error:
                raise _conflict(model, error, values) from error
        return read(await loaded(model.objects).get(**{pk: pk_value}))

    async def delete(pk_value: PkParam) -> Response:
        try:
            deleted = await model.objects.filter(**{pk: pk_value}).delete()
        except IntegrityError as error:
            raise HTTPException(409, still_linked(model, error)) from error
        if not deleted:
            raise model.DoesNotExist(f"No {name} matches { ({pk: pk_value}) }.")
        return Response(status_code=204)

    # Readable names in duplicate-route errors (fyro.crud.Post.get) and OpenAPI summaries.
    for action, endpoint in [
        ("list", list_),
        ("get", get),
        ("create", create),
        ("update", update),
        ("delete", delete),
    ]:
        endpoint.__module__ = __name__
        endpoint.__qualname__ = f"{name}.{action}"
        endpoint.__name__ = f"{action}_{name}"

    levels = _levels(model, auth_installed)
    can_read = [Depends(_guard(levels["read"]))]
    can_write = [Depends(_guard(levels["write"]))]

    router = APIRouter()
    item = f"/{{{pk}}}"
    # Out is Read when nothing can be expanded; then exclude_unset changes nothing.
    expanding = {"response_model_exclude_unset": Out is not Read}
    router.add_api_route(
        "", list_, methods=["GET"], response_model=Page, dependencies=can_read, **expanding
    )
    router.add_api_route(
        "", create, methods=["POST"], response_model=Read, status_code=201, dependencies=can_write
    )
    router.add_api_route(
        item, get, methods=["GET"], response_model=Out, dependencies=can_read, **expanding
    )
    router.add_api_route(
        item, update, methods=["PATCH"], response_model=Read, dependencies=can_write
    )
    router.add_api_route(
        item,
        delete,
        methods=["DELETE"],
        status_code=204,
        response_class=Response,
        dependencies=can_write,
    )
    return router
