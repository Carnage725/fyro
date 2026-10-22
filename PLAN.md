# fyro: Build Plan

> **Status: FINAL (v0.1 plan).** Phases 0 to 4 are built and their gates passed. Phase 5 is detailed in `BUILD_PROMPT.md`. Phases 6 to 10 get detailed after the Phase 5 gate.

## Goal

A Django-feeling framework on top of FastAPI. One model class produces the table, the schemas, the API and the admin.

**Success test:** a developer writes one model class, runs one command, and gets a working table, an API with docs and an admin page. It takes under 10 minutes, with no glue code and no decisions about where files go.

## Locked decisions

| Area | Decision |
|---|---|
| Package name | `fyro` |
| Stack | Python 3.12+, uv, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2 async + asyncpg, Alembic, Typer (CLI), Jinja2 + HTMX (admin) |
| Database | Postgres only. No relations until v0.2. |
| Settings | Project `settings.py` holds a `fyro.Settings` subclass. Env vars and `.env` (prefix `FYRO_`) override. |
| List env vars | `list[str]` settings accept JSON (`'["a","b"]'`) or comma-separated (`a,b`) |
| Secret key | Required with no default. `fyro new` writes `FYRO_SECRET_KEY=fyro-insecure-<random>` to a gitignored `.env`. An insecure key gives a warning when `debug=True` and an error when `debug=False`. |
| URLs | All API under `/api`: auto CRUD at `/api/<app>/<model>`, custom app routes at `/api/<app>/...`. Admin at `/admin`, docs at `/docs`. No `/v1` in v0.1. A duplicate method+path is a startup error. |
| Finding settings | `FYRO_SETTINGS=myproject.settings` env var, written into generated `manage.py` / `asgi.py` |
| App layout | `models.py` required. `routes.py` and `admin.py` optional. |
| Field syntax | Typed annotations: `title: str = Field(max_length=200)` |
| Field flags | `read_only` (not in Create/Update) and `write_only` (not in Read) |
| Primary key | Auto-added `id: int` (bigint, autoincrement, read_only) when none is declared |
| Runtime instance | The Model class is a SQLAlchemy-mapped class. `Create`/`Read`/`Update` are separate generated Pydantic classes. It is not a dual Pydantic/SA object (the SQLModel way). |
| Table name | `<app_label>_<model_snake>`, e.g. `blog_post`. `Meta.table_name` overrides. |
| Escape hatch | `Meta` tweaks (`create_exclude`, `read_exclude`, `update_exclude`) plus full replace (`create_schema`, `read_schema`, `update_schema`) |
| Query API | Django-style async manager: `await Post.objects.filter(title="x").all()`. Session comes from a request-scoped contextvar. |
| Engine seam | The manager only calls `engine.fetch(stmt) -> list` (rows/scalars) and `engine.execute(stmt) -> int` (rowcount). Python now, Rust (PyO3/maturin) later, with an identical API. |
| Update schema | Strict: every field defaults to `None`, but the annotation stays `T`. An explicit `null` on a NOT NULL column is rejected. (Phase 3 gate) |
| Defaults | Scalar defaults are Python-side only (`mapped_column(default=...)`). No `server_default`. (Phase 3 gate) |
| Enums | Stored as VARCHAR(255) holding the member value (`native_enum=False`), not Postgres `ENUM` types. No CHECK constraint; Pydantic validates. (Phase 6 decision) |
| Migration files | Hidden `env.py` inside fyro; Alembic hex revision ids with date-prefixed file names; one `migrate(target)` that moves up or down, `"zero"` = empty. (Phase 6 decision) |
| Not found | `create_app()` turns `fyro.db.DoesNotExist` into a 404 on every route, including hand-written ones. (Phase 4 gate) |
| Migrations | One Alembic history in `<project>/migrations/`, wrapped by `makemigrations` / `migrate` |
| Test DB | `docker compose` Postgres |
| Plan depth | Each phase is detailed in `BUILD_PROMPT.md` only after the previous phase's gate passes. |

## North star: the user experience we are building toward

```
myproject/
  manage.py            # sets FYRO_SETTINGS, calls fyro CLI
  .env                 # FYRO_SECRET_KEY etc., gitignored
  asgi.py              # app = fyro.create_app()
  myproject/
    settings.py
  migrations/
  blog/
    __init__.py
    models.py
    routes.py          # optional
    admin.py           # optional
```

```python
# blog/models.py
from fyro import Model, Field

class Post(Model):
    title: str = Field(max_length=200)
    body: str
    secret: str = Field(write_only=True)
    published: bool = False
```

