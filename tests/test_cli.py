import asyncio
import os
import sys
import textwrap
import warnings
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

from fyro import Model, apps
from fyro.apps import Registry
from fyro.cli import app
from fyro.conf import get_settings
from fyro.db import engine as db_engine

runner = CliRunner()
BASE_URL = make_url(
    os.environ.get(
        "FYRO_TEST_DATABASE_URL", "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
    )
)
DB_URL = BASE_URL.set(database="fyro_test_cli")
PROJECT_FILES = {
    "manage.py",
    "asgi.py",
    ".env",
    ".gitignore",
    "compose.yaml",
    "mysite/__init__.py",
    "mysite/settings.py",
}
POST = """
    from fyro import Field, Model

    class Post(Model):
        title: str = Field(max_length=200)
        body: str
"""


def run_sql(url, *statements, autocommit=False):
    async def go():
        engine = create_async_engine(
            url, poolclass=NullPool, isolation_level="AUTOCOMMIT" if autocommit else None
        )
        try:
            async with engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(go())


def files_under(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def env_file(root: Path) -> dict[str, str]:
    lines = (root / ".env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("FYRO_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(apps, "registry", Registry())
    # The process-wide engine is global state: anything a test creates (e.g. a request through
    # SessionMiddleware) is discarded afterwards, so it can never point at another test's database.
    monkeypatch.setattr(db_engine, "_async_engine", None)
    get_settings.cache_clear()
    yield
    Model.registry.dispose()
    Model.metadata.clear()
    get_settings.cache_clear()
    for name in list(sys.modules):
        if name.split(".")[0] in ("blog", "mysite"):
            del sys.modules[name]


def assert_one_line_error(result, *fragments):
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert result.output.strip().startswith("Error:")
    assert len(result.output.strip().splitlines()) == 1, result.output
    for fragment in fragments:
        assert fragment in result.output


# --- new -------------------------------------------------------------------


def test_new_writes_north_star_tree(tmp_path):
    result = runner.invoke(app, ["new", "mysite"])
    assert result.exit_code == 0, result.output
    assert files_under(tmp_path / "mysite") == PROJECT_FILES
    for step in ("cd mysite", "docker compose up -d", "FYRO_DB_PORT", "startapp"):
        assert step in result.output


def test_new_env_has_insecure_random_key_debug_and_database_url(tmp_path):
    runner.invoke(app, ["new", "mysite"])
    runner.invoke(app, ["new", "othersite"])
    env = env_file(tmp_path / "mysite")
    assert set(env) == {"FYRO_SECRET_KEY", "FYRO_DEBUG", "FYRO_DATABASE_URL"}
    assert env["FYRO_SECRET_KEY"].startswith("fyro-insecure-")
    assert len(env["FYRO_SECRET_KEY"]) >= len("fyro-insecure-") + 50
    assert env["FYRO_DEBUG"] == "true"
    assert env["FYRO_DATABASE_URL"] == "postgresql+asyncpg://fyro:fyro@localhost:5432/mysite"
    # bug: a hardcoded key shared by every project
    assert env_file(tmp_path / "othersite")["FYRO_SECRET_KEY"] != env["FYRO_SECRET_KEY"]


def test_new_gitignore_and_compose(tmp_path):
    runner.invoke(app, ["new", "mysite"])
    gitignore = (tmp_path / "mysite" / ".gitignore").read_text().splitlines()
    assert ".env" in gitignore
    compose = (tmp_path / "mysite" / "compose.yaml").read_text()
    for fragment in ("image: postgres:16", '"${FYRO_DB_PORT:-5432}:5432"', "POSTGRES_DB: mysite", "pg_isready"):
        assert fragment in compose


def test_new_entry_points_point_at_project_settings(tmp_path):
    runner.invoke(app, ["new", "mysite"])
    for name in ("manage.py", "asgi.py"):
        source = (tmp_path / "mysite" / name).read_text()
        assert 'os.environ.setdefault("FYRO_SETTINGS", "mysite.settings")' in source


def test_new_project_settings_load_with_warning_in_debug(tmp_path, monkeypatch):
    # bug: generated .env breaks Phase 1 rules (extra keys, insecure key outside debug)
    runner.invoke(app, ["new", "mysite"])
    monkeypatch.chdir(tmp_path / "mysite")
    monkeypatch.syspath_prepend(str(tmp_path / "mysite"))
    monkeypatch.setenv("FYRO_SETTINGS", "mysite.settings")
    with pytest.warns(UserWarning, match="FYRO_SECRET_KEY"):
        settings = get_settings()
    assert settings.debug is True
    assert settings.installed_apps == []


def test_new_into_existing_empty_directory(tmp_path):
    result = runner.invoke(app, ["new", "mysite", "."])
    assert result.exit_code == 0, result.output
    assert files_under(tmp_path) == PROJECT_FILES


@pytest.mark.parametrize("name", ["my-site", "1site", "class", "my site"])
def test_new_rejects_invalid_names_and_writes_nothing(tmp_path, name):
    result = runner.invoke(app, ["new", name])
    assert_one_line_error(result, repr(name))
    assert list(tmp_path.iterdir()) == []


def test_new_rejects_name_shadowing_a_module(tmp_path):
    result = runner.invoke(app, ["new", "json"])
    assert_one_line_error(result, "json", "conflicts")
    assert list(tmp_path.iterdir()) == []


def test_new_never_overwrites(tmp_path):
    (tmp_path / "mysite").mkdir()
    (tmp_path / "mysite" / "manage.py").write_text("keep me")
    result = runner.invoke(app, ["new", "mysite"])
    assert_one_line_error(result, "manage.py")
    assert (tmp_path / "mysite" / "manage.py").read_text() == "keep me"
    assert files_under(tmp_path / "mysite") == {"manage.py"}


# --- startapp --------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fresh `fyro new mysite`, entered as the working directory."""
    run_sql(DB_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
    runner.invoke(app, ["new", "mysite"])
    root = tmp_path / "mysite"
    monkeypatch.chdir(root)
    monkeypatch.setenv("FYRO_SETTINGS", "mysite.settings")
    monkeypatch.setenv("FYRO_DATABASE_URL", DB_URL.render_as_string(hide_password=False))
    warnings.simplefilter("ignore", UserWarning)  # the generated insecure key, by design
    return root


def install(root: Path, *labels: str) -> None:
    settings = root / "mysite" / "settings.py"
    source = settings.read_text()
    assert "installed_apps: list[str] = []" in source
    settings.write_text(source.replace("= []", f"= {list(labels)!r}"))


def write_models(root: Path, source: str) -> None:
    (root / "blog" / "models.py").write_text(textwrap.dedent(source))


def test_startapp_creates_app_and_leaves_settings_alone(project):
    settings_before = (project / "mysite" / "settings.py").read_text()
    result = runner.invoke(app, ["startapp", "blog"])
    assert result.exit_code == 0, result.output
    assert files_under(project / "blog") == {"__init__.py", "models.py"}
    compile((project / "blog" / "models.py").read_text(), "models.py", "exec")
    assert '"blog"' in result.output and "installed_apps" in result.output
    assert (project / "mysite" / "settings.py").read_text() == settings_before


def test_startapp_refuses_existing_directory_and_bad_name(project):
    (project / "blog").mkdir()
    assert_one_line_error(runner.invoke(app, ["startapp", "blog"]), "blog")
    assert_one_line_error(runner.invoke(app, ["startapp", "my-app"]), "'my-app'")


# --- makemigrations / migrate ----------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def database():
    run_sql(BASE_URL, "DROP DATABASE IF EXISTS fyro_test_cli", autocommit=True)
    run_sql(BASE_URL, "CREATE DATABASE fyro_test_cli", autocommit=True)
    yield
    run_sql(BASE_URL, "DROP DATABASE fyro_test_cli", autocommit=True)


def test_makemigrations_and_migrate_flow(project):
    runner.invoke(app, ["startapp", "blog"])
    write_models(project, POST)
    install(project, "blog")

    made = runner.invoke(app, ["makemigrations"])
    assert made.exit_code == 0, made.output
    assert "migrations/" in made.output and "_initial.py" in made.output
    # bug: Alembic's own chatter leaks through
    assert "Generating" not in made.output and "INFO" not in made.output

    migrated = runner.invoke(app, ["migrate"])
    assert migrated.exit_code == 0, migrated.output
    rev = next((project / "migrations").glob("*_initial.py")).name.split("-")[1][:12]
    assert f"Database is at {rev}" in migrated.output

    again = runner.invoke(app, ["makemigrations"])
    assert again.exit_code == 0
    assert "No changes detected" in again.output

    zero = runner.invoke(app, ["migrate", "zero"])
    assert zero.exit_code == 0, zero.output
    assert "Database is at zero (empty)" in zero.output


def test_makemigrations_custom_message(project):
    runner.invoke(app, ["startapp", "blog"])
    write_models(project, POST)
    install(project, "blog")
    result = runner.invoke(app, ["makemigrations", "-m", "add posts"])
    assert result.exit_code == 0, result.output
    assert "_add_posts.py" in result.output


def test_app_not_installed_warning(project):
    runner.invoke(app, ["startapp", "blog"])
    result = runner.invoke(app, ["makemigrations"])
    assert "blog/ looks like an app but is not in installed_apps (mysite/settings.py)" in result.output


def test_migrate_without_migrations_is_one_line_error(project):
    assert_one_line_error(runner.invoke(app, ["migrate"]), "makemigrations")


def test_missing_fyro_settings_is_one_line_error(project, monkeypatch):
    monkeypatch.delenv("FYRO_SETTINGS")
    assert_one_line_error(runner.invoke(app, ["makemigrations"]), "FYRO_SETTINGS")


def test_bad_model_type_is_one_line_error(project):
    runner.invoke(app, ["startapp", "blog"])
    write_models(project, POST + "        tags: list[str]\n")
    install(project, "blog")
    assert_one_line_error(runner.invoke(app, ["makemigrations"]), "Post.tags")


# --- runserver -------------------------------------------------------------


class RecordingServer:
    """uvicorn.run stand-in: the server itself is exercised in test_cli_e2e.py."""

    def __init__(self):
        self.calls = []

    def __call__(self, app_path, **options):
        self.calls.append((app_path, options))


@pytest.fixture
def server(monkeypatch):
    recorder = RecordingServer()
    monkeypatch.setattr(uvicorn, "run", recorder)
    return recorder


def test_runserver_defaults_reload_on_localhost(project, server):
    result = runner.invoke(app, ["runserver"])
    assert result.exit_code == 0, result.output
    (app_path, options), = server.calls
    assert app_path == "asgi:app"
    assert options["host"] == "127.0.0.1" and options["port"] == 8000
    assert options["reload"] is True
    assert "http://127.0.0.1:8000/docs" in result.output


def test_runserver_options(project, server):
    runner.invoke(app, ["runserver", "--host", "0.0.0.0", "--port", "9000", "--no-reload"])
    (_, options), = server.calls
    assert (options["host"], options["port"], options["reload"]) == ("0.0.0.0", 9000, False)


def test_runserver_warns_about_unapplied_migrations(project, server):
    runner.invoke(app, ["startapp", "blog"])
    write_models(project, POST)
    install(project, "blog")
    runner.invoke(app, ["makemigrations"])
    result = runner.invoke(app, ["runserver"])
    assert "You have unapplied migrations; run migrate" in result.output
    assert len(server.calls) == 1  # a warning, not a failure


def test_runserver_does_not_start_on_project_error(project, server):
    runner.invoke(app, ["startapp", "blog"])
    write_models(project, POST + "        tags: list[str]\n")
    install(project, "blog")
    assert_one_line_error(runner.invoke(app, ["runserver"]), "Post.tags")
    assert server.calls == []


# --- createsuperuser (phase 8) ---------------------------------------------


def auth_project(project) -> None:
    install(project, "fyro.auth")
    assert runner.invoke(app, ["makemigrations"]).exit_code == 0
    assert runner.invoke(app, ["migrate"]).exit_code == 0


def users():
    async def go():
        engine = create_async_engine(DB_URL, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT username, email, is_superuser, password FROM auth_user")
                )
                return result.all()
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.fixture
def no_auth_modules():
    yield
    for name in list(sys.modules):
        if name.startswith("fyro.auth"):
            del sys.modules[name]


def test_createsuperuser_noinput_from_environment(project, monkeypatch, no_auth_modules):
    auth_project(project)
    monkeypatch.setenv("FYRO_SUPERUSER_USERNAME", "admin")
    monkeypatch.setenv("FYRO_SUPERUSER_PASSWORD", "s3cret-pass")
    result = runner.invoke(app, ["createsuperuser", "--noinput"])
    assert result.exit_code == 0, result.output
    assert "Superuser 'admin' created." in result.output
    ((username, email, superuser, password),) = users()
    assert (username, email, superuser) == ("admin", None, True)
    assert password.startswith("$argon2id$") and "s3cret-pass" not in password


def test_createsuperuser_interactive(project, no_auth_modules):
    auth_project(project)
    result = runner.invoke(
        app, ["createsuperuser"], input="admin\nadmin@example.com\npw-123\npw-123\n"
    )
    assert result.exit_code == 0, result.output
    assert "pw-123" not in result.output  # hidden input
    assert [(u, e, s) for u, e, s, _ in users()] == [("admin", "admin@example.com", True)]


def test_createsuperuser_duplicate_is_one_line_error(project, monkeypatch, no_auth_modules):
    auth_project(project)
    monkeypatch.setenv("FYRO_SUPERUSER_USERNAME", "admin")
    monkeypatch.setenv("FYRO_SUPERUSER_PASSWORD", "pw")
    runner.invoke(app, ["createsuperuser", "--noinput"])
    assert_one_line_error(runner.invoke(app, ["createsuperuser", "--noinput"]), "'admin'", "exists")
    assert len(users()) == 1


def test_createsuperuser_noinput_without_env_is_error(project, no_auth_modules):
    auth_project(project)
    assert_one_line_error(
        runner.invoke(app, ["createsuperuser", "--noinput"]), "FYRO_SUPERUSER_USERNAME"
    )


def test_createsuperuser_without_fyro_auth_is_error(project):
    assert_one_line_error(runner.invoke(app, ["createsuperuser", "--noinput"]), "fyro.auth")


# --- database errors are one line, never a traceback or a leaked password -----


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("postgresql+asyncpg://fyro:fyro@127.0.0.1:1/fyro", "Cannot connect to the database"),
        (str(BASE_URL.set(database="fyro_no_such_db").render_as_string(hide_password=False)), "does not exist"),
        (str(BASE_URL.set(password="wrong-pass").render_as_string(hide_password=False)), "password"),
    ],
    ids=["unreachable", "missing-database", "wrong-password"],
)
@pytest.mark.parametrize("command", [["migrate"], ["makemigrations"]])
def test_database_errors_are_one_line(project, monkeypatch, url, fragment, command):
    monkeypatch.setenv("FYRO_DATABASE_URL", url)
    (project / "migrations").mkdir()
    result = runner.invoke(app, command)
    assert_one_line_error(result, fragment)
    assert "wrong-pass" not in result.output and ":fyro@" not in result.output
