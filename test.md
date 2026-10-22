# fyro: How to test

Read this before writing, reviewing, fixing or extending any test in this repo. It applies even when the request is only "add tests".

A test is an attack on the code, not a demonstration that it runs. Its job is to find where fyro breaks. No suite can prove fyro never breaks. The aim is to attack from many angles, make bugs hard to hide, and say plainly what is not covered.

Agents tend to write tests that fail none of these ways: they pass on the first run, copy the implementation's logic into the expected value, mock everything, and never show a red state. That is banned here.

## Project context

- **Stack:** FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2 async, asyncpg, Alembic, Typer. Postgres only.
- **Test tools:** pytest, pytest-asyncio (`asyncio_mode = "auto"`), httpx, and a real Postgres from `docker compose up -d`.
- **The contract is `PLAN.md` and `BUILD_PROMPT.md`, not `src/`.** Expected values come from the phase spec. If the spec does not say what should happen, stop and ask the user. Do not read the answer off the code.
- **Build state:** v0.1 is fully built: phases 0 to 9 (settings, app loader, Model class, engine/session/manager, auto CRUD, migrations, CLI, auth, admin), plus the phase 6.1 fix for new NOT NULL columns.

Four pieces carry the framework and get the heaviest attack. A silent bug in any of them breaks every user at once:

| Piece | Code | Main tests |
|---|---|---|
| App loader | `src/fyro/apps.py`, `src/fyro/app.py` | `tests/test_apps.py` |
| Model class | `src/fyro/models/{base,fields,schemas}.py` | `tests/test_models.py`, `tests/test_models_db.py` |
| Engine, session, manager | `src/fyro/db/`, `src/fyro/models/manager.py` | `tests/test_db.py` |
| Auto CRUD (phase 5) | `src/fyro/crud.py`, `src/fyro/app.py` (404 handler, route order, duplicate check) | `tests/test_crud.py` (DB), `tests/test_crud_startup.py` (startup errors) |
| Migrations (phase 6) | `src/fyro/migrations/__init__.py`, `src/fyro/migrations/template/{env.py,script.py.mako}` | `tests/test_migrations.py` (own database `fyro_test_migrations`) |
| CLI (phase 7) | `src/fyro/cli.py`, `[project.scripts]` in `pyproject.toml` | `tests/test_cli.py` (CliRunner, own database `fyro_test_cli`), `tests/test_cli_e2e.py` (real processes, marked `slow`, own database `fyro_test_e2e`) |

## Mindset

These are not tests and are banned:
- calling a function and checking only that it did not raise
- calling it and printing or logging the result
- calling it to reach a line, raise coverage, or turn the suite green
- asserting something true no matter what the code does (`assert True`, `assert x is not None` on a value that can never be `None`)

Instead:
- **Every test targets a named bug.** Put the bug in the test name or a one-line comment, for example "write_only field leaks into Read" or "limit=101 accepted". If you cannot name a bug the test would catch, delete it.
- **Assume the code is wrong until it survives.** Spend most effort on boundaries, hostile input, failing dependencies, concurrency and cancellation.
- **A break is a success.** When an attack finds a bug, report it. Do not bend the test until it passes. If a serious attack found nothing, say what you tried.
- **Green proves nothing by itself.** For each passing test, ask which code change would turn it red. If the answer is "none", the test is worthless.

## Workflow

Do these steps in order. If a step cannot be done (tool missing, Docker down), say so in the report. Do not skip a step silently.

### 1. Write the contract
Copy the promises for the unit under test from `PLAN.md` / `BUILD_PROMPT.md` into the report (template below): inputs and valid ranges, outputs, exceptions (exact class and message fragment), side effects, invariants. Locked decisions count as contract, for example:
- the strict `Update` schema;
- Python-side defaults only;
- `DoesNotExist` giving a 404 on every route;
- the phase 5 API decisions:
  - list page `{items, total, limit, offset}`, `limit` default 50 and max 100, with 101 giving 422 (never capped);
  - snake_case path segment (`/api/blog/blog_post`) and a path param named after the pk field (`{id}`, `{slug}`);
  - 409 `"<Model> with this <field> already exists."` on unique violations from `POST`/`PATCH` only;
  - constraint names fixed by `Model.metadata`'s naming convention (`uq_<table>_<col>`, `ix_<table>_<col>`, `pk_<table>`).
