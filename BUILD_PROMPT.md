# fyro: Build Prompts

> **Status: FINAL (v0.1, Phases 0 to 6).**
> How to use: paste the **Master prompt** at the start of every build session, then paste exactly **one** phase prompt. One phase per session.

---

## Master prompt

You are building **fyro**, a web framework on top of FastAPI that feels like Django. FastAPI is the engine. fyro adds project structure, a single-source Model class, a hidden DB session, auto CRUD, migrations, a CLI, auth and an admin.

Read `IDEA.md` and `PLAN.md` before doing anything. `PLAN.md` "Locked decisions" are final. Do not change them. If one blocks you, stop and ask.

**Rules**
- The API is the product. Match the public API in `PLAN.md` exactly.
- Wrap, do not rewrite. Use Pydantic, SQLAlchemy 2 async, Alembic and FastAPI. Do not build on top of SQLModel.
- Enforce opinions in code. Wrong structure raises a clear error that names the file, app or field and says how to fix it.
- Simplicity first: write the minimum code for the current phase. No features from later phases, no speculative options.
- Before writing code that uses a library, check its current docs (WebFetch / context7).
- Tests first: write failing tests, then implement, then show them passing.
- Postgres via `docker compose up -d` for any DB test.
- Only touch files the phase needs.
- Do not add "Co-Authored-By" lines to commits.

**Working protocol**
1. Restate the phase goal and list the steps with their verify check.
2. Execute step by step. After each step, run its check and report the result.
3. At any **spike** or **gate** in the phase, stop and report to the user before continuing.
4. Finish with: files changed, test output, and anything deferred or unclear.

---

## Phase 0 prompt: Foundations

Execute **Phase 0** of `PLAN.md`.
- Create `pyproject.toml` (uv, src layout, Python >= 3.12) with runtime deps: fastapi, pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic, typer, uvicorn, jinja2. Dev deps: pytest, pytest-asyncio, httpx, ruff.
- Create `docker-compose.yml` with Postgres 16, a healthcheck and a named volume.
- Create `src/fyro/__init__.py` and `tests/test_smoke.py`.

Done when `uv sync`, `docker compose up -d` (healthy), `uv run pytest` and `uv run ruff check` all succeed.

---

## Phase 1 prompt: Typed settings

Execute **Phase 1** of `PLAN.md`.
- Build `fyro.Settings` (subclass of pydantic-settings `BaseSettings`, `env_prefix="FYRO_"`, `env_file=".env"`) and `fyro.conf.get_settings()`.
- `secret_key` is required with no default. An insecure key (starts with `fyro-insecure-` or is shorter than 50 characters) triggers `warnings.warn` when `debug=True` and raises an error when `debug=False`.
- `list[str]` fields read from env: a value starting with `[` is parsed as JSON, and any other value is split on commas and stripped. Use pydantic-settings `NoDecode` + `BeforeValidator`, and check its current docs first.
- `get_settings()` imports the module named by `FYRO_SETTINGS`, finds exactly one `Settings` subclass there, instantiates it, and caches it.
- Write the tests listed in Phase 1 first. Each error message must name what is wrong and how to fix it.

Done when all Phase 1 tests pass and ruff is clean.

---

## Phase 2 prompt: App loader

Execute **Phase 2** of `PLAN.md`.
- Build `fyro.apps` (registry + loader) and `fyro.create_app()`.
- Mount each app's `routes.router` at `/api/<app_label>`. Everything API-shaped lives under `/api`, with no `/v1`.
- Create fixture apps under `tests/fixtures/` for: a valid app with `routes.py`, an app missing `models.py`, a `routes.py` without `router`, and duplicate labels.
- Tests first. Use `httpx.AsyncClient` with `ASGITransport` to hit a fixture app's custom route.
- Models are not built yet. Define the registry API that the Model class will call to register itself (`registry.register_model(app_label, model_cls)`) and test it with a stub class.

Done when all Phase 2 tests pass.

---

## Phase 3 prompt: The Model class

Execute **Phase 3** of `PLAN.md`. This is the heart of the product. Correctness and feel matter more than speed.

