"""fyro's command line: `fyro new` plus the project commands that manage.py runs."""

import asyncio
import contextlib
import functools
import importlib.util
import io
import keyword
import logging
import os
import secrets
import socket
import sys
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

import asyncpg
import sqlalchemy.exc
import typer
import uvicorn
from sqlalchemy import make_url

from fyro import apps, migrations
from fyro.conf import SETTINGS_ENV_VAR, ImproperlyConfigured, Settings, get_settings

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="fyro: Django's structure on FastAPI."
)

MANAGE_PY = '''\
#!/usr/bin/env python
import os

from fyro.cli import main

if __name__ == "__main__":
    os.environ.setdefault("FYRO_SETTINGS", "__NAME__.settings")
    main()
'''
ASGI_PY = '''\
import os

import fyro

os.environ.setdefault("FYRO_SETTINGS", "__NAME__.settings")
app = fyro.create_app()
'''
SETTINGS_PY = '''\
from fyro import Settings


class ProjectSettings(Settings):
    installed_apps: list[str] = []
'''
ENV = """\
# Development only. Never commit this file. In production set real values instead.
FYRO_SECRET_KEY=__KEY__
FYRO_DEBUG=true
FYRO_DATABASE_URL=postgresql+asyncpg://fyro:fyro@localhost:5432/__NAME__
"""
GITIGNORE = """\
.env
.venv/
__pycache__/
"""
COMPOSE_YAML = """\
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: fyro
      POSTGRES_PASSWORD: fyro
      POSTGRES_DB: __NAME__
    ports:
      - "${FYRO_DB_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U fyro -d __NAME__"]
      interval: 2s
      timeout: 5s
      retries: 15

volumes:
  pgdata:
"""
MODELS_PY = '''\
"""Models for the __NAME__ app.

Each Model gets a table, Create/Read/Update schemas and API routes at /api/__NAME__/<model>.
After changing models, run `makemigrations`, then `migrate`.
"""

# from fyro import Field, Model
#
#
# class Post(Model):
#     title: str = Field(max_length=200)
#     published: bool = False
'''


def _friendly_errors(command: Callable[..., Any]) -> Callable[..., Any]:
    """Configuration mistakes print one red line and exit 1, not a traceback."""

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return command(*args, **kwargs)
        except ImproperlyConfigured as error:
            message = str(error)
        except (OSError, asyncpg.PostgresError, sqlalchemy.exc.DBAPIError) as error:
            message = _database_error(error)
            if message is None:
                raise
        typer.secho(f"Error: {message}", fg="red", err=True)
        raise typer.Exit(1)

    return wrapper


def _database_error(error: BaseException) -> str | None:
    """One line for a database problem, naming the database (password masked); None if the
    error is not about the database (an ordinary OSError keeps its traceback)."""
    root = error
    while root.__cause__ is not None:
        root = root.__cause__
    reason = " ".join(str(root).split())
    # Settings loaded fine before any database work, so this cannot fail here.
    where = make_url(get_settings().database_url).render_as_string(hide_password=True)
    connection_failed = isinstance(root, (ConnectionError, socket.gaierror)) or (
        isinstance(root, OSError) and "Connect call failed" in reason
    )
    if connection_failed:
        return (
            f"Cannot connect to the database at {where}: {reason}. "
            "Is Postgres running? (docker compose up -d)"
        )
    if isinstance(error, (asyncpg.PostgresError, sqlalchemy.exc.DBAPIError)):
        return f"Database error at {where}: {reason}"
    return None


def _warn(message: str) -> None:
    typer.secho(f"Warning: {message}", fg="yellow", err=True)