Results: table `blog_post`, plus `Post.Create`, `Post.Read`, `Post.Update`, plus CRUD at `/api/blog/post` and an admin page.

## fyro package layout (target)

```
src/fyro/
  __init__.py      # public API: Settings, Model, Field, create_app
  conf.py          # Settings base + get_settings()
  apps.py          # app loader + registry
  models/
    fields.py      # Field(), FieldInfo with flags
    base.py        # Model base, class-creation hook
    schemas.py     # Create/Read/Update generation
  app.py           # create_app()
tests/
docker-compose.yml
pyproject.toml
```

---

## Phase 0: Foundations

**Goal:** an empty, tested, lintable package.

1. `pyproject.toml` (uv, src layout, Python >= 3.12) with dependencies listed. Verify: `uv sync` succeeds.
2. `docker-compose.yml` with Postgres 16 and a healthcheck. Verify: `docker compose up -d` shows healthy.
3. pytest + pytest-asyncio + ruff configured, plus one smoke test (`import fyro`). Verify: `uv run pytest` and `uv run ruff check` both pass.

**Out of scope:** CI, docs site, publishing.

---

## Phase 1: Typed settings

**Goal:** one settings class that everything reads from.

**Public API**

```python
from fyro import Settings
class ProjectSettings(Settings):
    database_url: str = "postgresql+asyncpg://..."
    installed_apps: list[str] = ["blog"]
```

`fyro.conf.get_settings()` returns the cached instance.

**Base fields:** `database_url` (must use the `postgresql+asyncpg` scheme), `secret_key` (required, no default), `debug: bool = False`, `installed_apps: list[str] = []`.

**Config:** `env_prefix="FYRO_"`, `env_file=".env"`.

**Rules enforced by code**
- `FYRO_SETTINGS` unset gives a clear error that names the variable.
- The module must define exactly one `Settings` subclass. Zero or more than one is an error.
- A non-Postgres URL is an error at load time.
- Env vars `FYRO_<FIELD>` and `.env` override file values.
- `list[str]` fields from env: a value starting with `[` is parsed as JSON. Any other value is split on commas and stripped. This uses pydantic-settings `NoDecode` + `BeforeValidator`.
- Insecure `secret_key` (starts with `fyro-insecure-` or is shorter than 50 characters): `warnings.warn` when `debug=True`, error when `debug=False`. This follows Django's `security.W009` and the FastAPI full-stack template's `changethis` check.

**Steps and checks**
1. Write tests first: load from module, env override, `.env` override, missing env var, wrong scheme, two subclasses, missing `secret_key`, list parsing (JSON and comma), insecure key with warning in debug and error in non-debug. Verify: the tests fail.
2. Implement `conf.py`. Verify: the tests pass.

**Out of scope:** per-environment settings files and secrets managers.

---

## Phase 2: App loader (the skeleton)

**Goal:** read `installed_apps`, discover each app's modules, and build a FastAPI app.

**Public API:** `fyro.create_app() -> FastAPI` and `fyro.apps.registry` (read-only view of loaded apps and models).

**Behavior**
- Import `<app>.models` for each installed app. It is required, and a missing module gives an error naming the app and file.
- Import `<app>.routes` if present. It must expose `router: APIRouter`, which is mounted at `/api/<app_label>`.
- `admin.py` is only recorded in phase 2 and used in phase 9.
- App label = last segment of the dotted path (`fyro.auth` gives `auth`). Duplicate labels are an error.
- Models register themselves with the registry when defined. A Model defined outside an installed app's `models.py` is an error.
- Loading happens once. A second call is idempotent.

**Steps and checks**
1. Create test fixture apps (valid, missing models, routes without `router`, duplicate label). Write failing tests.
2. Implement `apps.py` + `create_app()`. Verify: the tests pass, and the fixture app's custom route answers through `httpx.AsyncClient`.

**Out of scope:** CRUD routes (phase 5), DB (phase 4).

---

## Phase 3: The Model class (the heart)

**Goal:** one class gives a SQLAlchemy table plus Create/Read/Update schemas.

**Public API**

```python
from fyro import Model, Field

class Post(Model):
    title: str = Field(max_length=200)
    body: str
    secret: str = Field(write_only=True)

    class Meta:            # optional
        table_name = "posts"
        read_exclude = ["body"]
        create_schema = MyPostCreate
```