- the phase 6 decisions:
  - a hidden `env.py`, with no `alembic.ini` in the project;
  - Alembic hex revision ids in date-prefixed file names (`YYYY_MM_DD_HHMM-<rev>_<slug>.py`);
  - one `migrate(target)` that moves up or down (`"zero"` empties the database);
  - `migrations/` in the directory holding the settings module's top package;
  - enums stored as VARCHAR(255) holding the member value, with no CHECK constraint.
- the phase 7 decisions:
  - `fyro new` writes `manage.py`, `asgi.py`, `.env` (`fyro-insecure-` key, `FYRO_DEBUG=true`, `FYRO_DATABASE_URL`), `.gitignore`, `compose.yaml` (host port `${FYRO_DB_PORT:-5432}`) and `<name>/settings.py`, and never overwrites;
  - `startapp` never edits `settings.py`; it prints the line, and a warning names app folders missing from `installed_apps`;
  - `runserver` reloads by default and binds 127.0.0.1;
  - `ImproperlyConfigured` prints one line and exits 1;
  - the dev-key warning is hidden inside the CLI (Django-style);
  - `createsuperuser` belongs to phase 8.

### 2. List the attacks
Before writing code, list the attacks using the checklist below. Each one becomes a test or a recorded gap.

### 3. Write behavior tests
- Use `pytest.mark.parametrize` with readable `ids=`.
- Assert on outputs and effects you can observe: HTTP status and body, rows in Postgres, raised exceptions. Do not assert on private attributes.
- Hardcode expected values from the contract. Never compute them with the same logic as the code.
- fyro raises `ImproperlyConfigured` (from `fyro.conf`) for structure errors, `TypeError` for bad field types and unknown field names, and `fyro.db.DoesNotExist` / `MultipleObjectsReturned` (plus per-model subclasses) for lookups. Assert the exact class and its message: `pytest.raises(ImproperlyConfigured, match="blog")`. Never use `pytest.raises(Exception)` or `except Exception`. fyro's rule is that errors name the file, app or field and say how to fix it, so the `match=` must check that too.
- Test HTTP in-process through the real `create_app()`:

```python
async with httpx.AsyncClient(
    transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
) as client:
    r = await client.post("/api/blog/post", json={"title": "x" * 201, "body": "b", "secret": "s"})
    assert r.status_code == 422   # contract: max_length=200 is enforced
```

`ASGITransport` does not run lifespan. That is fine because `get_engine()` is lazy. Lifespan behavior (dispose on shutdown) needs its own test that enters the lifespan explicitly.

### 4. Prove each test can fail (red first)
- **Bug fix:** run the new test against the unfixed code and confirm it fails for the right reason. Then fix it and confirm it passes.
- **New code:** for each important test, break the implementation (step 8) and confirm the test goes red.

### 5. Property tests for invariants
Where a rule must always hold, write a Hypothesis test and pin each failing input it finds with `@example(...)`. fyro's main invariants:
- For any payload `Post.Create` accepts, the insert succeeds. Pydantic and Postgres must agree, so a payload that passes validation must never cause a 500.
- `Post.Read` output never contains a `write_only` field, for any model shape.
- `Update` with `exclude_unset=True` changes only the fields that were sent.
- List pagination: `len(items) <= limit`, `total` is the same for any `limit`/`offset`, and pages cover every row exactly once.

```python
from hypothesis import given, example, strategies as st

@given(st.text(max_size=200))
@example("\x00")                  # Postgres rejects NUL in text: must be a 4xx, never a 500
async def test_valid_title_never_500(title): ...
```

