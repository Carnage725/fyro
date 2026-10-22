# Command line

`fyro` is installed with the package; inside a project, `python manage.py <command>` runs the same commands with the project's settings.

| Command | What it does |
|---|---|
| `fyro new <name> [directory]` | Create a project. Refuses to overwrite any existing file. |
| `startapp <name>` | Create `<name>/models.py`. Prints the line to add to `installed_apps` (it never edits your settings). |
| `makemigrations [-m message] [-v]` | Write a migration. Prints the file, or `No changes detected`. |
| `migrate [target] [-v]` | Apply migrations (`target`: a revision, `head` or `zero`). |
| `runserver [--host 127.0.0.1] [--port 8000] [--no-reload]` | Development server with auto-reload. |
| `createsuperuser [--noinput]` | Create a superuser (needs `fyro.auth`). `--noinput` reads `FYRO_SUPERUSER_USERNAME`, `FYRO_SUPERUSER_EMAIL` and `FYRO_SUPERUSER_PASSWORD` from the environment. |

- Configuration and database problems print one line and exit with status 1, e.g. `Error: Cannot connect to the database at postgresql+asyncpg://fyro:***@localhost:5432/mysite: … Is Postgres running? (docker compose up -d)`. Passwords are always masked.
- `makemigrations` and `runserver` warn about folders that have a `models.py` but are not in `installed_apps`, and `runserver` warns about unapplied migrations.
- `fyro new` writes `compose.yaml` for Postgres. If port 5432 is taken: `FYRO_DB_PORT=5433 docker compose up -d`, and change the port in `.env`.