**`Field(...)` arguments (v0.1):** `default`, `default_factory`, `primary_key`, `max_length`, `unique`, `index`, `read_only`, `write_only`, plus Pydantic constraints passed through (`ge`, `le`, `min_length`, `pattern`, `description`).

**Supported types (v0.1):** `int`, `str`, `bool`, `float`, `Decimal`, `datetime`, `date`, `UUID`, `Enum`, and any of these as `T | None` (nullable). Any other type raises `TypeError` at class creation, naming the model and field.

**Schema rules**
- `Create`: all fields except `read_only`. Required unless they have a default.
- `Read`: all fields except `write_only`, with `from_attributes=True`.
- `Update`: `Create`'s fields, all optional with default `None`. Callers use `exclude_unset`.
- `Meta.*_exclude` removes fields. `Meta.*_schema` replaces the schema entirely.
- Unknown `Meta` keys or excluded field names that don't exist are errors.
- `read_only` and `write_only` both set on one field is an error.

**Mechanism (built):** `__init_subclass__` on a `DeclarativeBase` subclass (no metaclass needed). It rewrites plain annotations into `Mapped[T]` + `mapped_column(...)` before SQLAlchemy maps the class. It then generates the schemas with `pydantic.create_model`. It stores the field flags in the registry and leaves room for `.objects` (phase 4).

**Steps and checks**
1. **Spike:** prove that plain annotations can be rewritten to `Mapped` before SA mapping, with one throwaway test. Verify: `Post.__table__` has the right columns, types and nullability. If the spike fails, stop and report options.
2. Write failing tests for every schema rule above, plus table name, auto id and the unsupported-type error.
3. Implement `fields.py`, `schemas.py`, `base.py`. Verify: the tests pass, and `metadata.create_all` against Docker Postgres creates `blog_post`.
4. **Magic gate (human):** show the north-star `Post` example, its generated table DDL and the JSON schema of all 3 schemas. The user approves or we iterate. Phase 4 does not start before approval.

**Out of scope:** relations, the manager / queries, migrations.

---

## Phase 4: Engine, session and `Model.objects` (built)

Full spec: `BUILD_PROMPT.md`, Phase 4 prompt. Summary:
- `Engine` protocol with `fetch(stmt) -> list` and `execute(stmt) -> int`. `SQLAlchemyEngine` is the only implementation.
- `fyro.db.get_engine()` is lazy and cached. `create_app()` adds a `lifespan` that disposes the engine on shutdown.
- A pure ASGI middleware opens one session per request in a contextvar. It commits before the response starts and rolls back on exception.
- `async with fyro.db.session():` for scripts and tests. Nested sessions reuse the outer one.
- `Model.objects` manager: `filter`, `exclude`, `order_by`, `limit`, `offset`, `all`, `get`, `first`, `count`, `create`, `update`, `delete`. Equality lookups only. Per-model `DoesNotExist` / `MultipleObjectsReturned`.

---

## Phases 5 to 10: outline

5. **Auto CRUD** (detailed in `BUILD_PROMPT.md`): per-model router at `/api/<app>/<model>` with list (field filters, limit/offset, total count), get, create, patch and delete. It shows in OpenAPI and can be opted out of via `Meta.api = False`. A custom route that duplicates a CRUD method+path is a startup error naming both. **This is the demo gate.**
6. **Migrations**: Alembic, async `env.py`, one history per project, `target_metadata` from the registry. Spike: `Config` without `alembic.ini`. Renames show up as drop + add, and this is documented.
7. **CLI** (Typer): `new` (also writes `.env` with a `fyro-insecure-` secret key and a `.gitignore` containing `.env`), `startapp`, `runserver` (uvicorn), `makemigrations`, `migrate`, `createsuperuser`. Verify with a scripted end-to-end run of the 10-minute test.
8. **Auth**: built-in `fyro.auth` app, `User` model, password hashing, session + JWT login, per-model permissions (`Meta.permissions`).
9. **Admin**: at `/admin`, HTMX + Jinja2 inside the package, consuming the generated API: list, search, edit, delete. No relation picker in v0.1 because relations come in v0.2.
10. **Rust engine**: profile real usage, then swap the `Engine` implementation via PyO3/maturin. The public API does not change.

## Not building (v0.1)

WebSocket admin, OAuth, rate limiting, CSRF extras, background tasks, signals, auto-mocked tests, relations, multiple databases.

## fancy features off the top of my head in the far future
1. a fancy looking cli???
2. super easy commands to like running a dev server, maybe even a deploy command?
3. a open source marketplace for 3rd party packages?? like django currently has?
4. mcp for this project so llm can build at light speed or something?