1. **Spike first.** In a throwaway test, prove that a plain annotation such as `title: str = Field(max_length=200)` can be turned into `Mapped[str]` + `mapped_column(String(200))` before SQLAlchemy 2's declarative mapping runs. Prefer `__init_subclass__` on a `DeclarativeBase` subclass. Use a metaclass only if that fails. **Stop and report the result before continuing.**
2. Write failing tests for every rule under "Schema rules", plus: table name `blog_post`, auto `id`, nullable from `T | None`, the unsupported-type `TypeError`, unknown `Meta` key errors, and `read_only` + `write_only` conflict.
3. Implement `fyro/models/fields.py`, `schemas.py`, `base.py`. Generate schemas with `pydantic.create_model`. Attach them as `Model.Create`, `Model.Read`, `Model.Update`. Register each model with the Phase 2 registry, with app label derived from the defining module (`blog.models` gives `blog`).
4. Verify `metadata.create_all` on Docker Postgres creates the expected table.
5. **Magic gate.** Print for the north-star `Post` model: the `CREATE TABLE` DDL and `model_json_schema()` of Create, Read and Update. Stop and ask the user to approve the feel before anything else.

Do not build the `.objects` manager, sessions or routes. Those come in Phase 4 and later.

---

## Phase 4 prompt: Engine, session and `Model.objects`

Execute **Phase 4** of `PLAN.md` ("Engine + session"). The Phase 3 magic gate passed with these decisions, which are now locked:
- `Update` stays strict: every field defaults to `None`, but the annotation stays `T`. An explicit `null` on a NOT NULL column is rejected.
- Scalar defaults are Python-side only (`mapped_column(default=...)`). Do not emit `server_default`.

**Goal:** the user never writes a `get_db` function. Inside a request, `await Post.objects.filter(title="x").all()` works. In scripts and tests, `async with fyro.db.session():` gives the same API.

**Public API**

```python
from blog.models import Post

post = await Post.objects.create(title="Hi", body="...", secret="s")    # -> Post
posts = await Post.objects.filter(published=True).order_by("-id").limit(10).offset(20).all()
first = await Post.objects.exclude(title="x").first()                     # -> Post | None
one = await Post.objects.get(id=1)          # raises Post.DoesNotExist / Post.MultipleObjectsReturned
n = await Post.objects.filter(published=False).count()                    # -> int
changed = await Post.objects.filter(id=1).update(title="New")             # -> rows updated
removed = await Post.objects.filter(published=False).delete()             # -> rows deleted

import fyro.db
async with fyro.db.session():   # scripts / tests: commit on clean exit, rollback on exception
    await Post.objects.count()
```

**Rules**
- **Querysets.** `Post.objects` is a manager on the class only (accessing it on an instance is an error). Querysets are immutable: every chain method returns a new one. Only `all`, `first`, `get`, `count`, `create`, `update` and `delete` hit the DB, and they are all `async`.
- **Lookups.** `filter` and `exclude` take keyword equality only. `None` becomes `IS NULL`. Lookups such as `__gt` or `__in` are deferred.
- **Field names.** An unknown field name in `filter`, `exclude`, `order_by`, `create` or `update` raises `TypeError` naming `Post.<field>` and listing the valid fields.
- **`order_by("-field")`** sorts descending.
- **Exceptions.** `Post.DoesNotExist` and `Post.MultipleObjectsReturned` are per-model subclasses of `fyro.db.DoesNotExist` and `fyro.db.MultipleObjectsReturned`. They are attached in `Model.__init_subclass__`.
- **`create` and `update` do not validate** through `Post.Create` / `Post.Update`. Validation is the API layer's job (Phase 5).
- **Engine seam.** The manager builds SQLAlchemy statements and only calls the engine:
  - `fetch(stmt) -> list` returns ORM objects for `select(Model)` and scalars for `count`;
  - `execute(stmt) -> int` returns the rowcount.

  `create` uses `insert(Model).values(...).returning(Model)` through `fetch`. `update` and `delete` are bulk statements through `execute`. `SQLAlchemyEngine` is the only implementation. Nothing outside `fyro/db/` imports SQLAlchemy session APIs.
- **Engine lifecycle.**
  - `fyro.db.get_engine()` lazily creates one `AsyncEngine` from `settings.database_url` and caches it.
  - `create_app()` adds a FastAPI `lifespan` that calls `get_engine()` on startup and disposes it on shutdown.
  - Lazy creation keeps httpx `ASGITransport` tests working, because they do not run lifespan.