### 6. Real dependencies for what fyro owns
- **Never mock the database, the engine, the session or any `fyro` module.** Never use SQLite. fyro is Postgres-only and SQLite hides real behavior.
- **Database:** the docker compose Postgres, URL from `FYRO_TEST_DATABASE_URL` (default `postgresql+asyncpg://fyro:fyro@localhost:5432/fyro`). Follow the pattern in `tests/test_db.py`: tables are created per module and a `TRUNCATE ... RESTART IDENTITY` runs before each test.
- **Do not wrap tests in an outer transaction that rolls back.** fyro's request middleware commits before the response starts, and `fyro.db.session()` commits on clean exit. A rollback-wrapped test would turn those commits into savepoints and hide the exact bugs we need to see. Tests must observe real commits and real rollbacks by reading Postgres afterwards on a separate connection.
- **Schema:** build the test schema by running fyro's real `migrate`, so every run also tests migrations.
  - **Current state:** only `tests/test_migrations.py` does this. The phase 0 to 5 suites still use `Model.metadata.create_all`. Switching them is a pending, separate step: ask before doing it.
  - Migration tests need their own database, because `alembic_version` is global per database. Create and drop it with an autocommit connection, as `tests/test_migrations.py` does.
  - They also need `sys.dont_write_bytecode = True` when they rewrite `models.py` within one test, or a stale `.pyc` can hide the change.
- **Global state is the main source of flakiness.** `get_settings` is cached, the registry (`fyro.apps.registry`) and `Model.metadata` are global, and fixture apps sit in `sys.modules`. Every test that touches them must reset them, as the existing fixtures do (`get_settings.cache_clear()`, a fresh `Registry`, `Model.metadata.clear()`, removing `blog` from `sys.modules`). A test that passes alone but fails in random order is a real bug in the test or in fyro.
- **Fakes are only for what fyro cannot run or control:** third-party HTTP (later: OAuth, email), the clock, randomness, and failures you cannot trigger on demand.
  - Write small fakes against a Protocol, not `MagicMock`.
  - Every fake must be able to fail, and every failure path gets a test: exception, timeout, malformed data, `asyncio.CancelledError`.
  - Use `httpx.MockTransport` for outgoing HTTP.
  - Assert outcomes, not call counts.
  - Do not use `app.dependency_overrides` to swap the database.
- **One allowed exception:** the engine seam test may use a recording fake `Engine`. The contract there is "the manager only talks to the DB through `fetch` / `execute`", so the calls are the outcome.

### 7. Run the checks
Python has no race detector, so attack concurrency directly with `asyncio.gather` against real Postgres, random test order and repeated runs.

```bash
docker compose up -d
uv run ruff check .
uv run pytest -q -p no:cacheprovider --timeout=30   # run 3 times; pytest-randomly reshuffles each run
uv run pytest -q --hypothesis-show-statistics
PYTHONASYNCIODEBUG=1 uv run pytest -q -W error      # un-awaited coroutines, leaked tasks, unclosed connections
```

**Tools not installed yet:** `hypothesis`, `pytest-randomly` and `pytest-timeout` are not in the dev group. There is no type checker (pyright or mypy) configured. Ask the user before running `uv add --dev ...`. Until then, record the missing checks in the report as gaps.

Everything must pass. A flaky test is a failing test. Find the cause (shared global state, ordering, time, un-awaited tasks, leaked connections). Never rerun until green. Coverage (`--cov --cov-branch`, needs `pytest-cov`) is a hint for finding untested code, never a target. No `# pragma: no cover`.

### 8. Break the code yourself (mutation testing)
A mutant is a small deliberate bug. If no test fails, the mutant "survived" and the suite has a hole.

Mutate only in a throwaway copy. Never mutate the real working tree. Git now tracks the repo, but `git diff` only catches a leftover mutation if nobody commits it first.

```bash
tmp=$(mktemp -d) && rsync -a --exclude .venv --exclude .git ./ "$tmp/" && cd "$tmp"
# apply ONE mutation, then:
uv run pytest -q -x        # red = KILLED (good), green = SURVIVED (hole)
```

`mutmut` is optional and not installed. Use it only if the user agrees to add it, and never report results you did not get.

