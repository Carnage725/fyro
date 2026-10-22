from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fyro import apps
from fyro.conf import get_settings
from fyro.db.engine import dispose_engine, get_engine
from fyro.db.session import SessionMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    """Load installed apps and build the FastAPI app."""
    apps.registry.populate(get_settings().installed_apps)
    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(SessionMiddleware)
    for app_config in apps.registry.apps.values():
        if app_config.routes_module is not None:
            app.include_router(app_config.routes_module.router, prefix=f"/api/{app_config.label}")
    return app
