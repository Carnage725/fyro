import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import event, text
from support import MigratedSchema

import fyro.db
from fyro import Field, ForeignKey, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import get_settings
from fyro.db import engine as db_engine

pytestmark = pytest.mark.asyncio(loop_scope="module")

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)
PASSWORD = "correct horse battery staple"
PUBLIC = {"read": "public", "write": "public"}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        for name in list(os.environ):
            if name.startswith("FYRO_"):
                mp.delenv(name)
        mp.setenv("FYRO_SETTINGS", "fixture_settings")
        mp.setenv("FYRO_DATABASE_URL", DATABASE_URL)
        mp.syspath_prepend(str(FIXTURES))
        get_settings.cache_clear()
        mp.setattr(apps, "registry", Registry())
        apps.registry.populate(["fyro.auth", "blog"])
        from fyro.auth import User

        class Author(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=100)

            class Meta:
                permissions = PUBLIC

        class Secret(Model):
            __module__ = "blog.models"
            value: str

            class Meta:
                permissions = {"read": "superuser", "write": "superuser"}  # noqa: RUF012

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)
            author: Author = ForeignKey(on_delete="cascade")
            editor: Author | None = ForeignKey(on_delete="set_null", default=None)
            secret: Secret | None = ForeignKey(on_delete="set_null", default=None)
            owner: User | None = ForeignKey(on_delete="set_null", default=None)

            class Meta:
                permissions = PUBLIC

        class Summary(BaseModel):  # docs/guides/dropping-down.md: replacing a schema
            id: int
            name: str

        class Profile(Model):
            __module__ = "blog.models"
            name: str = Field(max_length=100)
            bio: str = ""

            class Meta:
                read_schema = Summary
                permissions = PUBLIC

        class Card(Model):
            __module__ = "blog.models"
            profile: Profile = ForeignKey(on_delete="cascade")

            class Meta:
                permissions = PUBLIC

        class Review(Model):
            __module__ = "blog.models"
            author: Author = ForeignKey(on_delete="restrict")

            class Meta:
                permissions = PUBLIC

        schema = MigratedSchema(tmp_path_factory.mktemp("schema"), DATABASE_URL)
        await schema.create()
        yield create_app()
        await schema.drop()
        await db_engine.dispose_engine()
        Model.registry.dispose()
        Model.metadata.clear()
        get_settings.cache_clear()
        for name in list(sys.modules):
            if name.split(".")[0] in ("blog", "fixture_settings") or name.startswith("fyro.auth"):
                del sys.modules[name]


@pytest_asyncio.fixture(loop_scope="module")
async def client(app):
    async with fyro.db.get_engine().begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE auth_user, auth_session, blog_author, blog_secret, blog_post, "
                "blog_review, blog_profile, blog_card RESTART IDENTITY"
            )
        )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@contextmanager
def count_selects():
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = fyro.db.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def author(client, name="ana") -> int:
    response = await client.post("/api/blog/author", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def login(client, username, *, superuser=False) -> dict:
    from fyro.auth import User
    from fyro.auth.passwords import hash_password

    async with fyro.db.session():
        await User.objects.create(
            username=username, password=hash_password(PASSWORD), is_superuser=superuser
        )
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


# --- ids in bodies and filters ---------------------------------------------


async def test_create_and_read_use_id_fields(client):
    ana = await author(client)
    response = await client.post("/api/blog/post", json={"title": "t", "author_id": ana})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": 1,
        "title": "t",
        "author_id": ana,
        "editor_id": None,
        "secret_id": None,
        "owner_id": None,
    }
    assert set((await client.get("/api/blog/post/1")).json()) == set(body)


async def test_filter_by_id_field(client):
    ana, bob = await author(client, "ana"), await author(client, "bob")
    for title, who in [("a", ana), ("b", bob), ("a2", ana)]:
        await client.post("/api/blog/post", json={"title": title, "author_id": who})
    response = await client.get("/api/blog/post", params={"author_id": ana})
    assert [p["title"] for p in response.json()["items"]] == ["a", "a2"]


async def test_missing_target_is_422_naming_field(client):
    response = await client.post("/api/blog/post", json={"title": "t", "author_id": 999})
    assert response.status_code == 422
    assert response.json()["detail"] == "author_id: no Author with id 999."

    ana = await author(client)
    created = await client.post("/api/blog/post", json={"title": "t", "author_id": ana})
    response = await client.patch(f"/api/blog/post/{created.json()['id']}", json={"editor_id": 998})
    assert response.status_code == 422
    assert response.json()["detail"] == "editor_id: no Author with id 998."


