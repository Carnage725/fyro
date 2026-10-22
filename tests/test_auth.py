"""fyro.auth against real Postgres: login, session tokens, CSRF and the permission matrix."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from support import MigratedSchema

import fyro.db
from fyro import Field, Model, apps, create_app
from fyro.apps import Registry
from fyro.conf import get_settings
from fyro.db import engine as db_engine

pytestmark = pytest.mark.asyncio(loop_scope="module")

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE_URL = os.environ.get(
    "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
)
PASSWORD = "correct horse battery staple"
CSRF = {"X-Requested-With": "fetch"}
COOKIE = "__Host-fyro_session"  # fixture settings run with debug off


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
        from fyro.auth import User, current_user, optional_user

        class Post(Model):
            __module__ = "blog.models"
            title: str = Field(max_length=200)

        class Secret(Model):
            __module__ = "blog.models"
            value: str

            class Meta:
                permissions = {"read": "superuser", "write": "superuser"}  # noqa: RUF012

        custom = APIRouter()

        @custom.get("/mine")
        async def mine(user: Annotated[User, Depends(current_user)]):
            return {"username": user.username}

        @custom.get("/maybe")
        async def maybe(user: Annotated[User | None, Depends(optional_user)]):
            return {"username": user.username if user else None}

        @custom.post("/touch")
        async def touch(user: Annotated[User, Depends(current_user)]):
            return {"username": user.username}

        apps.registry.apps["blog"].routes_module = SimpleNamespace(router=custom)
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
            text("TRUNCATE auth_user, auth_session, blog_post, blog_secret RESTART IDENTITY")
        )
    # https: the production cookie is Secure, and httpx only sends Secure cookies over https
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


async def make_user(username="alice", *, superuser=False, active=True):
    from fyro.auth import User
    from fyro.auth.passwords import hash_password

    async with fyro.db.session():
        return await User.objects.create(
            username=username,
            password=hash_password(PASSWORD),
            is_superuser=superuser,
            is_active=active,
        )


async def login(client, username="alice", password=PASSWORD):
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def sql(statement):
    async with fyro.db.get_engine().begin() as conn:
        result = await conn.execute(text(statement))
        return result.all() if result.returns_rows else None


# --- login -----------------------------------------------------------------


async def test_login_returns_token_and_sets_hardened_cookie(client):
    await make_user()
    response = await client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"token", "token_type", "expires_at"} and body["token_type"] == "bearer"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}={body['token']};")
    for flag in ("HttpOnly", "Secure", "Path=/", "SameSite=lax", "Max-Age=1209600"):
        assert flag in cookie
    assert "Domain" not in cookie


async def test_debug_uses_plain_cookie_name_without_secure(client, monkeypatch):
    await make_user()
    monkeypatch.setenv("FYRO_DEBUG", "true")
    get_settings.cache_clear()
    try:
        response = await client.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        )
    finally:
        monkeypatch.delenv("FYRO_DEBUG")
        get_settings.cache_clear()
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("fyro_session=") and "Secure" not in cookie


@pytest.mark.parametrize(
    ("username", "password"),
    [("nobody", PASSWORD), ("alice", "wrong password"), ("inactive", PASSWORD)],
    ids=["unknown-user", "wrong-password", "inactive-user"],
)
async def test_login_failures_are_indistinguishable(client, username, password):
    await make_user()
    await make_user("inactive", active=False)
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password."}
    assert "set-cookie" not in response.headers


async def test_unknown_user_still_pays_for_a_hash(client):
    # bug: skipping the hash for unknown users lets response time enumerate usernames
    await make_user()

    async def timed(username):
        start = time.perf_counter()
        await client.post("/api/auth/login", json={"username": username, "password": "x"})
        return time.perf_counter() - start

    known = min([await timed("alice") for _ in range(3)])
    unknown = min([await timed("nobody") for _ in range(3)])
    assert unknown > known * 0.3  # same order of magnitude; without the dummy hash it is ~0


async def test_login_accepts_json_only(client):
    await make_user()
    response = await client.post("/api/auth/login", data={"username": "alice", "password": PASSWORD})
    assert response.status_code == 422


# --- session tokens --------------------------------------------------------


async def test_token_works_as_bearer_and_as_cookie(client):
    await make_user()
    token = await login(client)  # also stores the cookie in the client
    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "alice"
    client.cookies.clear()
    me = await client.get("/api/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert "password" not in me.json()


async def test_only_the_token_hash_is_stored(client):
    await make_user()
    token = await login(client)
    rows = await sql("SELECT token_hash FROM auth_session")
    assert rows == [(hashlib.sha256(token.encode()).hexdigest(),)]
    assert token not in json.dumps([list(map(str, row)) for row in await sql("SELECT * FROM auth_session")])


async def test_each_login_issues_a_new_token(client):
    await make_user()
    first, second = await login(client), await login(client)
    assert first != second
    client.cookies.clear()
    for token in (first, second):
        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200


async def test_logout_kills_the_token_and_clears_the_cookie(client):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    response = await client.post("/api/auth/logout", headers=bearer(token))
    assert response.status_code == 204
    assert COOKIE in response.headers["set-cookie"] and 'Max-Age=0' in response.headers["set-cookie"]
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401
    assert await sql("SELECT count(*) FROM auth_session") == [(0,)]


@pytest.mark.parametrize("make_bad", [lambda t: "garbage", lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"), lambda t: ""], ids=["guessed", "tampered", "empty"])
async def test_bad_tokens_are_401(client, make_bad):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    response = await client.get("/api/auth/me", headers=bearer(make_bad(token)))
    assert response.status_code == 401


async def test_absolute_expiry(client):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    await sql("UPDATE auth_session SET expires_at = now() - interval '1 second'")
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401


async def test_idle_timeout(client):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    await sql("UPDATE auth_session SET last_seen = now() - interval '119 minutes'")
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200
    await sql("UPDATE auth_session SET last_seen = now() - interval '121 minutes'")
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401


async def test_last_seen_written_at_most_once_a_minute(client):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    await sql("UPDATE auth_session SET last_seen = now() - interval '5 minutes'")
    await client.get("/api/auth/me", headers=bearer(token))
    ((first,),) = await sql("SELECT last_seen FROM auth_session")
    await client.get("/api/auth/me", headers=bearer(token))
    ((second,),) = await sql("SELECT last_seen FROM auth_session")
    assert first == second  # touched once, then throttled
    assert await sql("SELECT now() - last_seen < interval '1 minute' FROM auth_session") == [(True,)]


async def test_deactivated_user_is_401_on_next_request(client):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    await sql("UPDATE auth_user SET is_active = false")
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401


# --- CSRF ------------------------------------------------------------------


async def test_cookie_write_without_header_is_403(client):
    await make_user()
    await login(client)
    response = await client.post("/api/blog/post", json={"title": "x"})
    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF check failed: send an X-Requested-With header."}
    assert await sql("SELECT count(*) FROM blog_post") == [(0,)]


@pytest.mark.parametrize("header", [{"X-Requested-With": "fetch"}, {"HX-Request": "true"}])
async def test_cookie_write_with_header_passes(client, header):
    await make_user()
    await login(client)
    assert (await client.post("/api/blog/post", json={"title": "x"}, headers=header)).status_code == 201


async def test_bearer_write_needs_no_csrf_header(client):
    await make_user()
    token = await login(client)
    client.cookies.clear()
    response = await client.post("/api/blog/post", json={"title": "x"}, headers=bearer(token))
    assert response.status_code == 201


async def test_bearer_wins_over_cookie(client):
    await make_user()
    token = await login(client)  # cookie still set
    response = await client.post("/api/blog/post", json={"title": "x"}, headers=bearer(token))
    assert response.status_code == 201


async def test_csrf_applies_to_hand_written_routes(client):
    await make_user()
    await login(client)
    assert (await client.post("/api/blog/touch")).status_code == 403
    assert (await client.post("/api/blog/touch", headers=CSRF)).status_code == 200


async def test_logout_by_cookie_needs_csrf_header(client):
    await make_user()
    await login(client)
    assert (await client.post("/api/auth/logout")).status_code == 403
    assert (await client.post("/api/auth/logout", headers=CSRF)).status_code == 204


# --- dependencies in hand-written routes -----------------------------------


async def test_current_user_and_optional_user(client):
    await make_user()
    assert (await client.get("/api/blog/mine")).status_code == 401
    assert (await client.get("/api/blog/maybe")).json() == {"username": None}
    await login(client)
    assert (await client.get("/api/blog/mine")).json() == {"username": "alice"}
    assert (await client.get("/api/blog/maybe")).json() == {"username": "alice"}


async def test_unauthenticated_has_www_authenticate_header(client):
    response = await client.get("/api/blog/mine")
    assert response.headers.get("www-authenticate") == "Bearer"


# --- permission matrix -----------------------------------------------------

READ, WRITE = ("GET", "/{model}", None), ("POST", "/{model}", {"title": "x", "value": "x"})


async def as_who(client, who):
    if who == "anonymous":
        return {}
    await make_user(who, superuser=(who == "root"))
    token = await login(client, who)
    client.cookies.clear()
    return bearer(token)


@pytest.mark.parametrize(
    ("model", "action", "who", "status"),
    [
        # Post: no Meta.permissions -> default read public, write authenticated
        ("post", READ, "anonymous", 200),
        ("post", WRITE, "anonymous", 401),
        ("post", WRITE, "alice", 201),
        # Secret: read and write superuser only
        ("secret", READ, "anonymous", 401),
        ("secret", READ, "alice", 403),
        ("secret", READ, "root", 200),
        ("secret", WRITE, "anonymous", 401),
        ("secret", WRITE, "alice", 403),
        ("secret", WRITE, "root", 201),
    ],
)
async def test_permission_matrix(client, model, action, who, status):
    method, path, body = action
    headers = await as_who(client, who)
    response = await client.request(
        method, "/api/blog" + path.format(model=model), json=body if body else None, headers=headers
    )
    assert response.status_code == status, response.text


@pytest.mark.parametrize(("who", "status"), [("anonymous", 401), ("alice", 403), ("root", 404)])
async def test_permission_checked_before_row_lookup(client, who, status):
    # no id enumeration: callers who may not read Secret never learn whether id 999 exists
    headers = await as_who(client, who)
    for method in ("GET", "PATCH", "DELETE"):
        response = await client.request(
            method, "/api/blog/secret/999", headers=headers, json={} if method == "PATCH" else None
        )
        assert response.status_code == status, (method, response.text)


async def test_no_password_hash_in_any_response_or_openapi(client, app):
    await make_user(superuser=True)
    token = await login(client)
    bodies = [
        (await client.get("/api/auth/me", headers=bearer(token))).text,
        json.dumps(app.openapi()),
    ]
    assert not any("$argon2" in body for body in bodies)
    read_schema = app.openapi()["components"]["schemas"]["UserRead"]
    assert "password" not in read_schema["properties"]