- **Request session.**
  - A pure ASGI middleware (not `BaseHTTPMiddleware`) opens one `AsyncSession` per HTTP request and stores it in a `contextvar`.
  - It **commits before the response starts**: it intercepts the first `http.response.start` message, commits, then sends.
  - It rolls back if the endpoint raises. If the commit fails, the client gets a 500, never a 200 for unsaved data.
- **No session.** Using `.objects` with no active session raises `RuntimeError`: "No database session. Inside a request fyro provides one; in scripts and tests use `async with fyro.db.session():`".
- **Nested sessions.** `fyro.db.session()` nested inside an active session reuses the outer one. It does not commit early.

**Layout:** `src/fyro/db/__init__.py` exports `session`, `get_engine`, `DoesNotExist` and `MultipleObjectsReturned`. Engine code goes in `src/fyro/db/engine.py` and session code in `src/fyro/db/session.py`. The manager and queryset go in `src/fyro/models/manager.py`.

**Steps and checks**
1. Check the current docs first:
   - SQLAlchemy 2.1 asyncio (`AsyncSession`, `async_sessionmaker`);
   - ORM-enabled `INSERT ... RETURNING` and bulk `UPDATE`/`DELETE`;
   - FastAPI `lifespan`;
   - pure ASGI middleware and `contextvars`.
2. **Spike.** In a throwaway test, prove two things:
   - a `contextvar` set in a pure ASGI middleware is visible inside an `async def` endpoint and its dependencies;
   - committing on `http.response.start` works with a streaming response.

   **Stop and report before continuing.**
3. Write failing tests. They run against Docker Postgres, with tables created per test module and truncated per test. Cover:
   - every manager method and chain;
   - immutability;
   - `get` with 0 and with 2+ matches;
   - unknown-field `TypeError`;
   - `None` filter becomes `IS NULL`;
   - the no-session `RuntimeError`;
   - `session()` commit, rollback and nesting;
   - request commit;
   - request rollback on exception (row not saved);
   - lifespan dispose;
   - an `Engine` fake that records `fetch`/`execute` calls, proving the manager only uses the seam.
4. Implement. Check: all tests pass (Phases 0 to 3 still green) and `uv run ruff check` is clean.
5. **Gate.** Use a demo project with the north-star `Post` and a hand-written `routes.py`. `POST /api/blog/posts` validates with `Post.Create`, calls `Post.objects.create(**data.model_dump())` and returns `Post.Read`. `GET /api/blog/posts` lists. Show the inputs and outputs through httpx and the rows in Postgres. **Stop and ask the user to approve before Phase 5.**

**Out of scope:**
- auto CRUD routes (Phase 5);
- instance `save()` / `delete()`;
- lookups beyond equality;
- `atomic()` blocks;
- migrations;
- relations.

---

## Phase 5 prompt: Auto CRUD

Execute **Phase 5** of `PLAN.md` ("Auto CRUD"). **This is the demo gate.** The Phase 4 gate passed with one decision for this phase: `create_app()` turns `fyro.db.DoesNotExist` into a 404 for **every** route, including hand-written ones.

**Goal:** a developer writes one model class and gets a full, documented API with no route code.

**Public API**

```python
# blog/models.py: nothing new to write
class Post(Model):
    title: str = Field(max_length=200)
    body: str
    secret: str = Field(write_only=True)
    published: bool = False

class AuditLog(Model):
    message: str

    class Meta:
        api = False          # opt out: no CRUD routes for this model
```

The routes for `Post`. `<pk>` is named after the primary key field (`id` by default), and its type comes from that field:

| Method and path | Input | Output |
|---|---|---|
| `GET /api/blog/post` | query: one equality filter per readable field, plus `limit` (default 50, max 100; above 100 is a 422, not silently capped) and `offset` (default 0) | `200 {"items": [Post.Read], "total": int, "limit": int, "offset": int}`, ordered by pk |
| `GET /api/blog/post/{id}` | | `200 Post.Read`, or `404` |
| `POST /api/blog/post` | body `Post.Create` | `201 Post.Read`, or `409` on a duplicate unique value |
| `PATCH /api/blog/post/{id}` | body `Post.Update`, applied with `exclude_unset=True` | `200 Post.Read`, `404`, or `409` on a duplicate unique value |
| `DELETE /api/blog/post/{id}` | | `204`, or `404` |

