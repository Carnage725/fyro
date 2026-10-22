from fastapi import FastAPI

from fyro import apps
from fyro.conf import get_settings


def create_app() -> FastAPI:
    """Load installed apps and build the FastAPI app."""
    apps.registry.populate(get_settings().installed_apps)
    app = FastAPI()
    for app_config in apps.registry.apps.values():
        if app_config.routes_module is not None:
            app.include_router(app_config.routes_module.router, prefix=f"/api/{app_config.label}")
    return app