Generic mutations: `<` to `<=`, `==` to `!=`, `and` to `or`, `+ 1` to `- 1`, delete an early `return` or `raise`, drop an `await`, flip `is None`, return a constant.

fyro-specific mutations (do all that apply to the code under test):
- **Model:** ignore `read_only` or `write_only` when building a schema; make `Update` fields `T | None` (breaks the strict-`Update` decision); drop the auto `id`; table name without the app label; skip the unknown-`Meta`-key check; emit `server_default`.
- **Loader:** skip the duplicate-label check; treat a missing `models.py` as optional; mount routes without the `/api/<app_label>` prefix.
- **Settings:** accept a non-`postgresql+asyncpg` URL; flip the debug check on insecure keys; skip the comma-split for list env vars.
- **Session:** commit after the response starts instead of before; skip the rollback on exception; let a nested `session()` commit; read the session from a module global instead of the contextvar.
- **Manager:** `exclude` behaving like `filter`; `None` becoming `= NULL` instead of `IS NULL`; `get` returning the first of two matches; `order_by("-x")` ascending; querysets mutating in place.
- **CRUD (phase 5):**
  - Paging: drop the `limit` upper bound; change the default `limit` from 50 to 20; make `total` count only the current page.
  - Leaks: let `write_only` fields into query params or responses.
  - `PATCH`: run it without `exclude_unset`; return the pre-update object instead of re-reading it.
  - Not found: make `DELETE` return 204 for a missing row; remove the `DoesNotExist` 404 handler.
  - Conflicts: return 409 for every `IntegrityError` instead of only SQLSTATE `23505`; remove the naming convention so the 409 says "these values".
  - Routing: mount CRUD before custom routes; skip the duplicate-route check; stop normalizing path param names in it (`{id}` vs `{post_id}`); include `Meta.api = False` models anyway.
- **Migrations (phase 6):** the first four were run by hand at the phase 6 build and were all KILLED.
  - `migrate` never downgrades.
  - Empty revisions are written.
  - The rename warning is removed.
  - The stale-database check in `makemigrations` is removed.
  - The default message is always `auto`.
  - `%` is not escaped in config paths.
  - `migrations/` resolves to the settings package instead of the project root.
  - The two-heads check is skipped.
  - `compare_type=False`.
  - Enums go back to `native_enum=True`, or store names instead of values.
  - **Phase 6.1** (the first three were run by hand and were all KILLED):
    - skip the fill rewrite;
    - keep the temporary database default;
    - turn an unknown factory into a plain add;
    - evaluate a factory once for all rows (same uuid everywhere);
    - format the default with an f-string instead of SQLAlchemy quoting.
- **CLI (phase 7):** the first five were run by hand at the phase 7 build and were all KILLED.
  - Errors are not caught (tracebacks instead of one line).
  - `new` writes files before the overwrite check.
  - Reload is off by default.
  - Alembic stdout is not redirected.
  - The unregistered-app warning is removed.
  - `new` uses a fixed secret key.
  - The key prefix is dropped (so production would accept it silently).
  - The `.env` lacks `FYRO_DEBUG`.
  - Bind 0.0.0.0 by default.
  - Skip the module-shadow check.
  - The dev-key filter matches every `UserWarning`.
  - `PYTHONWARNINGS` is not passed to the reload child.
  - `runserver` starts even when `create_app()` fails.

Hand-mutate at least every conditional and boundary in the code you tested. For each mutant, record KILLED, or SURVIVED with either a new test that kills it or a one-line reason it changes no behavior. Never drop a mutant to improve the numbers.

### 9. Write the report
Save the report as `tests/notes/<feature>.md` using the template below. It is not optional. The sections on bugs found, surviving mutants and known gaps keep the work honest.

## Hard rules

- No test that only runs code or cannot fail.
- Never mock fyro's own database, engine, session or modules. Never assert a mock's own return value.
- Every fake gets at least one failure-path test.
- Never edit or delete an existing test just to make it pass. If its expectation contradicts `PLAN.md` / `BUILD_PROMPT.md`, say so and explain why.
- Never add `pytest.skip`, `xfail` or a looser assertion to get green without telling the user.
- Never leave a mutated version of the code in the working tree.
- Never claim fyro is "fully tested" or "bug-free". Report what was attacked and what was not.
- Do not add test dependencies without asking (CLAUDE.md: simplicity first).
- Test code must be as readable as production code.