**Rules**
- **Path.** The model path segment is the snake_case class name: `Post` gives `post` and `BlogPost` gives `blog_post`. This is the same rule as table names, so one naming rule covers the framework. It reuses `_snake` from `fyro/models/base.py`. (Django/DRF would give `blogpost`; fyro deliberately does not.)
- **Filters.**
  - Only fields in `Post.Read` can be filtered. `write_only` fields never become query params, so they cannot be probed.
  - Each filter is optional and typed from the field.
  - Unknown query params are ignored (FastAPI default).
  - `total` counts the filtered rows, ignoring `limit` and `offset`.
- **Responses** always go through `Post.Read`, so `write_only` fields never leak, including from `POST` and `PATCH`.
- **Path param.** It is named after the primary key field (`{id}` by default, `{slug}` for `slug: str = Field(primary_key=True)`), not a generic `{pk}`. `Post.objects.get(id=1)` and `/post/{id}` use the same name, and OpenAPI shows the real field.
- **List shape.** `{items, total, limit, offset}` matches fastapi-pagination's limit/offset page.
- **Unique violations.** On `POST` and `PATCH` only, catch SQLAlchemy `IntegrityError` whose driver error is a unique violation (SQLSTATE `23505`). Return `409 {"detail": "Post with this <field> already exists."}`. Find `<field>` by matching the violated constraint name to the table's unique constraints and indexes, and fall back to "these values". Other database errors stay 500. The session is aborted, so the 409 (status >= 400) makes the Phase 4 middleware roll back.
- **Writes** use `Post.objects` only. The Phase 4 middleware commits. `PATCH` with an empty body returns the row unchanged.
- **Opting out.** `Meta.api = False` skips the CRUD router. Add `api` to the allowed `Meta` keys in `fyro/models/base.py`. A non-bool value raises `TypeError`.
- **Composite keys.** A model with more than one `primary_key` field and `api` not set to `False` raises `ImproperlyConfigured` at `create_app()`. It names the model and says to set `Meta.api = False`.
- **Route order.** Mount each app's custom `routes.router` **before** its CRUD routers, so `GET /api/blog/post/stats` is not swallowed by `GET /api/blog/post/{id}`.
- **Duplicates.** A duplicate method+path anywhere in the final app (custom or CRUD) raises `ImproperlyConfigured` at `create_app()`. The error names both endpoints as `module.function` and the method+path. Recent FastAPI wraps included routers (`app.routes` held `_IncludedRouter` objects in Phase 3), so check the current docs for how to list the final routes.
- **404 handler.** `create_app()` registers an exception handler: `fyro.db.DoesNotExist` gives `404 {"detail": "<message>"}`. `MultipleObjectsReturned` stays a 500.
- **OpenAPI.** It shows every CRUD route with `PostCreate`, `PostRead`, `PostUpdate` and a list-page schema, tagged `blog.post`.

**Layout:** `src/fyro/crud.py` with `build_router(model) -> APIRouter`. Small edits to `src/fyro/app.py` and `src/fyro/models/base.py`.

**Steps and checks**
1. Check the current docs:
   - asyncpg `UniqueViolationError` (`constraint_name`, SQLSTATE `23505`) as wrapped by SQLAlchemy `IntegrityError.orig`;
   - FastAPI query parameter models (`Annotated[Model, Query()]`) or dynamic dependencies for typed filters;
   - `response_model`, `status_code=204`;
   - exception handlers;
   - how to list routes when routers are included.
2. Write failing tests against Docker Postgres. Cover:
   - each endpoint's happy path and 404;
   - filters, `limit`/`offset`/`total`, and `limit` over 100 giving 422;
   - no `write_only` in any response, and no `write_only` query param;
   - `POST` and `PATCH` with a duplicate unique value giving 409 with the field name, and the row unchanged;
   - `PATCH` with partial data, an empty body, and an explicit `null` on a NOT NULL field giving 422;
   - `Meta.api = False`;
   - the composite pk error;
   - the duplicate route error naming both;
   - a custom route mounted before CRUD;
   - `DoesNotExist` giving 404 from a hand-written route;
   - OpenAPI listing the routes and schemas.

   Check: the tests fail.
3. Implement. Check: all tests pass (Phases 0 to 4 still green) and `uv run ruff check` is clean.
4. **Demo gate.** The north-star project has no `routes.py`, only `blog/models.py`. Run `create_app()` under uvicorn, open `/docs`, and exercise every endpoint through httpx. Show the inputs and outputs and the Postgres rows. Time the steps against the "under 10 minutes" success test (the CLI and migrations come later, so tables are made with `create_all`). **Stop and ask the user to approve before Phase 6.**