# --- expand ----------------------------------------------------------------


async def test_expand_on_get_and_list(client):
    ana, bob = await author(client, "ana"), await author(client, "bob")
    await client.post("/api/blog/post", json={"title": "t", "author_id": ana, "editor_id": bob})
    await client.post("/api/blog/post", json={"title": "u", "author_id": bob})

    plain = (await client.get("/api/blog/post/1")).json()
    assert "author" not in plain and "editor" not in plain

    got = (await client.get("/api/blog/post/1", params={"expand": "author"})).json()
    assert got["author"] == {"id": ana, "name": "ana"}
    assert "editor" not in got

    items = (await client.get("/api/blog/post", params={"expand": "author, editor"})).json()[
        "items"
    ]
    assert [(p["author"]["name"], p["editor"]) for p in items] == [
        ("ana", {"id": bob, "name": "bob"}),
        ("bob", None),
    ]


async def test_expand_unknown_is_422(client):
    response = await client.get("/api/blog/post", params={"expand": "title"})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Unknown expand field 'title'. Use one of: author, editor, secret."
    )


async def test_expand_hidden_model_is_422(client):
    response = await client.get("/api/blog/post", params={"expand": "owner"})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "owner can't be expanded: User is not exposed through the API (Meta.api = False)."
    )


async def test_expand_checks_target_read_permission(client):
    ana = await author(client)
    await client.post("/api/blog/post", json={"title": "t", "author_id": ana})
    params = {"expand": "secret"}
    assert (await client.get("/api/blog/post", params=params)).status_code == 401
    user = await login(client, "alice")
    assert (await client.get("/api/blog/post", params=params, headers=user)).status_code == 403
    admin = await login(client, "root", superuser=True)
    response = await client.get("/api/blog/post", params=params, headers=admin)
    assert response.status_code == 200
    assert response.json()["items"][0]["secret"] is None


async def test_expanded_list_is_one_select(client):
    ana = await author(client)
    async with fyro.db.session():
        Post = apps.registry.apps["blog"].models["post"]
        for i in range(50):
            await Post.objects.create(title=f"p{i}", author_id=ana, editor_id=ana)
    params = {"expand": "author,editor", "limit": 50, "with_total": "false"}
    with count_selects() as selects:
        response = await client.get("/api/blog/post", params=params)
    assert len(response.json()["items"]) == 50
    assert len(selects) == 1


# --- delete ----------------------------------------------------------------


async def test_delete_restricted_target_is_409(client):
    ana = await author(client)
    await client.post("/api/blog/review", json={"author_id": ana})
    response = await client.delete(f"/api/blog/author/{ana}")
    assert response.status_code == 409
    assert response.json()["detail"] == (
        'This Author is still linked from Review.author (on_delete="restrict"). '
        "Delete or change those rows first."
    )


async def test_delete_cascades(client):
    ana = await author(client)
    await client.post("/api/blog/post", json={"title": "t", "author_id": ana})
    assert (await client.delete(f"/api/blog/author/{ana}")).status_code == 204
    assert (await client.get("/api/blog/post")).json()["total"] == 0


# --- openapi ---------------------------------------------------------------


async def test_openapi_shows_expand(client):
    spec = (await client.get("/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    expanded = schemas["PostReadExpanded"]["properties"]
    assert expanded["author"]["$ref"].endswith("/AuthorRead")
    assert {"author", "editor", "secret"} <= set(expanded)
    assert "owner" not in expanded
    assert "author" not in expanded.get("required", [])
    params = {p["name"] for p in spec["paths"]["/api/blog/post"]["get"]["parameters"]}
    assert "expand" in params


# --- replaced read schema (docs/guides/dropping-down.md) -------------------


async def test_replaced_read_schema_shapes_responses_and_expand(client):
    response = await client.post("/api/blog/profile", json={"name": "ana", "bio": "hidden"})
    assert response.status_code == 201
    assert response.json() == {"id": 1, "name": "ana"}
    await client.post("/api/blog/card", json={"profile_id": 1})
    card = (await client.get("/api/blog/card/1", params={"expand": "profile"})).json()
    assert card["profile"] == {"id": 1, "name": "ana"}
