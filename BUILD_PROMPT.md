# fyro: Build Prompts

> **Status: FINAL (v0.1, Phases 0 to 8, plus the Phase 6.1 fix).**
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


## Phase 7 prompt: CLI

Execute **Phase 7** of `PLAN.md` ("CLI"). The Phase 6 gate passed: `fyro.migrations.makemigrations()` / `migrate(target)` exist as sync functions. Every command here is a thin wrapper over something already built. The CLI adds **no new framework behavior**.

**Goal:** the `PLAN.md` success test, end to end, from an empty folder, in under 10 minutes, with no glue code and no decisions about where files go.

**Public API**

```bash
fyro new mysite              # create a project (refuses to overwrite anything)
cd mysite
docker compose up -d         # Postgres for this project (compose.yaml written by `new`)
uv run manage.py startapp blog
# write blog/models.py, add "blog" to installed_apps
uv run manage.py makemigrations [-m "message"]
uv run manage.py migrate [target]        # target: revision id, "head" (default) or "zero"
uv run manage.py runserver [--host 127.0.0.1] [--port 8000] [--no-reload]
```

`fyro <command>` works the same as `manage.py <command>` whenever `FYRO_SETTINGS` is set. `manage.py` only sets it.

**`fyro new mysite` writes (the `PLAN.md` north star):**

```
mysite/
  manage.py           # os.environ.setdefault("FYRO_SETTINGS", "mysite.settings"); fyro.cli.main()
  asgi.py             # same setdefault; app = fyro.create_app()
  .env                # FYRO_SECRET_KEY=fyro-insecure-<random>, FYRO_DEBUG=true, FYRO_DATABASE_URL=...
  .gitignore          # .env, .venv/, __pycache__/
  compose.yaml        # Postgres 16, healthcheck, named volume, host port "${FYRO_DB_PORT:-5432}:5432"
  mysite/
    __init__.py
    settings.py       # class ProjectSettings(fyro.Settings): installed_apps: list[str] = []
```

`migrations/` is not created by `new`. The first `makemigrations` creates it (Phase 6).

**Rules**
- **Typer app** in `src/fyro/cli.py`. It is exposed as the `fyro` console script (`[project.scripts] fyro = "fyro.cli:main"`) and called by the generated `manage.py`.
- **Errors.** `ImproperlyConfigured` and `TypeError` from model definitions print one red line, with no traceback, and exit with code 1. Any other exception keeps its traceback.
- **Project commands** (`startapp`, `makemigrations`, `migrate`, `runserver`) put the current directory on `sys.path` first, so `fyro <cmd>` from the project root imports the project. That already happens for `python manage.py`.
- **`new <name> [directory]`.**
  - `name` must be a valid Python identifier and must not shadow an importable module (`fyro new json` is an error, as in Django `startproject`).
  - Any file it would write that already exists is an error, and nothing is written. It never partially overwrites.
  - The secret key is `fyro-insecure-` + `secrets.token_urlsafe(50)`. The Phase 1 rule then warns in debug and errors in production, by design.
  - It prints the next steps.
- **`startapp <name>`.** It creates `<name>/__init__.py` and `<name>/models.py` (`from fyro import Field, Model` with a commented example). It writes no `routes.py`, because CRUD is automatic. It **does not edit `settings.py`** (Django behavior); it prints the line to add to `installed_apps`. The same name rules as `new` apply, and an existing directory is an error.
- **App not installed.** `makemigrations` and `runserver` print a warning for every directory in the project root that holds a `models.py` but is not in `installed_apps`: "`blog/` looks like an app but is not in installed_apps (`mysite/settings.py`)". This catches the most common Django beginner mistake without editing user code.
- **`makemigrations [-m]`** prints the new file path, or `No changes detected` when it returns `None`. Alembic's own stdout and INFO logs are silenced (Alembic `Config(stdout=...)`, the logging level) unless `--verbose` is given.
- **`migrate [target]`** prints the revisions applied or unapplied, then `Database is at <rev>` (or `empty`).
- **`runserver`** runs uvicorn on `asgi:app`, binding 127.0.0.1 by default, with auto-reload on by default (Django `runserver`, `fastapi dev`) and `--no-reload` to turn it off. It prints the `/docs` URL. When unapplied migrations exist it prints a warning line ("You have unapplied migrations; run migrate"), as Django does, without failing.