## Attack checklist

**Any function:** boundaries (one below, at, one above); zero, `None`, empty string, empty list; duplicates; ordering; huge and negative numbers; unicode, emoji, very long strings, NUL bytes (`\x00`, which Postgres rejects in text); wrong types; dependency raises; called twice; called concurrently; cancelled midway.

**Settings (`fyro.conf`, built):**
- `FYRO_SETTINGS` unset, pointing to a missing module, or a module with 0 or 2 `Settings` subclasses: each gives `ImproperlyConfigured` naming the problem.
- A non-`postgresql+asyncpg` URL is rejected.
- Precedence: env beats `.env`, which beats `settings.py`.
- List env vars: JSON, `a,b`, `a, b`, a trailing comma, an empty string, and a JSON value that is not a list.
- Insecure key (prefix `fyro-insecure-` or shorter than 50 characters): a warning with `debug=True`, an error with `debug=False`. Test exactly 49 and 50 characters.
- **Secrets never appear in `repr(settings)`, logs or error messages.** That covers `secret_key` and the password inside `database_url`.

**App loader (built):**
- missing app; missing `models.py`; an `ImportError` inside an app's `models.py` (the error must name the app and must not be swallowed as "missing models")
- `routes.py` without `router`, or with a `router` that is not an `APIRouter`
- duplicate labels (`x.blog` and `y.blog`); `installed_apps` order does not change the result; loading twice is idempotent
- a Model defined outside an installed app's `models.py` is rejected
- custom routes are mounted at `/api/<app_label>`

**Model class (built, the heart):**
- every flag in every schema: `read_only` absent from `Create` and `Update`, `write_only` absent from `Read`, both on one field is an error
- `Update` stays strict: an explicit `null` on a NOT NULL field gives 422; an omitted field stays unchanged
- auto `id` (bigint, autoincrement, read-only); a declared primary key replaces it
- table name `<app>_<snake>` including tricky names (`HTTPLog`, `BlogPost`, `A`); `Meta.table_name` override
- `T | None` becomes a nullable column, and plain `T` becomes NOT NULL
- `max_length` is enforced in both Pydantic and the database
- defaults and `default_factory` exist only on the Python side (no `server_default` in the DDL)
- an unsupported type gives `TypeError` naming model and field at class definition, not at request time
- unknown `Meta` keys, excluding a field that does not exist, and a replacement schema that is not a Pydantic model are all errors
- two models with the same class name in different apps; a subclass of a model
- Pydantic and Postgres must agree (property test in step 5)

**Engine, session, manager (built):**
- **Contextvar isolation:** fire concurrent requests with `asyncio.gather`. Request A's session must never be visible in request B, and none may leak after the request ends.
- **Commit before response:** a 200 means the row is in Postgres, checked on a separate connection. If the commit fails, the client gets a 500, never a 200. Trigger this with a `DEFERRABLE INITIALLY DEFERRED` unique constraint added by raw DDL in the test. Also test a streaming response.
- **Rollback:** an endpoint that writes then raises leaves no row behind. Client disconnect and `asyncio.CancelledError` mid-request leave no half-committed data and no leaked connection.
- `session()`: commits on clean exit, rolls back on exception, a nested `session()` reuses the outer one and does not commit early; using `.objects` with no session raises the exact `RuntimeError` message.
- Queryset immutability: a chained call never changes the original.
- `get` with 0 and with 2+ matches raises the per-model subclass, and catching the base `fyro.db` class also works.
- `filter(x=None)` produces `IS NULL`; `exclude(x=None)` produces `IS NOT NULL`.
- Unknown field names in `filter`, `exclude`, `order_by`, `create` and `update` give `TypeError` listing the valid fields. **Never let a field name reach SQL text unchecked (SQL injection via names).**
- `limit(0)`, a negative `offset`, `offset` past the end.
- `update` / `delete` with no filter affect all rows (contract). The rowcount is exact.
- Lifespan disposes the engine; `get_engine()` after disposal works again.
- Engine seam: the manager only calls `fetch` / `execute`. Write this as one contract suite that any `Engine` implementation must pass.

