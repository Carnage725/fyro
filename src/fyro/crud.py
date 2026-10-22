from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Response
from pydantic import create_model
from sqlalchemy.exc import IntegrityError

from fyro.conf import ImproperlyConfigured

UNIQUE_VIOLATION = "23505"  # Postgres SQLSTATE


def _conflict(model: Any, error: IntegrityError) -> HTTPException:
    """409 for a unique violation, naming the field; re-raise anything else."""
    if getattr(error.orig, "sqlstate", None) != UNIQUE_VIOLATION:
        raise error
    violated = getattr(error.orig.__cause__, "constraint_name", None)
    table = model.__table__
    columns: list[str] = []
    for constraint in [*table.constraints, *table.indexes]:
        if str(constraint.name) == violated:
            columns = [column.key for column in constraint.columns]
    what = f"this {' and '.join(columns)}" if columns else "these values"
    return HTTPException(409, f"{model.__name__} with {what} already exists.")


def build_router(model: Any) -> APIRouter:
    """List, get, create, patch and delete routes for one model, mounted by create_app()."""
    name = model.__name__
    primary_key = list(model.__table__.primary_key.columns)
    if len(primary_key) != 1:
        raise ImproperlyConfigured(
            f"{name} has a composite primary key, which auto CRUD does not support. "
            f"Set Meta.api = False on {name}."
        )
    pk = primary_key[0].key
    PkParam = Annotated[primary_key[0].type.python_type, Path(alias=pk)]
    Read, Create, Update = model.Read, model.Create, model.Update
    # write_only fields are not in Read, so they never become filters and can't be probed.
    Filters = create_model(
        f"{name}Filters",
        **{
            field: (info.annotation | None, None)
            for field, info in Read.model_fields.items()
            if field in model.__fyro_fields__
        },
    )
    Page = create_model(
        f"{name}Page", items=(list[Read], ...), total=(int, ...), limit=(int, ...), offset=(int, ...)
    )

    def read(obj: Any) -> Any:
        return Read.model_validate(obj, from_attributes=True)

    async def list_(
        filters: Annotated[Filters, Query()],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Any:
        qs = model.objects.filter(**filters.model_dump(exclude_none=True))
        rows = await qs.order_by(pk).limit(limit).offset(offset).all()
        return Page(
            items=[read(row) for row in rows], total=await qs.count(), limit=limit, offset=offset
        )

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

    router = APIRouter()
    item = f"/{{{pk}}}"
    router.add_api_route("", list_, methods=["GET"], response_model=Page)
    router.add_api_route("", create, methods=["POST"], response_model=Read, status_code=201)
    router.add_api_route(item, get, methods=["GET"], response_model=Read)
    router.add_api_route(item, update, methods=["PATCH"], response_model=Read)
    router.add_api_route(item, delete, methods=["DELETE"], status_code=204, response_class=Response)
    return router