**Out of scope:**
- `PUT`;
- bulk endpoints;
- ordering and search query params;
- lookups beyond equality;
- auth and permissions (Phase 8);
- mapping other database errors (foreign key, check), which stay 500.

---

## Phase 6 prompt: Migrations

Execute **Phase 6** of `PLAN.md` ("Migrations"). The Phase 5 gate passed. Phase 5 also added a constraint naming convention to `Model.metadata` (`uq_<table>_<col>`, `ix_<table>_<col>`, `pk_<table>`), so autogenerate can name and drop constraints reliably.

**Goal:** the user changes a model and runs two commands. The database follows, and the user never writes migration code or touches Alembic config. The CLI comes in Phase 7, so Phase 6 delivers the Python functions that the CLI will call.

**Public API**

```python
from fyro import migrations

migrations.makemigrations()            # -> Path of the new file, or None when nothing changed
migrations.makemigrations("add tags")  # optional message for the file name
migrations.migrate()                   # move to the latest revision
migrations.migrate("ae1027a6acf")      # move up or down to that revision
migrations.migrate("zero")             # unapply everything (empty schema)
```

The project layout (from the `PLAN.md` north star):

```
myproject/                 # project root = parent of the package holding settings.py
  myproject/settings.py
  migrations/              # created on first makemigrations; only revision files live here
    2026_10_22_1530-ae1027a6acf_initial.py
    2026_10_23_0912-3c5d0e1b7f2a_add_tags.py
```

**Locked decisions (set before this phase, researched against Django, Rails, Flask-Migrate, Piccolo and Alembic):**
1. **Hidden `env.py`.** fyro ships `env.py` and `script.py.mako` inside the package. The user's `migrations/` folder holds only revision files. This is the Django and Piccolo approach. An "eject" option is deferred until someone asks.
2. **Alembic revision ids** (random hex) stay. Sequential `0001` ids would make two branches that both create `0002` a hard duplicate-id error, while hex ids give two heads that Alembic can merge. Files are prefixed with their creation time so they sort, as Piccolo does: `file_template = %(year)d_%(month).2d_%(day).2d_%(hour).2d%(minute).2d-%(rev)s_%(slug)s`.
3. **One `migrate(target)` moves up or down** to reach the target, like Rails `db:migrate VERSION=` and Django `migrate app 0001`. `"zero"` unapplies everything (Django) and maps to Alembic `base`.
4. **The folder location is derived:** `<project root>/migrations/`, where the project root is the parent directory of the package that contains the `FYRO_SETTINGS` module. There is no new setting.
5. **Enums are stored as VARCHAR, not Postgres `ENUM` types.** The Phase 3 type map changes from `sa.Enum(cls)` to `sa.Enum(cls, native_enum=False, values_callable=<member values>, length=255)`, with no CHECK constraint.
   - The column holds the member **value** (`"draft"`), the same as the API.
   - Pydantic validates values. The database does not, which matches Django `choices`.
   - Adding, removing, renaming or reordering values needs no migration, as long as values stay within 255 characters.
   - This avoids Alembic leaving enum types behind on downgrade, and the `alembic-postgresql-enum` dependency.

**Rules**
- **No `alembic.ini`.**
  - fyro builds `alembic.config.Config()` in code.
  - `script_location` points to `fyro/migrations/template/`.
  - `version_locations` points to the project's `migrations/`.
- **Metadata.** `env.py` loads the project via `get_settings()` and `apps.registry.populate(installed_apps)`, then uses `target_metadata = Model.metadata`. Every installed app's models are included, and nothing else.
- **Async.** `env.py` follows Alembic's asyncio recipe (`create_async_engine(settings.database_url, poolclass=NullPool)`, `connection.run_sync(do_run_migrations)`).
  - `makemigrations` and `migrate` are **sync** functions, as Alembic commands are, so the Phase 7 CLI can call them directly.
  - Called from inside a running event loop, they raise `RuntimeError`: "call from sync code or use `await asyncio.to_thread(...)`".
