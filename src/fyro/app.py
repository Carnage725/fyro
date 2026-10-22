import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts

from fyro import apps, db
from fyro.conf import ImproperlyConfigured, get_settings
from fyro.crud import build_router
from fyro.db.engine import dispose_engine, get_engine
from fyro.db.session import SessionMiddleware
from fyro.models.base import _snake


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    yield
    await dispose_engine()


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


def _check_duplicate_routes(app: FastAPI) -> None:
    seen: dict[tuple[str, str], str] = {}
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        shape = re.sub(r"\{[^}]*\}", "{}", route.path)  # /post/{id} == /post/{post_id}
        where = f"{route.endpoint.__module__}.{route.endpoint.__qualname__}"
        for method in sorted(route.methods):
            if (method, shape) in seen:
                raise ImproperlyConfigured(
                    f"Duplicate route {method} {route.path}: defined by {seen[method, shape]} "
                    f"and {where}. Remove one, or set Meta.api = False to drop the auto CRUD route."
                )
            seen[method, shape] = where


def create_app() -> FastAPI:
    """Load installed apps and build the FastAPI app."""
    apps.registry.populate(get_settings().installed_apps)
    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(SessionMiddleware)
    app.add_exception_handler(db.DoesNotExist, _not_found)
    for app_config in apps.registry.apps.values():
        prefix = f"/api/{app_config.label}"
        # Custom routes first, so /post/stats is not swallowed by the CRUD /post/{id}.
        if app_config.routes_module is not None:
            app.include_router(app_config.routes_module.router, prefix=prefix)
        for model in app_config.models.values():
            if model.__fyro_api__:
                segment = _snake(model.__name__)
                app.include_router(
                    build_router(model),
                    prefix=f"{prefix}/{segment}",
                    tags=[f"{app_config.label}.{segment}"],
                )
    _check_duplicate_routes(app)
    return app
