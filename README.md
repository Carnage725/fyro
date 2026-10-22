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

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                 # install deps
docker compose up -d    # Postgres 16 on localhost:5432 (user/password/db: fyro)
uv run pytest           # tests
uv run ruff check       # lint
```
