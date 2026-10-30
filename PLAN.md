# fyro: Build Plan

> **Status: DRAFT (v0.2 plan: relations).** v0.1 (Phases 0 to 9) is built. Its plan lives in git history. Phases here continue the numbering at 10, and each is detailed in `BUILD_PROMPT.md` only after the previous phase's gate passes.

## Goal

Models can point at each other. A post has an author, a post has many tags. One model class still produces the table, the schemas, the API and the admin, now including the links.

**Success test:** a developer adds `author: User = ForeignKey(on_delete="cascade")` and `tags: list[Tag] = ManyToMany()` to a model, runs `makemigrations` and `migrate`, and gets:
- the right columns, join table and constraints in Postgres,
- an API that accepts `author_id` and `tag_ids` and returns them, plus `?expand=author,tags` for the full objects,
- an admin with a dropdown for the author and a multi-select for the tags,

with no glue code, and with listing 50 posts with `?expand=author,tags` costing a fixed number of queries (not one per post).

## Locked decisions

| Area | Decision |
|---|---|
| Package name | `fyro` |
| Stack | Python 3.12+, uv, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2 async + asyncpg, Alembic, Typer (CLI), Jinja2 + HTMX (admin) |
| Database | Postgres only. |
| Settings | Project `settings.py` holds a `fyro.Settings` subclass. Env vars and `.env` (prefix `FYRO_`) override. |
| List env vars | `list[str]` settings accept JSON (`'["a","b"]'`) or comma-separated (`a,b`) |
| Secret key | Required with no default. `fyro new` writes `FYRO_SECRET_KEY=fyro-insecure-<random>` to a gitignored `.env`. An insecure key gives a warning when `debug=True` and an error when `debug=False`. |
| URLs | All API under `/api`: auto CRUD at `/api/<app>/<model>`, custom app routes at `/api/<app>/...`. Admin at `/admin`, docs at `/docs`. No `/v1`. A duplicate method+path is a startup error. |
| Finding settings | `FYRO_SETTINGS=myproject.settings` env var, written into generated `manage.py` / `asgi.py` |
| App layout | `models.py` required. `routes.py` and `admin.py` optional. |
| Field syntax | Typed annotations: `title: str = Field(max_length=200)` |
| Field flags | `read_only` (not in Create/Update) and `write_only` (not in Read) |
| Primary key | Auto-added `id: int` (bigint, autoincrement, read_only) when none is declared |
| Runtime instance | The Model class is a SQLAlchemy-mapped class. `Create`/`Read`/`Update` are separate generated Pydantic classes. It is not a dual Pydantic/SA object (the SQLModel way). |
| Table name | `<app_label>_<model_snake>`, e.g. `blog_post`. `Meta.table_name` overrides. |
| Escape hatch | `Meta` tweaks (`create_exclude`, `read_exclude`, `update_exclude`) plus full replace (`create_schema`, `read_schema`, `update_schema`) |
| Query API | Django-style async manager: `await Post.objects.filter(title="x").all()`. Session comes from a request-scoped contextvar. |
| Engine seam | The manager only calls `engine.fetch(stmt) -> list` (rows/scalars) and `engine.execute(stmt) -> int` (rowcount), so the database layer can be swapped without changing the public API. |
| Update schema | Strict: every field defaults to `None`, but the annotation stays `T`. An explicit `null` on a NOT NULL column is rejected. |
| Defaults | Scalar defaults are Python-side only (`mapped_column(default=...)`). No `server_default`. |
| New NOT NULL columns | `makemigrations` never writes a migration that fails on a table with rows. A constant default is added as a temporary database default and then dropped (Django's approach). `uuid4`, `datetime.now` and `date.today` factories fill old rows with `gen_random_uuid()`, `now()` or `CURRENT_DATE` (add nullable, fill, set NOT NULL). No default, or any other factory, is refused with an error. |
| Enums | Stored as VARCHAR(255) holding the member value (`native_enum=False`), not Postgres `ENUM` types. No CHECK constraint; Pydantic validates. |
| Migration files | Hidden `env.py` inside fyro; Alembic hex revision ids with date-prefixed file names; one `migrate(target)` that moves up or down, `"zero"` = empty. |
| Not found | `create_app()` turns `fyro.db.DoesNotExist` into a 404 on every route, including hand-written ones. |
| Migrations | One Alembic history in `<project>/migrations/`, wrapped by `makemigrations` / `migrate` |
| Test DB | `docker compose` Postgres |
| Plan depth | Each phase is detailed in `BUILD_PROMPT.md` only after the previous phase's gate passes. |
| **Relation syntax** | Django style. `author: User = ForeignKey(on_delete=...)` creates column `author_id`. `tags: list[Tag] = ManyToMany()` creates a join table. (v0.2 decision) |
| **Relations in the API** | Ids by default (`author_id`, `tag_ids`). `?expand=author,tags` adds the full objects, one level deep. (v0.2 decision) |
| **Relations engine** | SQLAlchemy `ForeignKey` + `relationship()`. fyro writes them; it does not build its own relation layer. (v0.2 decision) |
| **No hidden queries** | Relationships use `lazy="raise"`. Reading `post.author` without loading it first is a clear fyro error that says how to load it, never a silent extra query. Async SQLAlchemy cannot lazy-load anyway. (v0.2 decision) |
| **on_delete** | Required on every `ForeignKey`, like Django. `"cascade"`, `"set_null"` (field must be `T \| None`) or `"restrict"`. Enforced by Postgres (`ON DELETE ...`), so bulk deletes and raw SQL obey it too. (v0.2 decision) |

## North star: the user experience we are building toward

```python
# blog/models.py
from fyro import Model, Field, ForeignKey, ManyToMany
from fyro.auth.models import User

class Tag(Model):
    name: str = Field(max_length=50, unique=True)

class Post(Model):
    title: str = Field(max_length=200)
    author: User = ForeignKey(on_delete="cascade")
    editor: User | None = ForeignKey(on_delete="set_null", default=None)
    tags: list[Tag] = ManyToMany()
```

Database: `blog_post` gets `author_id BIGINT NOT NULL REFERENCES auth_user ON DELETE CASCADE` (indexed) and `editor_id` (nullable, `ON DELETE SET NULL`). A join table `blog_post_tags (post_id, tag_id)` with both columns as the primary key and `ON DELETE CASCADE` on both.

API:

```
POST /api/blog/post   {"title": "Hi", "author_id": 5, "tag_ids": [1, 2]}
GET  /api/blog/post/1                      -> {"id": 1, "title": "Hi", "author_id": 5, "editor_id": null, "tag_ids": [1, 2]}
GET  /api/blog/post/1?expand=author,tags   -> same, plus "author": {...User.Read...}, "tags": [{...Tag.Read...}, ...]
GET  /api/blog/post?author_id=5            -> posts by user 5
```

Python:

```python
post = await Post.objects.select_related("author").get(id=1)
post.author.username                       # loaded, no query
post.author_id                             # always there, no loading needed

posts = await Post.objects.prefetch_related("tags").all()
[t.name for t in posts[0].tags]            # loaded with one extra query for all posts
await Post.objects.filter(author_id=5).all()
await Post.objects.filter(author=user).all()

post = await Post.objects.get(id=1)
post.author    # fyro error: "Post.author is not loaded. Use Post.objects.select_related('author')."
```

## Rules for every phase

- A link target must be a Model class that is already defined (imported). A target that is a string, a self-reference (`parent: "Category"`) or a circular import is an error that names the field and says it comes later.
- Every relation error names the model and field and says how to fix it, like v0.1.
- `read_only` and `write_only` on `ForeignKey(...)` apply to its `author_id` field. `Meta.*_exclude` names the id field (`"author_id"`).
- Expanding a target uses the target's own `Read` schema, so `write_only` fields (like `User.password`) never leak. Expanding also checks the target's read permission level for the caller; a caller who may not read `User` gets a 403 for `?expand=author`.
- Query count is tested. A list with `expand` must run a fixed number of queries, whatever the page size.

---

## Phase 10: `ForeignKey` on the model

**Goal:** `author: User = ForeignKey(on_delete="cascade")` gives the right table and schemas.

**Public API:** `fyro.ForeignKey(*, on_delete, default=MISSING, read_only=False, write_only=False, description=None)`.

**Behavior**
- Column `<name>_id`, same type as the target's primary key, NOT NULL unless the annotation is `T | None`. Indexed (Postgres does not index foreign keys by itself; Django does).
- Constraint name from the existing naming convention (`fk_blog_post_author_id_auth_user`).
- A `relationship()` named `author` with `lazy="raise"`.
- Schemas: `Create`/`Update`/`Read` contain `author_id: int` (or `int | None`), not `author`.
- `on_delete` missing, unknown, or `"set_null"` on a non-nullable field: `TypeError` naming the field.
- A field named `author_id` next to `author = ForeignKey(...)`: error (name clash).

**Steps and checks**
1. **Spike:** prove that `__init_subclass__` can turn `author: User` into a `Mapped[int]` column `author_id` plus a `Mapped[User]` `relationship(lazy="raise")` before SQLAlchemy maps the class. Verify: one throwaway test shows `Post.__table__.c.author_id` with its foreign key, and `Post.author.property` is a relationship. If it fails, stop and report options.
2. Write failing tests for every behavior above. Verify: they fail.
3. Implement. Verify: tests pass, and `metadata.create_all` against Docker Postgres creates the constraint and index.
4. **Gate (human):** show the north-star `Post` DDL and the JSON schema of its three schemas.

**Out of scope:** queries on relations (Phase 11), API (Phase 12), many-to-many (Phase 16).

---

## Phase 11: Querying with `ForeignKey`

**Goal:** filter by a link and load linked objects without hidden queries.

**Public API**
- `filter(author_id=5)`, `filter(author=user)`, `exclude(...)` the same way.
- `QuerySet.select_related(*names)`: loads `ForeignKey` targets in the same request (`selectinload` or `joinedload`, picked at the spike).
- `create(author=user)` and `create(author_id=5)` both work.
- Reading an unloaded relation raises a fyro error naming the model, field and the `select_related` call to use.

**Rules**
- Unknown names in `select_related` are an error listing the model's relations.
- Everything still goes through `engine.fetch` / `engine.execute` (the engine seam stays).

**Steps and checks**
1. Write failing tests, including one that counts queries for `select_related` over 50 rows. Verify: they fail.
2. Implement. Verify: tests pass; the 50-row test runs 2 queries or fewer.

**Out of scope:** lookups across links (`author__username`, Phase 17), reverse access (`user.posts`).

---

## Phase 12: Relations in the auto API

**Goal:** the auto CRUD reads and writes links.

**Behavior**
- `author_id` in request and response bodies, and as a list filter (`?author_id=5`).
- `?expand=author` on list and get. Comma-separated for several. One level deep only. Unknown names: 422 listing the expandable fields. `expand` joins the reserved list query parameters (a field named `expand` is a startup error, like `limit`).
- Expanded objects use the target's `Read` schema and the target's read permission.
- A missing target on create/update (`author_id: 999`): 422 naming the field (`no User with id 999`), not a 500. This maps Postgres foreign key violations (SQLSTATE `23503`).
- Deleting a row that `restrict` links still point at: 409 naming the linking model.
- OpenAPI shows `expand` and the expanded schema.

**Steps and checks**
1. Write failing API tests for each behavior, plus a query-count test for `?expand=author&limit=50`. Verify: they fail.
2. Implement in `crud.py`. Verify: tests pass.
3. **Gate (human, demo):** run the north-star app; show the docs page and the JSON for get, list and expand.

---

## Phase 13: Migrations for `ForeignKey`

**Goal:** `makemigrations` writes working migrations for links.

**Behavior**
- New tables with foreign keys are created in dependency order (target first), across apps.
- Adding a NOT NULL `ForeignKey` to a table with rows (or making an optional one required) follows the locked "New NOT NULL columns" rule: with no default, it is refused with an error that says to give it `default=<id of an existing row>` (removable after migrating) or keep it optional. With a default, old rows get that id.
- Changing `on_delete` drops and recreates the constraint.
- Turning a plain id column into a `ForeignKey` (e.g. `author_id: int` into `author: Author = ForeignKey(...)`): rows pointing at a missing row get the `on_delete` treatment first (`cascade` deletes them, `set_null` clears them, `restrict` leaves them and the migration fails), with a warning from `makemigrations`.
- `fyro.auth.Session.user_id` becomes `user: User = ForeignKey(on_delete="cascade")`, through the rule above: sessions of deleted users are removed, then the constraint is added. An authenticated request then loads session and user in one SELECT instead of two.

**Steps and checks**
1. Write failing migration tests: create, add nullable FK to a table with rows, refuse NOT NULL FK without default, change `on_delete`, migrate down to `"zero"`. Verify: they fail.
2. Implement. Verify: tests pass; the CLI end-to-end test still passes.

---

## Phase 14: `ForeignKey` in the admin

**Goal:** the admin edits links.

**Behavior**
- Add/edit forms show a dropdown of target rows. Over 100 target rows: a search box that queries the target instead.
- A row is shown by its `__str__` if the model defines one, otherwise `<Model> #<id>`.
- List pages show the linked row's label and can filter by it.
- Admin list pages load labels with `select_related`, never one query per row.

**Steps and checks**
1. Write failing admin tests (form widget, save, list label, filter, query count). Verify: they fail.
2. Implement. Verify: tests pass; check the screens in a browser.

---

## Phase 15: Escape hatches

**Goal:** dropping down to plain FastAPI and SQLAlchemy is natural, and a renamed field keeps its data. (From an outside review: "opinionated by default, flexible by choice".)

**Public API**
- `fyro.db.current_session() -> AsyncSession`: the request's (or `fyro.db.session()`'s) SQLAlchemy session, for raw queries. Outside a session: the existing clear error.
- `fyro.db.DBSession`: the same as a FastAPI dependency, `async def stats(db: DBSession)`.
- `makemigrations` asks about renames. When a table loses one column and gains another of the same type and nullability, the CLI asks `Was blog_post.author renamed to writer? [y/N]`. Yes writes `ALTER TABLE ... RENAME COLUMN` (data kept, and the downgrade renames back); no keeps drop + add with the existing warning. `--noinput`, or no terminal, never asks. In Python: `migrations.makemigrations(ask_rename=callable)`.
- A guide page, "Dropping down": raw SQLAlchemy with the session, custom routes in `routes.py`, replacing generated schemas, `Meta.api = False`.

