import functools
import importlib
import json
import os
import warnings
from typing import Any

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SETTINGS_ENV_VAR = "FYRO_SETTINGS"
INSECURE_KEY_PREFIX = "fyro-insecure-"
MIN_SECRET_KEY_LENGTH = 50


class ImproperlyConfigured(Exception):
    """fyro is set up wrong. The message says what and how to fix it."""


class Settings(BaseSettings):
    # enable_decoding=False is the class-wide NoDecode: env strings reach
    # _parse_list_fields raw, so list[str] fields re-declared in subclasses
    # still accept both JSON and comma-separated values.
    model_config = SettingsConfigDict(
        env_prefix="FYRO_", env_file=".env", enable_decoding=False
    )

    database_url: str
    secret_key: str
    debug: bool = False
    installed_apps: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _parse_list_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            value = data.get(name)
            if field.annotation == list[str] and isinstance(value, str):
                value = value.strip()
                if value.startswith("["):
                    data[name] = json.loads(value)
                else:
                    data[name] = [item.strip() for item in value.split(",") if item.strip()]
        return data

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            scheme = value.split("://", 1)[0]
            raise ValueError(
                f"scheme {scheme!r} is not supported. fyro needs Postgres via asyncpg: "
                "use 'postgresql+asyncpg://user:password@host:5432/dbname'"
            )
        return value

    @model_validator(mode="after")
    def _check_secret_key(self) -> "Settings":
        key = self.secret_key
        if key.startswith(INSECURE_KEY_PREFIX) or len(key) < MIN_SECRET_KEY_LENGTH:
            message = (
                f"FYRO_SECRET_KEY is insecure (it starts with {INSECURE_KEY_PREFIX!r} or is "
                f"shorter than {MIN_SECRET_KEY_LENGTH} characters). Set FYRO_SECRET_KEY to a "
                "random value, e.g. python -c 'import secrets; print(secrets.token_urlsafe(50))'"
            )
            if not self.debug:
                raise ValueError(message)
            warnings.warn(message, stacklevel=2)
        return self


@functools.cache
def get_settings() -> Settings:
    """Load, validate and cache the project's Settings subclass."""
    module_name = os.environ.get(SETTINGS_ENV_VAR)
    if not module_name:
        raise ImproperlyConfigured(
            f"{SETTINGS_ENV_VAR} is not set. Set it to your settings module, "
            f"e.g. {SETTINGS_ENV_VAR}=myproject.settings"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImproperlyConfigured(
            f"Cannot import settings module {module_name!r} named by {SETTINGS_ENV_VAR}: "
            f"{exc}. Check the dotted path and that the project is on sys.path."
        ) from exc

    found = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, Settings)
        and obj is not Settings
        and obj.__module__ == module.__name__
    ]
    if len(found) != 1:
        names = ", ".join(cls.__name__ for cls in found)
        problem = f"defines {len(found)} ({names})" if found else "has no Settings subclass"
        raise ImproperlyConfigured(
            f"Settings module {module_name!r} {problem}. "
            "Define exactly one class that subclasses fyro.Settings."
        )

    settings_cls = found[0]
    try:
        return settings_cls()
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"]) or "settings"
            line = f"  {field}: {error['msg']}"
            if error["type"] == "missing":
                line += f". Set FYRO_{field.upper()} in the environment or .env"
            lines.append(line)
        raise ImproperlyConfigured(
            f"Invalid settings in {settings_cls.__module__}.{settings_cls.__name__}:\n"
            + "\n".join(lines)
        ) from exc
