import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from fyro import apps, create_app
from fyro.apps import Registry
from fyro.conf import ImproperlyConfigured, get_settings
from fyro.db import engine as db_engine

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_MODULES = ("blog", "nomodels", "norouter", "dup", "fixture_settings")


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for name in list(os.environ):
        if name.startswith("FYRO_"):
            monkeypatch.delenv(name)
    monkeypatch.syspath_prepend(str(FIXTURES))
    monkeypatch.setenv("FYRO_SETTINGS", "fixture_settings")
    fresh = Registry()
    monkeypatch.setattr(apps, "registry", fresh)
    # The process-wide engine is global state: anything a test creates (e.g. a request through
    # SessionMiddleware) is discarded afterwards, so it can never point at another test's database.
    monkeypatch.setattr(db_engine, "_async_engine", None)
    get_settings.cache_clear()
    yield fresh
    get_settings.cache_clear()
    for name in list(sys.modules):
        if name.split(".")[0] in FIXTURE_MODULES:
            del sys.modules[name]


def stub(module: str) -> type:
    return type("Stub", (), {"__module__": module})


def test_valid_app_is_loaded(registry):
    registry.populate(["blog"])
    app = registry.apps["blog"]
    assert app.name == "blog"
    assert app.label == "blog"
    assert app.models_module.__name__ == "blog.models"
    assert app.routes_module.__name__ == "blog.routes"
    assert app.admin_module.__name__ == "blog.admin"


def test_label_is_last_segment(registry):
    registry.populate(["dup.blog"])
    assert registry.apps["blog"].name == "dup.blog"
    assert registry.apps["blog"].routes_module is None
    assert registry.apps["blog"].admin_module is None


def test_missing_models_module(registry):
    with pytest.raises(ImproperlyConfigured, match=r"nomodels.*models\.py"):
        registry.populate(["nomodels"])


def test_routes_without_router(registry):
    with pytest.raises(ImproperlyConfigured, match=r"norouter/routes\.py.*router"):
        registry.populate(["norouter"])


def test_duplicate_labels(registry):
    with pytest.raises(ImproperlyConfigured, match=r"'blog'.*'dup\.blog'"):
        registry.populate(["blog", "dup.blog"])


def test_unknown_app_package(registry):
    with pytest.raises(ImproperlyConfigured, match="ghost"):
        registry.populate(["ghost"])


def test_populate_is_idempotent(registry):
    registry.populate(["blog"])
    first = registry.apps["blog"]
    registry.populate(["blog"])
    assert registry.apps["blog"] is first
    assert list(registry.apps) == ["blog"]


def test_apps_view_is_read_only(registry):
    registry.populate(["blog"])
    with pytest.raises(TypeError):
        registry.apps["other"] = registry.apps["blog"]


def test_register_model(registry):
    registry.populate(["blog"])
    model = stub("blog.models")
    registry.register_model("blog", model)
    assert registry.apps["blog"].models["stub"] is model


def test_register_model_unknown_app(registry):
    registry.populate(["blog"])
    with pytest.raises(ImproperlyConfigured, match="installed_apps"):
        registry.register_model("shop", stub("shop.models"))


def test_register_model_outside_models_module(registry):
    registry.populate(["blog"])
    with pytest.raises(ImproperlyConfigured, match=r"blog/models\.py"):
        registry.register_model("blog", stub("blog.routes"))


async def test_create_app_mounts_routes_under_api():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/blog/hello")
        assert response.status_code == 200
        assert response.json() == {"app": "blog"}
        assert (await client.get("/hello")).status_code == 404
