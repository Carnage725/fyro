import enum
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ValidationError

from fyro import Field, Model, apps
from fyro.apps import Registry
from fyro.conf import ImproperlyConfigured

FIXTURES = Path(__file__).parent / "fixtures"


class Status(enum.Enum):
    DRAFT = "draft"
    LIVE = "live"


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.syspath_prepend(str(FIXTURES))
    fresh = Registry()
    monkeypatch.setattr(apps, "registry", fresh)
    fresh.populate(["blog"])
    yield fresh
    Model.registry.dispose()
    Model.metadata.clear()
    for name in list(sys.modules):
        if name.split(".")[0] == "blog":
            del sys.modules[name]


def define_post(**body):
    """Define `Post` in blog.models with the given annotations/attributes."""
    annotations = body.pop("__annotations__")
    namespace = {"__module__": "blog.models", "__annotations__": annotations, **body}
    return type("Post", (Model,), namespace)


def north_star():
    class Post(Model):
        __module__ = "blog.models"
        title: str = Field(max_length=200)
        body: str
        secret: str = Field(write_only=True)
        published: bool = False

    return Post


def columns(model) -> dict[str, sa.Column]:
    return {c.name: c for c in model.__table__.columns}


# --- table -----------------------------------------------------------------


def test_table_name():
    assert north_star().__tablename__ == "blog_post"


def test_table_name_snake_cases_class_name():
    class BlogPost(Model):
        __module__ = "blog.models"
        title: str

    assert BlogPost.__tablename__ == "blog_blog_post"


def test_meta_table_name_overrides():
    class Post(Model):
        __module__ = "blog.models"
        title: str

        class Meta:
            table_name = "posts"

    assert Post.__tablename__ == "posts"


def test_auto_id_is_first_bigint_primary_key():
    cols = list(north_star().__table__.columns)
    assert [c.name for c in cols] == ["id", "title", "body", "secret", "published"]
    id_col = cols[0]
    assert id_col.primary_key
    assert isinstance(id_col.type, sa.BigInteger)
    assert id_col.autoincrement is True


def test_declared_primary_key_skips_auto_id():
    class Post(Model):
        __module__ = "blog.models"
        slug: str = Field(primary_key=True, max_length=50)

    assert list(columns(Post)) == ["slug"]
    assert Post.__table__.c.slug.primary_key


def test_id_without_primary_key_is_error():
    with pytest.raises(TypeError, match=r"Post\.id"):
        define_post(__annotations__={"id": int})


def test_column_types_and_nullability():
    class Post(Model):
        __module__ = "blog.models"
        title: str = Field(max_length=200)
        body: str
        views: int
        published: bool
        score: float
        price: Decimal
        created: datetime
        day: date
        token: uuid.UUID
        status: Status
        subtitle: str | None
        rating: Optional[int]  # noqa: UP045

    cols = columns(Post)
    assert isinstance(cols["title"].type, sa.String) and cols["title"].type.length == 200
    assert isinstance(cols["body"].type, sa.Text)
    assert isinstance(cols["views"].type, sa.Integer)
    assert isinstance(cols["published"].type, sa.Boolean)
    assert isinstance(cols["score"].type, sa.Float)
    assert isinstance(cols["price"].type, sa.Numeric)
    assert isinstance(cols["created"].type, sa.DateTime) and cols["created"].type.timezone
    assert isinstance(cols["day"].type, sa.Date)
    assert isinstance(cols["token"].type, sa.Uuid)
    assert isinstance(cols["status"].type, sa.Enum) and cols["status"].type.enum_class is Status
    # Phase 6 decision: VARCHAR(255) holding the member value, no Postgres ENUM type
    assert cols["status"].type.native_enum is False
    assert cols["status"].type.length == 255
    assert cols["status"].type.enums == ["draft", "live"]
    assert cols["subtitle"].nullable and cols["rating"].nullable
    assert not cols["title"].nullable and not cols["views"].nullable


def test_unique_and_index_flags():
    class Post(Model):
        __module__ = "blog.models"
        slug: str = Field(max_length=50, unique=True)
        author: str = Field(index=True)

    cols = columns(Post)
    assert cols["slug"].unique
    assert cols["author"].index


@pytest.mark.parametrize("tp", [list[str], int | str, dict])
def test_unsupported_type_is_error(tp):
    with pytest.raises(TypeError, match=r"Post\.tags"):
        define_post(__annotations__={"tags": tp})