**Steps and checks**
1. Session: failing tests for `current_session()` and `DBSession` in a hand-written route, then implement. Verify: tests pass.
2. Renames: failing tests (rename kept with data, downgrade renames back, "no" keeps drop + add, different types never asked, no `ask_rename` never asks, a required column renamed is not refused), then implement. Verify: tests pass, and the CLI end-to-end test still passes.
3. Docs page in `docs/guides/`, added to `mkdocs.yml`. Verify: every code sample on it runs in a test.

---

## Phase 16: `ManyToMany`

**Goal:** `tags: list[Tag] = ManyToMany()` end to end: model, queries, API, migrations, admin.

**Behavior**
- Join table `<table>_<field>` (e.g. `blog_post_tags`) with columns `<model>_id`, `<target>_id`, both the primary key, both `ON DELETE CASCADE`, the second indexed. Not a Model; fyro owns it.
- The ids field is the field name made singular plus `_ids`: `tags` gives `tag_ids`, `categories` gives `category_ids`, `staff` gives `staff_ids`. `Create`/`Update`/`Read` contain it as `list[int]` (Create defaults to `[]`). On update it replaces the whole set. Ids come back sorted.
- Writes go through the manager (decision at the step 1 spike, over Django's `post.tags.add()`): `create(tags=[news, tech])` or `create(tag_ids=[1, 2])`; `filter(...).update(tags=[...])` replaces the set of every matching row (rows are picked before the UPDATE runs).
- Queries: `prefetch_related("tags")` (one extra query for all rows), `filter(tags=tag)` / `exclude(tags=tag)` with a row or an id. `filter(tag_ids=...)` waits for `__in` (Phase 17). Reading `post.tags` or `post.tag_ids` unloaded raises `NotLoaded` naming `prefetch_related`.
- API: `tag_ids` is read for every row (one extra query per page), `?expand=tags`. Unknown ids: 422 naming the first missing one (`tag_ids: no Tag with id 42.`).
- Migrations: the join table is created and dropped with the field.
- Admin: multi-select; over 100 target rows, a search box that keeps the current picks selected.

**Steps and checks** (done)
1. Spike + decision: manager writes. Verified SQLAlchemy `secondary=` with `lazy="raise"`, `selectinload` (2 queries for 20 rows) and Core writes against Postgres.
2. Tests for each part, then implementation. Query counts tested.
3. **Gate (human, demo):** the full north-star app.

---

## Phase 17: Lookups

**Goal:** filters beyond "equals", and one hop across a link.

**Public API:** `__gt`, `__gte`, `__lt`, `__lte`, `__in`, `__isnull`, `__icontains` (plus `__exact`), one hop across a `ForeignKey` (`filter(author__username="ana")`, `author__name__icontains=`), `author__in=[...]`, and `tags=` / `tags__in=` on a `ManyToMany`. The API list endpoint gets the same names on the model's own readable fields and its ManyToManys (`?created_at__gte=2026-01-01`, `?id__in=1&id__in=3`, `?tags=2`), typed per field: comparisons on numbers and dates, `icontains` on text, `isnull` on optional fields. Lookups across a ForeignKey are Python-only: on the API they would let anyone probe the target's fields, including models hidden from the API. `exclude()` keeps rows whose column is NULL, like Django.

**Rules**
- Unknown lookup or field: error naming both and listing valid ones.
- `write_only` fields cannot be filtered with any lookup (they could be probed).
- The admin bulk delete uses `id__in` (one query instead of two per row).

**Steps and checks**
1. Write failing tests. Verify: they fail.
2. Implement. Verify: tests pass.

---

## Not building (v0.2)

String or self-referencing targets (`parent: "Category"`), one-to-one, reverse accessors (`user.posts`), expand deeper than one level, lookups over more than one hop, generic relations, custom user model, plus everything from v0.1's list: WebSocket admin, OAuth, rate limiting, background tasks, signals, other databases.

## fancy features off the top of my head in the far future
1. a fancy looking cli???
2. super easy commands to like running a dev server, maybe even a deploy command?
3. a open source marketplace for 3rd party packages?? like django currently has?
4. mcp for this project so llm can build at light speed or something?
