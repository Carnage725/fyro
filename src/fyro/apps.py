import importlib
from dataclasses import dataclass, field
from types import MappingProxyType, ModuleType

from fastapi import APIRouter

from fyro.conf import ImproperlyConfigured


@dataclass
class AppConfig:
    name: str
    label: str
    models_module: ModuleType | None = None
    routes_module: ModuleType | None = None
    admin_module: ModuleType | None = None
    models: dict[str, type] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return self.name.replace(".", "/")


def _import_optional(module_name: str) -> ModuleType | None:
    """Import a module, or return None if that exact module does not exist."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise


class Registry:
    def __init__(self) -> None:
        self._apps: dict[str, AppConfig] = {}
        self._loaded = False

    @property
    def apps(self) -> MappingProxyType[str, AppConfig]:
        return MappingProxyType(self._apps)

    def populate(self, installed_apps: list[str]) -> None:
        """Import every installed app's modules. Runs once; later calls do nothing."""
        if self._loaded:
            return
        for name in installed_apps:
            label = name.rsplit(".", 1)[-1]
            if label in self._apps:
                raise ImproperlyConfigured(
                    f"Apps {self._apps[label].name!r} and {name!r} share the label {label!r}. "
                    "App labels (last part of the dotted path) must be unique: rename one app."
                )
            if _import_optional(name) is None:
                raise ImproperlyConfigured(
                    f"Installed app {name!r} cannot be imported. "
                    "Check the name in installed_apps and that the app is on sys.path."
                )
            # Registered before importing models so models can register themselves.
            app = self._apps[label] = AppConfig(name=name, label=label)

            app.models_module = _import_optional(f"{name}.models")
            if app.models_module is None:
                raise ImproperlyConfigured(
                    f"App {name!r} has no models.py. Create {app.path}/models.py (it may be empty)."
                )

            app.routes_module = _import_optional(f"{name}.routes")
            if app.routes_module is not None and not isinstance(
                getattr(app.routes_module, "router", None), APIRouter
            ):
                raise ImproperlyConfigured(
                    f"{app.path}/routes.py must define `router = APIRouter()`."
                )

            app.admin_module = _import_optional(f"{name}.admin")
        self._loaded = True

    def register_model(self, app_label: str, model_cls: type) -> None:
        """Called by each Model class when it is defined."""
        app = self._apps.get(app_label)
        if app is None:
            raise ImproperlyConfigured(
                f"Model {model_cls.__name__} in {model_cls.__module__!r} belongs to app "
                f"{app_label!r}, which is not installed. Add the app to installed_apps."
            )
        if model_cls.__module__ != f"{app.name}.models":
            raise ImproperlyConfigured(
                f"Model {model_cls.__name__} is defined in {model_cls.__module__!r}. "
                f"Models must be defined in {app.path}/models.py."
            )
        app.models[model_cls.__name__.lower()] = model_cls


registry = Registry()