**Steps and checks**
1. Check the current docs: Typer (commands, options, `typer.testing.CliRunner`, exit codes), `[project.scripts]` with `uv_build`, `uvicorn.run(..., reload=True)` (needs an import string), and Alembic `Config(stdout=...)`.
2. Write failing tests:
   - `CliRunner` for every command's output and exit code;
   - `new` writing the exact tree, plus the refusals (bad name, shadowed module, existing files) leaving nothing behind;
   - the `.env` key prefix and length;
   - `.gitignore` containing `.env`;
   - `startapp` output and its refusals;
   - `makemigrations` "No changes detected";
   - `migrate zero` and back;
   - an error being one line with exit code 1 and no traceback;
   - `fyro` vs `manage.py` parity;
   - the unapplied-migrations warning.

   Check: the tests fail.
3. Implement. Check: all tests pass (Phases 0 to 6 still green) and `uv run ruff check` is clean.
4. **Scripted end-to-end test (the success test).** A subprocess script in a temp dir runs:
   1. `fyro new mysite`, with the DB URL pointed at a dedicated test database;
   2. `startapp blog`;
   3. it writes the north-star `Post`, adds `"blog"`, then runs `makemigrations` and `migrate`;
   4. it starts `runserver` and polls until it is up;
   5. it calls `POST`/`GET /api/blog/post` and `/docs`;
   6. it stops the server and times each step.

   Mark it as a slow test.
5. **Gate.** Run the end-to-end flow by hand in a scratch dir and show every command with its output, the generated files, and the API calls. **Stop and ask the user to approve before Phase 8.**

**Locked decisions (researched against Django, Rails 7.2, Laravel Sail and the FastAPI CLI):**
1. **`createsuperuser` moves to Phase 8.** In Django it ships with the auth app; fyro has no `User` until Phase 8.
2. **`new` writes `compose.yaml` by default.** fyro is Postgres-only, so there is no database to choose (Rails makes it opt-in only because it supports many). The host port is overridable from the shell, Sail-style.
   - The variable cannot live in `.env`: Phase 1 forbids unknown keys there.
   - The next steps printed by `new` mention the override.
3. **`FYRO_DEBUG=true` in `.env`.** This mirrors Django `startproject` (`DEBUG = True` plus a `django-insecure-` key). `Settings.debug` still defaults to `False`, so a server without this `.env` is safe.
4. **`runserver` reloads by default and binds 127.0.0.1,** like `fastapi dev`.
5. **`startapp` prints the `installed_apps` line and never edits `settings.py`,** plus the app-not-installed warning above.
6. **Both entry points:** the `fyro` console script and the generated `manage.py`, Django-style.

**Out of scope:**
- `createsuperuser` (Phase 8);
- `shell`, `dbshell`, `test` and `merge` commands;
- project templates and options (`--template`);
- shell completion;
- Windows-specific handling beyond what Typer and uvicorn give.

---

## Phase 8 prompt: Auth and permissions

Execute **Phase 8** of `PLAN.md` ("Auth"). The Phase 7 gate passed. Phase 6.1 added the rule that `makemigrations` never writes a migration that fails on a table with rows. `createsuperuser` moved here from Phase 7.

**Goal:** add `"fyro.auth"` to `installed_apps` and get:
- users;
- login and logout;
- the current user in any route;
- per-model permissions on auto CRUD;
- `createsuperuser`.

All of it with no auth code in the project.

**Public API**

```python
# settings.py
installed_apps: list[str] = ["fyro.auth", "blog"]

# blog/models.py
class Post(Model):
    title: str = Field(max_length=200)

    class Meta:
        permissions = {"read": "public", "write": "superuser"}   # optional; see defaults below

# blog/routes.py: a hand-written route that needs a user
from fastapi import APIRouter, Depends
from fyro.auth import User, current_user

router = APIRouter()

@router.get("/mine")
async def mine(user: User = Depends(current_user)):   # 401 when not logged in
    ...
```

```bash
python manage.py makemigrations && python manage.py migrate   # creates auth_user and auth_session
python manage.py createsuperuser                              # prompts; --noinput reads env vars
```

| Method and path | Input | Output |
|---|---|---|
| `POST /api/auth/login` | JSON `{"username", "password"}` | `200 {"token", "token_type": "bearer", "expires_at"}`, plus the same token as a session cookie; `401 {"detail": "Invalid username or password."}`, identical for an unknown user, a wrong password and an inactive user |
| `POST /api/auth/logout` | cookie or bearer | `204`; the session row is deleted and the cookie cleared |
| `GET /api/auth/me` | cookie or bearer | `200 UserRead` (no password), or `401` |