- **Messages.** The default message is `initial` for the first revision and `auto` after that.
- **No empty migrations.** When autogenerate finds no changes, no file is written and `makemigrations()` returns `None`. Do this with `process_revision_directives`, passed to `command.revision` or set in `env.py`. Programmatic configs have no `cmd_opts`, so the cookbook's `cmd_opts.autogenerate` check does not apply as written.
- **Type comparison.** Autogenerate compares types (`compare_type=True`). There is no `server_default` comparison, because defaults are Python-side only (Phase 3 gate). A fresh `migrate()` must leave `command.check` with nothing to do.
- **Renames show up as drop plus add.** That loses data. `makemigrations()` logs a warning naming the table and columns whenever one revision drops and adds columns on the same table. The README documents this.
- **Errors** raise `ImproperlyConfigured` and say how to fix the problem:
  - `migrate()` with no `migrations/` directory: run `makemigrations` first.
  - An unknown target revision.
  - Two heads (a branch): name both and say how to merge (`alembic merge`; a fyro command for it is deferred to Phase 7).

**Layout:**
- `src/fyro/migrations/__init__.py` holds `makemigrations` and `migrate`, plus a private `_config()`.
- `src/fyro/migrations/template/env.py` and `script.py.mako` ship as package data. Verify that `uv_build` puts the `.mako` file in the wheel.
- `src/fyro/models/base.py` gets the enum mapping change, and the Phase 3 tests are updated to the new mapping.

**Steps and checks**
1. Check the current docs:
   - Alembic `Config` without an ini file (`set_main_option`, `%%` escaping, `script_location`, `version_locations`, `file_template`);
   - `command.revision(autogenerate=True, process_revision_directives=...)`, `upgrade`, `downgrade`, `check`, and `ScriptDirectory` (for heads and for up/down direction);
   - the asyncio cookbook;
   - SQLAlchemy `Enum(native_enum=False, values_callable=..., length=...)`;
   - `uv_build` package data.
2. **Spike.** In a throwaway directory against Docker Postgres, prove these things:
   - `Config()` with no ini file, with `script_location` and `version_locations` in different directories, can autogenerate, upgrade, check and downgrade;
   - `file_template` gives a date-prefixed file name;
   - the empty-migration directive writes nothing;
   - a non-native Enum column round-trips with no spurious diff in `check`;
   - `uv build` puts `template/script.py.mako` in the wheel.

   **Stop and report before continuing.**
3. Write failing tests. They use a temporary project dir and a **dedicated database per test module**, created and dropped with an autocommit connection, so `alembic_version` never touches other tests. Cover:
   - the first `makemigrations` gives `*_initial.py`, which creates every installed model's table;
   - running it again with no changes gives `None`;
   - adding a field gives `*_auto.py` with only that column;
   - `migrate()` twice is a no-op;
   - after `migrate()`, `command.check` passes;
   - `migrate(<first rev>)` downgrades and `migrate("zero")` empties the schema;
   - `migrate(<head>)` from `zero` goes back up;
   - unique constraints and indexes keep the naming-convention names;
   - a rename produces drop plus add and a warning;
   - the error cases above;
   - calling from a running loop raises `RuntimeError`;
   - an Enum column is `VARCHAR(255)`, stores the value, and adding an enum member needs no migration (`makemigrations()` returns `None`).
4. Implement. Check: all tests pass (Phases 0 to 5 still green, with the Phase 3 enum test updated to the new mapping) and `uv run ruff check` is clean.
5. **Gate.** Use a demo project with only `blog/models.py`. Then:
   1. run `makemigrations` and show the initial file content;
   2. run `migrate`;
   3. show the tables with `psql \d`;
   4. add a field, run `makemigrations` and `migrate`;
   5. run `makemigrations` again, which reports nothing;
   6. migrate back to the first revision, showing the column is gone;
   7. hit the Phase 5 CRUD API to prove the app works on a migrated schema.

   **Stop and ask the user to approve before Phase 7.**

**Out of scope:**
- the CLI commands themselves (Phase 7);
- a merge command;
- data migrations;
- squashing;
- multiple databases;
- per-app histories;
- rename detection;
- warning at startup about unapplied migrations;
- an `env.py` eject;
- switching the existing test suite from `create_all` to `migrate` (`test.md` asks for this after Phase 6; do it as a separate step once approved).

---


## Phases 7 to 10 prompts

Written after the Phase 6 gate passes, using the outline in `PLAN.md`. Each will follow the same shape: goal, public API, tests first, step checks, gate.