**Auto CRUD (phase 5, built):**
- **Paging:**
  - `limit` at 0, 1, 50 (default), 100, 101 and negative: 101 and 0 give 422, never silently capped.
  - `offset` negative and past the end.
  - `total` is unaffected by paging, and items are ordered by pk.
- **Filters:**
  - Every readable field, with typed values: `?published=notabool` gives 422.
  - No `write_only` field as a query param, and the value is never echoed back.
  - A nullable field cannot be filtered to `IS NULL` through the API. This is a known limit, so check it is documented, not silently wrong.
- **Leaks:** `write_only` fields are absent from every response, including `POST`, `PATCH` and every list item.
- **`PATCH`:** a missing field versus an explicit `null` (422 on NOT NULL, 200 on nullable), and an empty body (returns the row unchanged; 404 if the row is missing).
- **404 versus 422:** a bad id type is 422 and a missing row is 404. Deleting twice gives 204 then 404.
- **Primary keys:** non-`int` pks (`str`, `UUID`, `Enum`) get the right path param name (`{slug}`) and type (bad type gives 422).
- **Unique violation:**
  - `POST` and `PATCH` give 409 with the exact detail naming the field.
  - Afterwards the row is unchanged and the next request on the same app still works (the session was rolled back).
  - Check a `unique=True, index=True` field (a unique *index*, `ix_...`) as well as a plain unique constraint.
  - Any other `IntegrityError` stays 500.
- **Concurrency:** two concurrent `PATCH`es on one row; two concurrent `POST`s with the same unique value (exactly one 201, the other 409).
- **Not found:** `Post.DoesNotExist` raised in a hand-written route gives 404 with the message as `detail`. `MultipleObjectsReturned` stays 500.
- **Routing:**
  - The custom route `/api/blog/post/stats` is not swallowed by `/{id}`.
  - A duplicate method+path gives `ImproperlyConfigured` naming both endpoints, even when the param names differ (`{id}` vs `{post_id}`) or the custom route sits in a nested router.
  - The path segment is snake_case (`BlogPost` gives `blog_post`, `HTTPLog` gives `http_log`).
- **Opting out:** `Meta.api = False` produces no routes and no OpenAPI entries. `Meta.api = "no"` gives `TypeError`. A composite primary key without `api = False` is `ImproperlyConfigured`.
- **Escape hatches:** a custom `Meta.read_schema` without `from_attributes` still serializes ORM rows. A custom `create_schema` with a field the model lacks is unspecified in the contract, so ask before asserting.
- **OpenAPI:** each model has `<Model>Create`, `Read`, `Update` and `Page` schemas, tag `<app>.<model>`, and summaries such as "Get Post".
- **Known gap to attack:** a model field named `limit` or `offset` collides with the paging params, and nothing checks for it yet. Report what happens.

**Migrations (phase 6, built):**
- **Adding a NOT NULL field to a table that already has rows (phase 6.1 fix).** This was reproduced at the phase 7 gate (`NotNullViolationError ... contains null values`) and fixed in phase 6.1.
  - **Contract:** `makemigrations` never writes a migration that fails on a non-empty table.
  - **Constants:** int, bool, str with a quote, float and Enum fill old rows, and leave no column default.
  - **Factories:** `uuid4` gives distinct values per row; `datetime.now` and `date.today` fill.
  - **Refusals:** no default, or an unknown factory, gives `ImproperlyConfigured` and writes no file.
  - **Nullable:** a nullable field is a plain add.
  - **Still to attack:**
    - Decimal, date, datetime and UUID *constants*;
    - an Enum whose value contains a quote;
    - a default longer than the column (`max_length`);
    - a unique field with a constant default on a table with 2+ rows (expected: the migration fails on the unique constraint; decide whether to refuse up front);
    - changing an existing column from `T | None` to `T` while rows hold NULLs (not handled: expect a failing migration);
    - `date.today` vs the server timezone.
  - Defaults are Python-side only (phase 3 decision), so the generated `add_column(..., nullable=False)` has no `server_default`.
  - Postgres rejects that on a non-empty table (`column ... contains null values`).
  - Expected: `migrate()` fails. This is a likely real gap between two locked decisions: report it, do not paper over it.