**Locked decisions (researched against OWASP, Django, Django REST Framework and the FastAPI security tutorial):**
1. **Hashing: Argon2id through `pwdlib[argon2]`** (`PasswordHash.recommended()`). OWASP's first choice, with a minimum of 19 MiB memory, 2 iterations and parallelism 1. Verify that pwdlib's defaults meet that minimum. bcrypt is legacy only.
2. **Sessions: a database table, not signed cookies.** Logout and deactivation take effect immediately (Django's default backend). Signed cookies stay valid after logout once stolen (Django ticket #24799).
3. **No JWT.** **One opaque session token** is sent as a cookie for browsers or as `Authorization: Bearer <token>` for API clients and scripts.
   - JWTs cannot be revoked before they expire without a deny-list or refresh tokens, which bring back the database lookup that fyro does anyway.
   - Dropping them removes `alg: none` and algorithm-confusion risk, and a leaked `secret_key` cannot forge sessions.
   - JWT can be added later as an option for multi-service setups. Clients won't change, since they already send `Bearer`.
4. **Default permissions with `fyro.auth` installed: read is public, write needs login** (DRF's `IsAuthenticatedOrReadOnly`).
   - Until ownership checks exist (relations, v0.2), "logged in" means any account can write any row.
   - That is acceptable in v0.1 only because there is no public sign-up.
5. **Baseline CSRF protection is built now.** OWASP: `SameSite` is defense in depth, not a fix on its own, and custom request headers are its recommended defense for API-driven sites. HTMX sends `HX-Request` by itself, so the Phase 9 admin works unchanged.
6. **Login by username** (Django's default). `email` is optional and not unique. There is no custom user model in v0.1, so this choice sticks.

**Rules**
- **`fyro.auth` is an ordinary installed app inside the fyro package.** Its label is `auth`, its tables are `auth_user` and `auth_session`, and they go into the project's single migration history. Install order in `installed_apps` does not matter.
- **`User`:**
  - `username: str` (unique, max 150, compared exactly, case-sensitive as in Django);
  - `email: str | None = None`;
  - `password: str` (stores the **Argon2 hash** and never appears in any schema, response, log or OpenAPI);
  - `is_active: bool = True`;
  - `is_superuser: bool = False`;
  - `date_joined: datetime = Field(default_factory=datetime.now)`.

  `Meta.api = False`: there is no CRUD for users in v0.1.
- **`Session`:**
  - `token_hash: str` (unique; the SHA-256 of the token, so a leaked table leaks no live sessions);
  - `user_id: int` (indexed; a plain column, because relations come in v0.2);
  - `created_at`;
  - `expires_at`.

  `Meta.api = False`.
- **Tokens:**
  - `secrets.token_urlsafe(32)`, created fresh at every login, which also prevents session fixation.
  - The lifetime is 14 days (Django's `SESSION_COOKIE_AGE`), as a constant in v0.1.
  - An expired session is deleted when it is next used and gives 401.
  - Cleaning up expired sessions in bulk is deferred.
- **Cookie:** `fyro_session`, with:
  - `HttpOnly`;
  - `SameSite=Lax`;
  - `Secure` unless `debug`;
  - `Path=/`;
  - `Max-Age` equal to the lifetime.
- **Login:**
  - Usernames are looked up exactly.
  - An unknown user is verified against a precomputed **dummy hash**, so timing does not reveal which usernames exist (the FastAPI tutorial's advice).
  - An inactive user gets the same 401 as a wrong password.
  - The login endpoint accepts JSON only.
- **`current_user` / `optional_user`** are FastAPI dependencies exported from `fyro.auth`.
  - They read `Authorization: Bearer` first, then the cookie.
  - They hash the token and look up an unexpired session, then an active user.
  - `current_user` raises 401 and `optional_user` returns `None`.
  - A bad, guessed, expired or logged-out token is a 401. The response never says which.
- **CSRF:**
  - A request **authenticated by the cookie** using POST, PUT, PATCH or DELETE must carry an `X-Requested-With` or `HX-Request` header. Otherwise it gets `403 {"detail": "CSRF check failed: send an X-Requested-With header."}`.
  - Bearer-authenticated requests are exempt, because browsers never attach them automatically.
  - Unauthenticated requests are exempt because they have nothing to forge.
- **Permissions:**
  - `Meta.permissions` maps the actions `"read"` (list, get) and `"write"` (create, patch, delete) to the levels `"public"`, `"authenticated"` and `"superuser"`.
  - Either key may be omitted; the default for that action applies.
  - Auto CRUD enforces it: 401 when not logged in, 403 when logged in but not allowed.
  - **The permission check runs before the row is loaded.** A request that isn't allowed gets 401/403 even for an id that does not exist, so nobody can probe which ids exist (id enumeration). 404 is only for callers who are allowed to see the model.
  - Unknown actions or levels raise `TypeError` at class definition, as other `Meta` errors do.
  - Without `fyro.auth` installed:
    - any model with `Meta.permissions` makes `create_app()` raise `ImproperlyConfigured`, saying to install `fyro.auth`;
    - CRUD stays open, as in Phase 5;
    - `create_app()` logs one warning when `debug` is off: "API writes are open to anyone; add fyro.auth to installed_apps".
- **`createsuperuser`:**
  - It is a CLI command that needs `fyro.auth` installed. Otherwise it gives a one-line error.
  - It prompts for username, email (optional) and password twice, with `getpass`.
  - `--noinput` reads `FYRO_SUPERUSER_USERNAME`, `FYRO_SUPERUSER_EMAIL` and `FYRO_SUPERUSER_PASSWORD` from the **environment**. They cannot go in `.env`, which Phase 1 limits to settings fields.
  - It refuses an existing username, an empty password and mismatched passwords.
- **No secrets anywhere:** password hashes, raw tokens and token hashes never appear in responses (except the token in the login response), logs, error messages or OpenAPI.

**Layout:**
- `src/fyro/auth/` holds:
  - `__init__.py` (`User`, `current_user`, `optional_user`);
  - `models.py`;
  - `routes.py`;
  - `passwords.py`;
  - `sessions.py`.
- Small edits:
  - `crud.py` (permission and CSRF checks);
  - `models/base.py` (the `Meta.permissions` key);
  - `app.py` (checks and the warning);
  - `cli.py` (`createsuperuser`).
- Add `pwdlib[argon2]` to the runtime dependencies. There is **no** PyJWT.

**Steps and checks**
1. Check the current docs:
   - the FastAPI security tutorial (`OAuth2PasswordBearer`-style dependencies, the dummy-hash advice);
   - pwdlib (its defaults against OWASP's Argon2id minimum);
   - the OWASP cheat sheets for password storage, session management and CSRF;
   - Starlette `Response.set_cookie` / `delete_cookie`;
   - Python `secrets` and `hmac.compare_digest`.
2. **Spike.** Prove these things:
   - `fyro.auth`, an installed app inside the fyro package, loads through the Phase 2 loader;
   - its models join the project's migration history (`makemigrations` creates `auth_user` and `auth_session`);
   - a CLI command can require it.

   **Stop and report.**
3. Write failing tests:
   - **Login:** success; failure with the same 401 body for an unknown user, a wrong password and an inactive user; the dummy hash verified for unknown users; JSON only.
   - **Sessions:**
     - a token works as a cookie and as a bearer header;
     - the token hash is stored, never the raw token;
     - a fresh token on each login;
     - logout makes the old token 401 at once;
     - an expired session gives 401 and its row is deleted;
     - a deactivated user gives 401;
     - a guessed or tampered token gives 401.
   - **Cookie flags.**
   - **CSRF:** a cookie write without the header gets 403; with `X-Requested-With` or `HX-Request` it passes; a bearer write needs no header.
   - **Dependencies:** `/me`, and `current_user` / `optional_user` in a hand-written route.
   - **Permission matrix:** every action and level × anonymous, user and superuser gives 401, 403 or 200/201/204; the defaults with no `Meta.permissions`; `Meta.permissions` validation errors; `Meta.permissions` without `fyro.auth` installed raises `ImproperlyConfigured`; the open-writes warning appears only with debug off.
   - **`createsuperuser`:** interactive, `--noinput`, a duplicate, a mismatch, an empty password, and running without `fyro.auth`.
   - **No leaks:** a hash never in any response or OpenAPI schema.
   - **Migrations:** they create the `auth_*` tables.
4. Implement. Check: all tests pass (Phases 0 to 7 still green) and `uv run ruff check` is clean.
5. **Gate.** Use a scratch project with `fyro.auth` and `blog`. Then:
   1. run `createsuperuser`;
   2. log in through `/docs` and through curl with a bearer token;
   3. an anonymous write gets 401;
   4. a logged-in write succeeds;
   5. a cookie write without the header gets 403;
   6. a non-superuser on a superuser-only model gets 403;
   7. log out, and the old token gets 401;
   8. show the `auth_user` row (hashed password) and the `auth_session` row (hash only).

   **Stop and ask the user to approve before Phase 9.**

**Out of scope:**
- OAuth and social login;
- password reset, password change and email;
- JWT and refresh tokens;
- rate limiting (in PLAN's "Not building");
- object-level and ownership permissions (relations, v0.2);
- groups and roles beyond superuser;
- a custom user model;
- user-management endpoints and sign-up;
- a command that clears expired sessions.

---

## Phases 9 to 10 prompts

Written after the Phase 8 gate passes, using the outline in `PLAN.md`. Each will follow the same shape: goal, public API, tests first, step checks, gate.
