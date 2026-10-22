# fyro

A web framework on top of FastAPI that feels like Django. FastAPI is the engine; fyro adds project structure, a single-source Model class, a hidden DB session, auto CRUD, migrations, a CLI, auth and an admin.

See [IDEA.md](IDEA.md) for the why and [PLAN.md](PLAN.md) for the build plan.

## Status

v0.1 in progress. Done: Phase 0 (foundations), Phase 1 (typed settings).

## Settings

```python
# myproject/settings.py
from fyro import Settings

class ProjectSettings(Settings):
    database_url: str = "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
    installed_apps: list[str] = ["blog"]
```

Point `FYRO_SETTINGS` at the module (`FYRO_SETTINGS=myproject.settings`). Any field can be overridden by an env var or `.env` entry named `FYRO_<FIELD>`. `FYRO_SECRET_KEY` is required. List fields accept JSON (`'["a","b"]'`) or comma-separated (`a,b`) values.

## Migrations

```python
from fyro import migrations

migrations.makemigrations()   # writes migrations/<date>-<rev>_initial.py, or None if nothing changed
migrations.migrate()          # up to the latest revision
migrations.migrate("<rev>")   # up or down to a revision; "zero" empties the database
```

Revision files live in `<project root>/migrations/`. fyro keeps the Alembic config and `env.py` internal.

- **Renames are not detected.** Renaming a field shows up as drop + add, which loses that column's data. `makemigrations()` logs a warning when it sees this.
- **New required fields on tables with data.** A constant default (`views: int = 0`) fills existing rows. `default_factory=uuid.uuid4`, `datetime.now` and `date.today` give each existing row its own value. A required field with no default, or another `default_factory`, is refused: give it a constant default or make it optional (`T | None = None`).
- **Enums are stored as VARCHAR(255)** holding the member value, so adding members needs no migration. Before removing a member, update the rows that use it: reading a row whose stored value is no longer in the enum raises `LookupError`.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                 # install deps
docker compose up -d    # Postgres 16 on localhost:5432 (user/password/db: fyro)
uv run pytest           # tests
uv run ruff check       # lint
```