- **`makemigrations`:**
  - no changes gives `None` and no file;
  - the first file is `*_initial`, later ones `*_auto`, and a custom message becomes the slug;
  - the file name matches `YYYY_MM_DD_HHMM-<12 hex>_<slug>.py`;
  - a database behind head gives `ImproperlyConfigured` mentioning `migrate()`;
  - two heads give `ImproperlyConfigured` naming both ids;
  - a call from a running event loop gives `RuntimeError` mentioning `to_thread`.
- **Location:**
  - `migrations/` lands in the directory holding the settings module's top package (`myproject.settings` puts it beside `myproject/`).
  - Also try a top-level `settings` module, a nested `a.b.settings`, and project paths containing spaces, `%` and `:`.
- **`migrate`:**
  - twice is a no-op;
  - targets `head`, `zero`, a full revision id, a unique prefix, an ambiguous prefix, and an unknown id (`ImproperlyConfigured` naming it);
  - an older target downgrades, a newer one upgrades, the current one is a no-op;
  - no `migrations/` gives `ImproperlyConfigured` mentioning `makemigrations()`;
  - an empty `migrations/` folder.
- **Schema equality:** after `migrate()`, `makemigrations()` returns `None` for **every** supported type:
  - `int`, `str` with and without `max_length`, `bool`, `float`, `Decimal`, `datetime` (timestamptz), `date`, `UUID`, `Enum`;
  - each as `T | None`;
  - with `unique`, `index`, and a declared non-`id` primary key.
  
  Any spurious diff is a bug.
- **Names:** constraints and indexes in Postgres carry the naming-convention names (`pk_`, `uq_`, `ix_`).
- **Rename warning:** a rename logs a warning on `fyro.migrations` naming the table and both columns. A pure add or a pure drop logs nothing.
- **Round trips:** `upgrade`, `zero` and `upgrade` again, repeated, with data in the tables where the downgrade allows it.
- **Enums:**
  - the column is `character varying(255)` and stores the member value (`draft`, not `DRAFT`), with no `pg_type` created;
  - adding a member gives no migration;
  - an int-valued Enum stores the string form;
  - **removing a member that rows still use makes reads raise `LookupError`** (documented behavior: assert it, so a change is noticed);
  - a value longer than 255 characters is not specified in the contract, so ask.
- **Scope of `env.py`:** only installed apps' models reach `target_metadata`. Offline mode raises `RuntimeError`.
- **Concurrency:** two `migrate()` calls at once on one database. Alembic takes no lock. Report what happens.
- **Package data:** a built wheel (`uv build`) contains `fyro/migrations/template/env.py` and `script.py.mako`.

**CLI (phase 7, built):**
- **`new`:**
  - the exact file tree;
  - the key is random per project, prefixed `fyro-insecure-` and at least 50 random characters;
  - `.env` holds only keys that `Settings` accepts (Phase 1 forbids extras): **adding any other key there breaks every command**;
  - the generated project loads with `debug=True`, and with `FYRO_DEBUG=false` the generated key is an error;
  - `.gitignore` lists `.env`;
  - `compose.yaml` port override works (`FYRO_DB_PORT=5433 docker compose up -d`).
- **`new` refusals:** invalid identifiers, keywords, names shadowing a module (`json`, `test`), an existing file anywhere in the tree, a target that is a file not a directory, and an unwritable directory. Each refusal leaves **nothing** behind (no partial project).
- **`startapp`:**
  - the files exist and `settings.py` is byte-for-byte unchanged;
  - an existing directory and a bad name are refused;
  - the generated `models.py` compiles and creates no table.
