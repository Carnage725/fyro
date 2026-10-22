import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter

from fyro import Field, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import ImproperlyConfigured, get_settings
from fyro.db import engine as db_engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for name in list(os.environ):
        if name.startswith("FYRO_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("FYRO_SETTINGS", "fixture_settings")
    monkeypatch.syspath_prepend(str(FIXTURES))
    get_settings.cache_clear()
    fresh = Registry()
    monkeypatch.setattr(apps, "registry", fresh)
    # The process-wide engine is global state: anything a test creates (e.g. a request through
    # SessionMiddleware) is discarded afterwards, so it can never point at another test's database.
    monkeypatch.setattr(db_engine, "_async_engine", None)
    fresh.populate(["blog"])
    yield fresh
    Model.registry.dispose()
    Model.metadata.clear()
    get_settings.cache_clear()
    for name in list(sys.modules):
        if name.split(".")[0] in ("blog", "fixture_settings"):
            del sys.modules[name]


def test_path_segment_is_snake_case_and_param_is_pk_name():
    class BlogPost(Model):
        __module__ = "blog.models"
        slug: str = Field(primary_key=True, max_length=50)

    paths = create_app().openapi()["paths"]
    assert "/api/blog/blog_post" in paths
    assert "/api/blog/blog_post/{slug}" in paths


def test_composite_primary_key_needs_api_false():
    class Membership(Model):
        __module__ = "blog.models"
        user: int = Field(primary_key=True)
        group: int = Field(primary_key=True)

    with pytest.raises(ImproperlyConfigured, match=r"Membership.*Meta\.api = False"):
        create_app()


def test_composite_primary_key_with_api_false_is_fine():
    class Membership(Model):
        __module__ = "blog.models"
        user: int = Field(primary_key=True)
        group: int = Field(primary_key=True)

        class Meta:
            api = False

    assert "/api/blog/membership" not in create_app().openapi()["paths"]


def test_meta_api_must_be_bool():
    with pytest.raises(TypeError, match=r"Post\.Meta\.api"):

        class Post(Model):
            __module__ = "blog.models"
            title: str

            class Meta:
                api = "no"


def test_duplicate_route_is_startup_error(registry):
    class Post(Model):
        __module__ = "blog.models"
        title: str

    custom = APIRouter()

    @custom.get("/post/{post_id}")
    async def my_post(post_id: int):
        return {}

    registry.apps["blog"].routes_module = SimpleNamespace(router=custom)
    with pytest.raises(
        ImproperlyConfigured, match=r"GET /api/blog/post/\{.*my_post.*fyro\.crud\.Post\.get"
    ):
        create_app()


@pytest.mark.parametrize("field", ["limit", "offset", "search", "order_by", "with_total"])
def test_field_named_like_a_list_parameter_is_startup_error(field):
    # bug: ?limit= would mean both "page size" and "filter on the limit field"
    type(
        "Post",
        (Model,),
        {"__module__": "blog.models", "__annotations__": {field: str}},
    )
    with pytest.raises(ImproperlyConfigured, match=rf"(?i)Post\.{field}.*rename.*Meta\.api = False"):
        create_app()


def test_reserved_name_is_fine_without_api():
    type(
        "Post",
        (Model,),
        {
            "__module__": "blog.models",
            "__annotations__": {"limit": int},
            "Meta": type("Meta", (), {"api": False}),
        },
    )
    create_app()
