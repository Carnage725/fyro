import logging
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

logger = logging.getLogger("fyro")


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


def _check_auth(auth_installed: bool) -> None:
    if auth_installed:
        return
    models = [m for a in apps.registry.apps.values() for m in a.models.values() if m.__fyro_api__]
    secured = sorted(m.__name__ for m in models if m.__fyro_permissions__)
    if secured:
        raise ImproperlyConfigured(
            f"{', '.join(secured)} set Meta.permissions, which needs \"fyro.auth\" in "
            "installed_apps."
        )
    if models and not get_settings().debug:
        logger.warning("API writes are open to anyone; add fyro.auth to installed_apps.")


def create_app() -> FastAPI:
    """Load installed apps and build the FastAPI app."""
    apps.registry.populate(get_settings().installed_apps)
    auth = apps.registry.apps.get("auth")
    auth_installed = auth is not None and auth.name == "fyro.auth"
    _check_auth(auth_installed)
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
                    build_router(model, auth_installed),
                    prefix=f"{prefix}/{segment}",
                    tags=[f"{app_config.label}.{segment}"],
                )
    if auth_installed:
        from fyro import admin

        admin.mount(app)
    else:
        logger.info('The admin is not mounted: it needs "fyro.auth" in installed_apps.')
    _check_duplicate_routes(app)
    return app
