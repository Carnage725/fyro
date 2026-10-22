from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import create_model
from sqlalchemy.exc import IntegrityError

from fyro.conf import ImproperlyConfigured
from fyro.models.base import _storable_text

UNIQUE_VIOLATION = "23505"  # Postgres SQLSTATE
LIST_PARAMETERS = ("limit", "offset", "search", "order_by", "with_total")


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


def _conflict(model: Any, error: IntegrityError) -> HTTPException:
    """409 for a unique violation, naming the field; re-raise anything else."""
    return HTTPException(409, unique_violation(model, error)[1])


def _field_type(info: Any) -> Any:
    """A schema field's type with its constraints (int range, storable text) still attached."""
    return Annotated[info.annotation, *info.metadata] if info.metadata else info.annotation


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
    # write_only fields are not in Read, so they never become filters and can't be probed.
    Filters = create_model(
        f"{name}Filters",
        **{
            field: (_field_type(info) | None, None)
            for field, info in Read.model_fields.items()
            if field in model.__fyro_fields__
        },
    )
    Page = create_model(
        f"{name}Page",
        items=(list[Read], ...),
        total=(int | None, ...),
        limit=(int, ...),
        offset=(int, ...),
    )

    def read(obj: Any) -> Any:
        return Read.model_validate(obj, from_attributes=True)

    async def list_(
        filters: Annotated[Filters, Query()],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        search: str | None = None,
        order_by: str | None = None,
        with_total: bool = True,
    ) -> Any:
        try:
            qs = list_query(model, filters.model_dump(exclude_none=True), search, order_by)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        rows = await qs.limit(limit).offset(offset).all()
        # with_total=false skips the count query, which reads every matching row.
        total = await qs.count() if with_total else None
        return Page(items=[read(row) for row in rows], total=total, limit=limit, offset=offset)

    async def get(pk_value: PkParam) -> Any:
        return read(await model.objects.get(**{pk: pk_value}))

    async def create(data: Create) -> Any:
        try:
            return read(await model.objects.create(**data.model_dump()))
        except IntegrityError as error:
            raise _conflict(model, error) from error

    async def update(pk_value: PkParam, data: Update) -> Any:
        values = data.model_dump(exclude_unset=True)
        if values:
            try:
                await model.objects.filter(**{pk: pk_value}).update(**values)
            except IntegrityError as error:
                raise _conflict(model, error) from error
        return read(await model.objects.get(**{pk: pk_value}))

    async def delete(pk_value: PkParam) -> Response:
        if not await model.objects.filter(**{pk: pk_value}).delete():
            raise model.DoesNotExist(f"No {name} matches {({pk: pk_value})}.")
        return Response(status_code=204)

    # Readable names in duplicate-route errors (fyro.crud.Post.get) and OpenAPI summaries.
    for action, endpoint in [
        ("list", list_), ("get", get), ("create", create), ("update", update), ("delete", delete)
    ]:
        endpoint.__module__ = __name__
        endpoint.__qualname__ = f"{name}.{action}"
        endpoint.__name__ = f"{action}_{name}"

    # Defaults with fyro.auth: anyone reads, logged-in users write. Without it, all public.
    levels = {"read": "public", "write": "authenticated" if auth_installed else "public"}
    levels |= model.__fyro_permissions__ or {}
    can_read = [Depends(_guard(levels["read"]))]
    can_write = [Depends(_guard(levels["write"]))]

    router = APIRouter()
    item = f"/{{{pk}}}"
    router.add_api_route("", list_, methods=["GET"], response_model=Page, dependencies=can_read)
    router.add_api_route(
        "", create, methods=["POST"], response_model=Read, status_code=201, dependencies=can_write
    )
    router.add_api_route(item, get, methods=["GET"], response_model=Read, dependencies=can_read)
    router.add_api_route(
        item, update, methods=["PATCH"], response_model=Read, dependencies=can_write
    )
    router.add_api_route(
        item, delete, methods=["DELETE"], status_code=204, response_class=Response,
        dependencies=can_write,
    )
    return router
