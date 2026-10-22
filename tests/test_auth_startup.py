"""fyro.auth wiring that needs no database: loading, Meta.permissions checks, warnings."""

import logging
import os
import sys
from pathlib import Path

import pytest

from fyro import Field, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import ImproperlyConfigured, get_settings
from fyro.db import engine as db_engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in list(os.environ):
        if name.startswith("FYRO_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("FYRO_SETTINGS", "fixture_settings")
    monkeypatch.syspath_prepend(str(FIXTURES))
    monkeypatch.setattr(apps, "registry", Registry())
    # The process-wide engine is global state: anything a test creates (e.g. a request through
    # SessionMiddleware) is discarded afterwards, so it can never point at another test's database.
    monkeypatch.setattr(db_engine, "_async_engine", None)
    get_settings.cache_clear()
    yield
    Model.registry.dispose()
    Model.metadata.clear()
    get_settings.cache_clear()
    for name in list(sys.modules):
        if name.split(".")[0] in ("blog", "journal", "fixture_settings") or name.startswith(
            "fyro.auth"
        ):
            del sys.modules[name]


@pytest.mark.parametrize(
    "installed", [["fyro.auth", "journal"], ["journal", "fyro.auth"]], ids=["auth-first", "auth-last"]
)
def test_apps_may_import_fyro_auth_in_any_order(installed):
    # bug: "Model User ... belongs to app 'auth', which is not installed" (found by the spike)
    apps.registry.populate(installed)
    assert set(apps.registry.apps["auth"].models) == {"user", "session"}
    assert apps.registry.apps["journal"].routes_module is not None


def test_auth_routes_mounted_and_no_user_crud():
    apps.registry.populate(["fyro.auth"])
    paths = create_app().openapi()["paths"]
    assert {"/api/auth/login", "/api/auth/logout", "/api/auth/me"} <= set(paths)
    assert not any(path.startswith(("/api/auth/user", "/api/auth/session")) for path in paths)


def test_user_password_is_in_no_schema():
    apps.registry.populate(["fyro.auth"])
    from fyro.auth import User

    for schema in (User.Create, User.Read, User.Update):
        assert "password" not in schema.model_fields


@pytest.mark.parametrize(
    ("permissions", "fragment"),
    [
        ({"delete": "public"}, "delete"),
        ({"read": "admins"}, "admins"),
        ("public", "dict"),
    ],
    ids=["unknown-action", "unknown-level", "not-a-dict"],
)
def test_meta_permissions_validated_at_class_definition(permissions, fragment):
    apps.registry.populate(["blog"])
    with pytest.raises(TypeError, match=rf"Post\.Meta\.permissions.*{fragment}"):
        type(
            "Post",
            (Model,),
            {
                "__module__": "blog.models",
                "__annotations__": {"title": str},
                "Meta": type("Meta", (), {"permissions": permissions}),
            },
        )


def test_meta_permissions_without_fyro_auth_is_startup_error():
    apps.registry.populate(["blog"])

    class Post(Model):
        __module__ = "blog.models"
        title: str

        class Meta:
            permissions = {"write": "superuser"}  # noqa: RUF012

    with pytest.raises(ImproperlyConfigured, match=r"Post.*fyro\.auth"):
        create_app()


@pytest.mark.parametrize(("debug", "warned"), [("false", True), ("true", False)])
def test_open_writes_warning_only_without_auth_and_debug_off(monkeypatch, caplog, debug, warned):
    monkeypatch.setenv("FYRO_DEBUG", debug)
    apps.registry.populate(["blog"])

    class Post(Model):
        __module__ = "blog.models"
        title: str = Field(max_length=10)

    with caplog.at_level(logging.WARNING, logger="fyro"):
        create_app()
    assert ("API writes are open to anyone" in caplog.text) is warned


def test_no_open_writes_warning_with_fyro_auth(caplog):
    apps.registry.populate(["fyro.auth", "blog"])

    class Post(Model):
        __module__ = "blog.models"
        title: str

    with caplog.at_level(logging.WARNING, logger="fyro"):
        create_app()
    assert "open to anyone" not in caplog.text


def test_admin_not_mounted_without_fyro_auth(caplog):
    apps.registry.populate(["blog"])
    from fastapi.routing import iter_route_contexts

    with caplog.at_level(logging.INFO, logger="fyro"):
        app = create_app()
    paths = [route.path for route in iter_route_contexts(app.routes)]
    assert not any(path and path.startswith("/admin") for path in paths)
    assert all(getattr(route, "path", "") != "/admin/static" for route in app.routes)
    assert "admin is not mounted" in caplog.text


@pytest.mark.parametrize(
    ("meta", "fragment"),
    [
        ({"admin": "yes"}, "admin"),
        ({"list_display": ("nope",)}, "nope"),
        ({"search_fields": ("views",)}, "views"),
    ],
    ids=["admin-not-bool", "unknown-list-display", "search-non-text"],
)
def test_admin_meta_keys_validated(meta, fragment):
    apps.registry.populate(["blog"])
    with pytest.raises(TypeError, match=rf"Post\.Meta.*{fragment}"):
        type(
            "Post",
            (Model,),
            {
                "__module__": "blog.models",
                "__annotations__": {"title": str, "views": int},
                "Meta": type("Meta", (), meta),
            },
        )