# --- Create ----------------------------------------------------------------


def test_create_schema():
    Post = north_star()
    assert set(Post.Create.model_fields) == {"title", "body", "secret", "published"}
    assert Post.Create.__name__ == "PostCreate"
    data = Post.Create(title="t", body="b", secret="s")
    assert data.published is False
    with pytest.raises(ValidationError):
        Post.Create(body="b", secret="s")  # title required
    with pytest.raises(ValidationError):
        Post.Create(title="x" * 201, body="b", secret="s")


def test_create_excludes_read_only_and_uses_default_factory():
    class Post(Model):
        __module__ = "blog.models"
        views: int = Field(default=0, read_only=True)
        tags_count: int = Field(default_factory=lambda: 3, ge=0)

    assert set(Post.Create.model_fields) == {"tags_count"}
    assert Post.Create().tags_count == 3
    with pytest.raises(ValidationError):
        Post.Create(tags_count=-1)


# --- Read ------------------------------------------------------------------


def test_read_schema_excludes_write_only_and_reads_attributes():
    Post = north_star()
    assert set(Post.Read.model_fields) == {"id", "title", "body", "published"}
    post = Post(id=1, title="t", body="b", secret="s", published=True)
    read = Post.Read.model_validate(post)
    assert read.model_dump() == {"id": 1, "title": "t", "body": "b", "published": True}


# --- Update ----------------------------------------------------------------


def test_update_schema_all_optional():
    Post = north_star()
    assert set(Post.Update.model_fields) == {"title", "body", "secret", "published"}
    assert Post.Update().model_dump(exclude_unset=True) == {}
    assert Post.Update(title="new").model_dump(exclude_unset=True) == {"title": "new"}
    with pytest.raises(ValidationError):
        Post.Update(title=None)  # NOT NULL column
    with pytest.raises(ValidationError):
        Post.Update(title="x" * 201)


def test_update_allows_null_for_nullable_field():
    class Post(Model):
        __module__ = "blog.models"
        subtitle: str | None = None

    assert Post.Update(subtitle=None).model_dump(exclude_unset=True) == {"subtitle": None}


# --- Meta ------------------------------------------------------------------


def test_meta_excludes():
    class Post(Model):
        __module__ = "blog.models"
        title: str
        body: str
        secret: str

        class Meta:
            create_exclude = ("secret",)
            read_exclude = ("body",)
            update_exclude = ("title",)

    assert set(Post.Create.model_fields) == {"title", "body"}
    assert set(Post.Read.model_fields) == {"id", "title", "secret"}
    assert set(Post.Update.model_fields) == {"body"}


@pytest.mark.parametrize("key", ["create_exclude", "read_exclude", "update_exclude"])
def test_meta_exclude_unknown_field_is_error(key):
    with pytest.raises(TypeError, match=rf"Post.*{key}.*nope"):
        define_post(__annotations__={"title": str}, Meta=type("Meta", (), {key: ["nope"]}))


def test_meta_schema_replaces():
    class MyCreate(BaseModel):
        title: str

    class Post(Model):
        __module__ = "blog.models"
        title: str
        body: str

        class Meta:
            create_schema = MyCreate

    assert Post.Create is MyCreate
    assert Post.Read.__name__ == "PostRead"


def test_meta_schema_must_be_basemodel():
    with pytest.raises(TypeError, match=r"Post.*read_schema"):
        define_post(__annotations__={"title": str}, Meta=type("Meta", (), {"read_schema": dict}))


def test_unknown_meta_key_is_error():
    with pytest.raises(TypeError, match=r"Post.*tablename"):
        define_post(__annotations__={"title": str}, Meta=type("Meta", (), {"tablename": "x"}))


def test_read_only_and_write_only_conflict():
    with pytest.raises(TypeError, match=r"Post\.secret"):
        define_post(
            __annotations__={"secret": str}, secret=Field(read_only=True, write_only=True)
        )


# --- registry --------------------------------------------------------------


def test_model_registers_with_app(registry):
    Post = north_star()
    assert registry.apps["blog"].models["post"] is Post
    assert Post.__fyro_fields__["secret"].write_only


def test_model_outside_models_module_is_error():
    with pytest.raises(ImproperlyConfigured, match=r"models\.py"):
        type("Post", (Model,), {"__module__": "blog.views", "__annotations__": {"title": str}})
