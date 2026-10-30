import os
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from fyro import Field, ForeignKey, Model, apps
from fyro.apps import Registry

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)


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


def author_model():
    class Author(Model):
        __module__ = "blog.models"
        name: str = Field(max_length=100)

    return Author


def north_star():
    Author = author_model()

    class Post(Model):
        __module__ = "blog.models"
        title: str = Field(max_length=200)
        author: Author = ForeignKey(on_delete="cascade")
        editor: Author | None = ForeignKey(on_delete="set_null", default=None)

    return Author, Post


# --- table -----------------------------------------------------------------


def test_foreign_key_becomes_id_column_in_declaration_order():
    _, Post = north_star()
    assert [c.name for c in Post.__table__.columns] == ["id", "title", "author_id", "editor_id"]


def test_foreign_key_column_matches_target_primary_key():
    _, Post = north_star()
    col = Post.__table__.c.author_id
    assert isinstance(col.type, sa.BigInteger)
    assert not col.nullable
    assert col.index
    (fk,) = col.foreign_keys
    assert fk.target_fullname == "blog_author.id"
    assert fk.ondelete == "CASCADE"


def test_foreign_key_to_uuid_primary_key():
    class Author(Model):
        __module__ = "blog.models"
        key: uuid.UUID = Field(primary_key=True)

    class Post(Model):
        __module__ = "blog.models"
        author: Author = ForeignKey(on_delete="cascade")

    assert isinstance(Post.__table__.c.author_id.type, sa.Uuid)
    assert Post.Create.model_fields["author_id"].annotation is uuid.UUID


def test_nullable_foreign_key_set_null():
    _, Post = north_star()
    col = Post.__table__.c.editor_id
    assert col.nullable
    assert next(iter(col.foreign_keys)).ondelete == "SET NULL"


def test_restrict():
    Author = author_model()

    class Post(Model):
        __module__ = "blog.models"
        author: Author = ForeignKey(on_delete="restrict")

    assert next(iter(Post.__table__.c.author_id.foreign_keys)).ondelete == "RESTRICT"


def test_constraint_name_follows_naming_convention():
    _, Post = north_star()
    names = {c.name for c in Post.__table__.foreign_key_constraints}
    assert names == {"fk_blog_post_author_id_blog_author", "fk_blog_post_editor_id_blog_author"}


# --- relationship ----------------------------------------------------------


def test_relationship_raises_on_lazy_load():
    Author, Post = north_star()
    prop = Post.author.property
    assert prop.mapper.class_ is Author
    assert prop.lazy == "raise"
    assert "author" not in Post.__table__.c


def test_two_links_to_same_target_use_their_own_column():
    _, Post = north_star()
    assert [c.name for c in Post.author.property.local_columns] == ["author_id"]
    assert [c.name for c in Post.editor.property.local_columns] == ["editor_id"]


# --- schemas ---------------------------------------------------------------


def test_schemas_use_id_field():
    _, Post = north_star()
    for schema in (Post.Create, Post.Read, Post.Update):
        assert "author_id" in schema.model_fields
        assert "author" not in schema.model_fields
    assert Post.Create.model_fields["author_id"].is_required()
    assert not Post.Create.model_fields["editor_id"].is_required()
    with pytest.raises(ValidationError):
        Post.Create(title="t", author_id=2**63)  # outside BIGINT


def test_read_and_write_only_flags_apply_to_id_field():
    Author = author_model()

    class Post(Model):
        __module__ = "blog.models"
        author: Author = ForeignKey(on_delete="cascade", read_only=True)
        owner: Author = ForeignKey(on_delete="cascade", write_only=True)

    assert "author_id" not in Post.Create.model_fields
    assert "author_id" in Post.Read.model_fields
    assert "owner_id" in Post.Create.model_fields
    assert "owner_id" not in Post.Read.model_fields


def test_meta_exclude_uses_id_field_name():
    Author = author_model()

    class Post(Model):
        __module__ = "blog.models"
        author: Author = ForeignKey(on_delete="cascade")

        class Meta:
            read_exclude = ("author_id",)

    assert "author_id" not in Post.Read.model_fields


# --- errors ----------------------------------------------------------------


def test_on_delete_missing_is_error():
    Author = author_model()
    with pytest.raises(TypeError, match=r"Post\.author: .*on_delete"):

        class Post(Model):
            __module__ = "blog.models"
            author: Author = ForeignKey()


def test_unknown_on_delete_is_error():
    Author = author_model()
    with pytest.raises(TypeError, match=r"Post\.author: .*'nope'.*cascade"):

        class Post(Model):
            __module__ = "blog.models"
            author: Author = ForeignKey(on_delete="nope")


def test_set_null_on_required_field_is_error():
    Author = author_model()
    with pytest.raises(TypeError, match=r"Post\.author: .*Author \| None"):

        class Post(Model):
            __module__ = "blog.models"
            author: Author = ForeignKey(on_delete="set_null")


def test_foreign_key_to_non_model_is_error():
    with pytest.raises(TypeError, match=r"Post\.author: .*Model"):

        class Post(Model):
            __module__ = "blog.models"
            author: int = ForeignKey(on_delete="cascade")


def test_model_annotation_without_foreign_key_is_error():
    Author = author_model()
    with pytest.raises(TypeError, match=r"Post\.author: .*ForeignKey\(on_delete="):

        class Post(Model):
            __module__ = "blog.models"
            author: Author


def test_id_field_clash_is_error():
    Author = author_model()
    with pytest.raises(TypeError, match=r"Post\.author_id"):

        class Post(Model):
            __module__ = "blog.models"
            author_id: int
            author: Author = ForeignKey(on_delete="cascade")


def test_self_reference_is_error():
    with pytest.raises(TypeError, match=r"Category: .*'Category'.*not defined"):

        class Category(Model):
            __module__ = "blog.models"
            parent: "Category | None" = ForeignKey(on_delete="cascade")


# --- database --------------------------------------------------------------


async def test_create_all_creates_constraint_and_index():
    north_star()
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Model.metadata.drop_all)
            await conn.run_sync(Model.metadata.create_all)

            def inspect(sync_conn):
                insp = sa.inspect(sync_conn)
                return insp.get_foreign_keys("blog_post"), insp.get_indexes("blog_post")

            fks, indexes = await conn.run_sync(inspect)
            await conn.run_sync(Model.metadata.drop_all)
    finally:
        await engine.dispose()

    found = {
        fk["name"]: (fk["constrained_columns"], fk["referred_table"], fk["options"].get("ondelete"))
        for fk in fks
    }
    assert found == {
        "fk_blog_post_author_id_blog_author": (["author_id"], "blog_author", "CASCADE"),
        "fk_blog_post_editor_id_blog_author": (["editor_id"], "blog_author", "SET NULL"),
    }
    assert {i["name"] for i in indexes} == {"ix_blog_post_author_id", "ix_blog_post_editor_id"}
