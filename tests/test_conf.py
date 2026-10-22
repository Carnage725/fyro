import itertools
import os
import textwrap
import warnings

import pytest

from fyro.conf import ImproperlyConfigured, get_settings

SECURE_KEY = "s" * 60
URL = "postgresql+asyncpg://fyro:fyro@localhost:5432/fyro"
_counter = itertools.count()


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("FYRO_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings_module(tmp_path, monkeypatch):
    """Write a settings module into tmp_path and point FYRO_SETTINGS at it."""

    def write(body: str) -> str:
        name = f"proj_settings_{next(_counter)}"
        (tmp_path / f"{name}.py").write_text(textwrap.dedent(body))
        monkeypatch.setenv("FYRO_SETTINGS", name)
        return name

    return write


def project(**extra: str) -> str:
    lines = "".join(f"    {k}: {v}\n" for k, v in extra.items())
    return (
        "from fyro import Settings\n"
        "class ProjectSettings(Settings):\n"
        f"    database_url: str = {URL!r}\n"
        f"    secret_key: str = {SECURE_KEY!r}\n"
        '    installed_apps: list[str] = ["blog"]\n' + lines
    )


def test_loads_from_module_and_caches(settings_module):
    settings_module(project())
    settings = get_settings()
    assert type(settings).__name__ == "ProjectSettings"
    assert settings.database_url == URL
    assert settings.installed_apps == ["blog"]
    assert settings.debug is False
    assert get_settings() is settings


def test_env_var_overrides_file(settings_module, monkeypatch):
    settings_module(project())
    monkeypatch.setenv("FYRO_DATABASE_URL", "postgresql+asyncpg://other/db")
    assert get_settings().database_url == "postgresql+asyncpg://other/db"


def test_dotenv_overrides_file(settings_module, tmp_path):
    settings_module(project())
    (tmp_path / ".env").write_text("FYRO_DATABASE_URL=postgresql+asyncpg://dotenv/db\n")
    assert get_settings().database_url == "postgresql+asyncpg://dotenv/db"


def test_env_var_beats_dotenv(settings_module, tmp_path, monkeypatch):
    settings_module(project())
    (tmp_path / ".env").write_text("FYRO_DATABASE_URL=postgresql+asyncpg://dotenv/db\n")
    monkeypatch.setenv("FYRO_DATABASE_URL", "postgresql+asyncpg://env/db")
    assert get_settings().database_url == "postgresql+asyncpg://env/db"


def test_missing_fyro_settings_env_var():
    with pytest.raises(ImproperlyConfigured, match="FYRO_SETTINGS"):
        get_settings()


def test_unimportable_module(monkeypatch):
    monkeypatch.setenv("FYRO_SETTINGS", "does_not_exist_settings")
    with pytest.raises(ImproperlyConfigured, match="does_not_exist_settings"):
        get_settings()


def test_module_without_settings_subclass(settings_module):
    name = settings_module("from fyro import Settings\n")
    with pytest.raises(ImproperlyConfigured, match=f"{name}.*no Settings subclass"):
        get_settings()


def test_module_with_two_settings_subclasses(settings_module):
    settings_module(project() + "class Other(ProjectSettings):\n    pass\n")
    with pytest.raises(ImproperlyConfigured, match="Other.*ProjectSettings|ProjectSettings.*Other"):
        get_settings()


@pytest.mark.parametrize("url", ["postgresql://x/db", "sqlite:///db.sqlite3"])
def test_non_asyncpg_url_rejected(settings_module, monkeypatch, url):
    settings_module(project())
    monkeypatch.setenv("FYRO_DATABASE_URL", url)
    with pytest.raises(ImproperlyConfigured, match=r"postgresql\+asyncpg"):
        get_settings()


def test_missing_secret_key(settings_module):
    settings_module(
        f"""
        from fyro import Settings
        class ProjectSettings(Settings):
            database_url: str = {URL!r}
        """
    )
    with pytest.raises(ImproperlyConfigured, match="FYRO_SECRET_KEY"):
        get_settings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("blog, shop ,", ["blog", "shop"]),
        ('["a", "b"]', ["a", "b"]),
        ("solo", ["solo"]),
    ],
)
def test_list_from_env(settings_module, monkeypatch, raw, expected):
    settings_module(project())
    monkeypatch.setenv("FYRO_INSTALLED_APPS", raw)
    assert get_settings().installed_apps == expected


def test_list_from_dotenv(settings_module, tmp_path):
    settings_module(project())
    (tmp_path / ".env").write_text("FYRO_INSTALLED_APPS=blog,shop\n")
    assert get_settings().installed_apps == ["blog", "shop"]


@pytest.mark.parametrize("key", ["fyro-insecure-" + "x" * 60, "short-key"])
def test_insecure_key_warns_in_debug(settings_module, monkeypatch, key):
    settings_module(project(debug="bool = True"))
    monkeypatch.setenv("FYRO_SECRET_KEY", key)
    with pytest.warns(UserWarning, match="FYRO_SECRET_KEY"):
        get_settings()


@pytest.mark.parametrize("key", ["fyro-insecure-" + "x" * 60, "short-key"])
def test_insecure_key_errors_without_debug(settings_module, monkeypatch, key):
    settings_module(project())
    monkeypatch.setenv("FYRO_SECRET_KEY", key)
    with pytest.raises(ImproperlyConfigured, match="FYRO_SECRET_KEY"):
        get_settings()


def test_secure_key_no_warning(settings_module):
    settings_module(project())
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        get_settings()