- **Project commands** (`makemigrations`, `migrate`, `runserver`), via both `python manage.py` and `fyro` with `FYRO_SETTINGS`:
  - run from the project root, from a subdirectory, and with `FYRO_SETTINGS` unset;
  - output carries no Alembic `Generating`/`INFO` lines unless `--verbose`;
  - the exit code is 1 for `ImproperlyConfigured` and for model `TypeError`s at load time;
  - **a database error (Postgres down, `NotNullViolation`) currently prints a full traceback: decide whether that is wanted.**
- **Warnings:**
  - the app-not-installed warning names the folder and settings file, and does not fire for installed apps, the settings package or folders without `models.py`;
  - the unapplied-migrations warning fires before `migrate` and not after;
  - `runserver` with Postgres down warns instead of crashing.
- **`runserver`:**
  - reload is on and `--no-reload` works;
  - it binds 127.0.0.1;
  - code edits trigger a reload;
  - Ctrl+C / SIGTERM stops the reloader and its child (no orphan process holding the port);
  - a port already in use gives a clear error.
- **Dev-key warning:**
  - it is hidden in every CLI command and in the reload child;
  - it still shows when running `uvicorn asgi:app` directly with debug on;
  - an insecure key with debug off is still an **error** through the CLI.
- **End to end:** `tests/test_cli_e2e.py` runs the PLAN success test with real processes. Keep it green, and keep its timing printout honest (machine time, not the human 10-minute budget).

**Auth (phase 8, database-backed opaque session tokens, no JWT):**
- **Leaks:** no endpoint, log line, error or OpenAPI schema ever contains a password hash, a raw token (except the login response) or a token hash.
- **Login:** an unknown user, a wrong password and an inactive user give the identical 401 body. Unknown users still run a hash verification, so time cannot enumerate usernames; check this coarsely, with no flaky timing asserts.
- **Tokens:**
  - guessed, tampered, truncated, expired and logged-out tokens are all 401;
  - a deactivated user's token is 401 on the next request;
  - every login gives a new token (no session fixation);
  - only the SHA-256 is stored.
- **Cookie:** the flags are `HttpOnly`, `SameSite=Lax`, and `Secure` unless debug. Logout clears the cookie **and** deletes the row.
- **CSRF:**
  - a cookie-authenticated POST, PATCH or DELETE without `X-Requested-With`/`HX-Request` gives 403;
  - a bearer request needs no header;
  - a request with **both** a valid bearer token and a cookie is authenticated by the bearer token (it is read first), so no CSRF header is needed.
- **Permissions:**
  - the full matrix of action × level × anonymous, user and superuser (401 vs 403 vs success);
  - the defaults (read public, write authenticated);
  - the permission check runs before the row lookup: an unauthorized caller gets 401/403 for both existing **and** missing ids, never 404 (no id enumeration); only allowed callers see 404;
  - `Meta.permissions` without `fyro.auth` gives `ImproperlyConfigured`.
- **`createsuperuser`:** a duplicate, a mismatch, an empty password, `--noinput` without the env vars, and a run without `fyro.auth` installed.
- **Concurrency:** two logins at once give two valid sessions; logout during an in-flight request.

**Admin (phase 9):** permissions are enforced on every action; field values are HTML-escaped (stored XSS); state-changing forms are protected against CSRF.

## Report template (`tests/notes/<feature>.md`)

```
# Test notes: <feature>

## Contract (from PLAN.md / BUILD_PROMPT.md, phase N)
Inputs and valid ranges:
Outputs:
Exceptions raised:
Side effects:
Invariants:

## Attacks tried
- <attack> -> <test name> -> result

## Bugs found
- <bug, how it was found, fixed or open>

## Mutation results
Tool or by hand:
KILLED:
SURVIVED (with fix or one-line justification):

## Known gaps (deliberately not covered)
- <gap and why, including any tool from step 7 that is not installed>

## How to re-run
<exact commands>
```

## What to tell the user at the end

In a few lines: what you attacked, what broke (bugs found, surviving mutants and what to do about them), what is deliberately not covered, and the exact commands to re-run.