def _check_name(name: str, kind: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ImproperlyConfigured(
            f"{name!r} is not a valid {kind} name. "
            "Use letters, digits and underscores, not starting with a digit."
        )
    if importlib.util.find_spec(name) is not None:
        raise ImproperlyConfigured(
            f"{name!r} conflicts with an existing Python module. Pick another {kind} name."
        )


def _load_project() -> Settings:
    """Import settings and installed apps from the current directory."""
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    settings = get_settings()
    _warn_unregistered_apps(settings)
    try:
        apps.registry.populate(settings.installed_apps)
    except TypeError as error:  # model definition errors name Model.field
        raise ImproperlyConfigured(str(error)) from error
    return settings


def _warn_unregistered_apps(settings: Settings) -> None:
    labels = {name.rsplit(".", 1)[-1] for name in settings.installed_apps}
    settings_file = Path(sys.modules[os.environ[SETTINGS_ENV_VAR]].__file__)
    with contextlib.suppress(ValueError):
        settings_file = settings_file.relative_to(Path.cwd())
    for child in sorted(Path.cwd().iterdir()):
        if child.is_dir() and (child / "models.py").is_file() and child.name not in labels:
            _warn(
                f"{child.name}/ looks like an app but is not in installed_apps ({settings_file})."
            )


@contextlib.contextmanager
def _quiet(verbose: bool) -> Iterator[None]:
    """Hide Alembic's own stdout lines and INFO logs unless --verbose."""
    if verbose:
        yield
        return
    logger = logging.getLogger("alembic")
    level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        logger.setLevel(level)


def _database_state() -> str:
    current = migrations._current_revision()
    return f"Database is at {current}" if current else "Database is at zero (empty)"


@app.command()
@_friendly_errors
def new(
    name: str,
    directory: Annotated[Path | None, typer.Argument(help="Defaults to ./<name>.")] = None,
) -> None:
    """Create a project: settings, manage.py, asgi.py, .env, .gitignore and compose.yaml."""
    _check_name(name, "project")
    root = directory or Path(name)
    files = {
        "manage.py": MANAGE_PY,
        "asgi.py": ASGI_PY,
        ".env": ENV.replace("__KEY__", "fyro-insecure-" + secrets.token_urlsafe(50)),
        ".gitignore": GITIGNORE,
        "compose.yaml": COMPOSE_YAML,
        f"{name}/__init__.py": "",
        f"{name}/settings.py": SETTINGS_PY,
    }
    existing = sorted(path for path in files if (root / path).exists())
    if existing:
        raise ImproperlyConfigured(
            f"{root}/ already has {', '.join(existing)}. fyro new never overwrites files: "
            "use an empty directory."
        )
    for path, content in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(content.replace("__NAME__", name))
    cd = "" if root.resolve() == Path.cwd() else f"  cd {root}\n"
    typer.echo(
        f"Created project {name!r} in {root}/\n\nNext steps:\n{cd}"
        "  docker compose up -d                # Postgres on localhost:5432\n"
        "  python manage.py startapp blog\n\n"
        "Port 5432 taken? Run FYRO_DB_PORT=5433 docker compose up -d and change the port\n"
        "in FYRO_DATABASE_URL in .env."
    )


@app.command()
@_friendly_errors
def startapp(name: str) -> None:
    """Create an app folder with models.py."""
    _check_name(name, "app")
    target = Path(name)
    if target.exists():
        raise ImproperlyConfigured(f"{name}/ already exists. Pick another app name.")
    target.mkdir()
    (target / "__init__.py").touch()
    (target / "models.py").write_text(MODELS_PY.replace("__NAME__", name))
    typer.echo(
        f"Created app {name!r} in {name}/\n\n"
        f'Add it to installed_apps in your settings:\n    installed_apps: list[str] = ["{name}"]'
    )


@app.command()
@_friendly_errors
def makemigrations(
    message: Annotated[str | None, typer.Option("-m", "--message")] = None,
    verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False,
) -> None:
    """Write a migration file for model changes."""
    _load_project()
    with _quiet(verbose):
        path = migrations.makemigrations(message)
    if path is None:
        typer.echo("No changes detected")
    else:
        with contextlib.suppress(ValueError):
            path = path.relative_to(Path.cwd())
        typer.echo(f"Created {path}")


@app.command()
@_friendly_errors
def migrate(
    target: Annotated[str, typer.Argument(help='A revision id, "head" or "zero".')] = "head",
    verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False,
) -> None:
    """Move the database up or down to a migration (default: the latest)."""
    _load_project()
    before = _database_state()
    with _quiet(verbose):
        migrations.migrate(target)
    after = _database_state()
    if after == before:
        typer.echo("No migrations to apply.")
    typer.echo(after)


@app.command()
@_friendly_errors
def runserver(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: Annotated[bool, typer.Option("--reload/--no-reload")] = True,
) -> None:
    """Run the development server (auto-reload on by default)."""
    from fyro.app import create_app

    _load_project()
    create_app()  # surface project errors here, before the server starts
    _warn_unapplied_migrations()
    typer.echo(f"Starting fyro at http://{host}:{port}/docs (Ctrl+C to stop)")
    uvicorn.run("asgi:app", host=host, port=port, reload=reload, app_dir=os.getcwd())


@app.command()
@_friendly_errors
def createsuperuser(
    noinput: Annotated[
        bool,
        typer.Option(
            "--noinput",
            help="Read FYRO_SUPERUSER_USERNAME, FYRO_SUPERUSER_EMAIL and "
            "FYRO_SUPERUSER_PASSWORD from the environment instead of prompting.",
        ),
    ] = False,
) -> None:
    """Create a superuser (needs "fyro.auth" in installed_apps)."""
    _load_project()
    auth = apps.registry.apps.get("auth")
    if auth is None or auth.name != "fyro.auth":
        raise ImproperlyConfigured('createsuperuser needs "fyro.auth" in installed_apps.')
    if noinput:
        username = os.environ.get("FYRO_SUPERUSER_USERNAME")
        password = os.environ.get("FYRO_SUPERUSER_PASSWORD")
        email = os.environ.get("FYRO_SUPERUSER_EMAIL")
        if not username or not password:
            raise ImproperlyConfigured(
                "--noinput needs FYRO_SUPERUSER_USERNAME and FYRO_SUPERUSER_PASSWORD "
                "in the environment (not in .env)."
            )
    else:
        username = typer.prompt("Username")
        email = typer.prompt("Email (optional)", default="", show_default=False)
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    asyncio.run(_create_superuser(username, email or None, password))
    typer.echo(f"Superuser {username!r} created.")


async def _create_superuser(username: str, email: str | None, password: str) -> None:
    import fyro.db
    from fyro.auth import User
    from fyro.auth.passwords import hash_password
    from fyro.db.engine import dispose_engine

    try:
        async with fyro.db.session():
            if await User.objects.filter(username=username).first() is not None:
                raise ImproperlyConfigured(f"User {username!r} already exists.")
            await User.objects.create(
                username=username,
                email=email,
                password=hash_password(password),
                is_superuser=True,
            )
    finally:
        await dispose_engine()


def _warn_unapplied_migrations() -> None:
    directory = migrations._migrations_dir()
    if not directory.is_dir():
        return
    head = migrations._script(migrations._config(directory)).get_current_head()
    try:
        current = migrations._current_revision()
    except (OSError, sqlalchemy.exc.SQLAlchemyError) as error:
        _warn(f"cannot reach the database to check migrations: {error}")
        return
    if current != head:
        _warn("You have unapplied migrations; run migrate.")


DEV_KEY_WARNING = "FYRO_SECRET_KEY is insecure"


def main() -> None:
    # Like Django, don't nag about the generated dev key on every command. With debug off,
    # an insecure key is still an error. PYTHONWARNINGS carries this to runserver's reloader.
    warnings.filterwarnings("ignore", message=DEV_KEY_WARNING, category=UserWarning)
    rule = f"ignore:{DEV_KEY_WARNING}:UserWarning"
    os.environ["PYTHONWARNINGS"] = ",".join(filter(None, [os.environ.get("PYTHONWARNINGS"), rule]))
    app()


if __name__ == "__main__":
    main()
