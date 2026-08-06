import importlib
from collections.abc import Sequence

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from packaging.version import InvalidVersion
from packaging.version import parse as parse_version

from itambox.dictutils import deep_merge
from itambox.plugins import PLUGIN_API_VERSION, PluginConfig
from itambox.plugins.runtime import get_plugin_diagnostics, is_plugin_active, record_plugin_failure
from itambox.registry import registry


class PluginCompatibilityError(ImproperlyConfigured):
    """A configured plugin does not match this product or plugin API."""

    def __init__(self, message, compatibility):
        super().__init__(message)
        self.compatibility = compatibility


def _load_plugin_config(plugin_name):
    try:
        with registry.plugin_registration(plugin_name):
            plugin_module = importlib.import_module(plugin_name)
    except ImportError as exc:
        raise ImproperlyConfigured(f"Failed to import plugin {plugin_name!r}: {exc}") from exc

    config_cls = getattr(plugin_module, "config", None)
    if config_cls is None:
        raise ImproperlyConfigured(f"Plugin {plugin_name!r} does not declare a 'config' attribute in its __init__.py.")
    try:
        is_plugin_config = issubclass(config_cls, PluginConfig)
    except TypeError:
        is_plugin_config = False
    if not is_plugin_config:
        raise ImproperlyConfigured(f"Plugin {plugin_name!r} config class is not a subclass of PluginConfig.")
    return config_cls


def _validate_product_version(settings_module, plugin_name, config_cls):
    current_version_str = getattr(settings_module, "VERSION", "0.0.0")
    current_version = parse_version(current_version_str)
    min_version = getattr(config_cls, "min_version", None)
    if min_version and current_version < parse_version(min_version):
        raise PluginCompatibilityError(
            f"Plugin {plugin_name!r} requires minimum ITAMbox version {min_version} (current version is {current_version_str})",
            "incompatible-product",
        )

    max_version = getattr(config_cls, "max_version", None)
    if max_version and current_version > parse_version(max_version):
        raise PluginCompatibilityError(
            f"Plugin {plugin_name!r} supports maximum ITAMbox version {max_version} (current version is {current_version_str})",
            "incompatible-product",
        )


def _validate_config_metadata(plugin_name, config_cls):
    config_name = getattr(config_cls, "name", None)
    if not isinstance(config_name, str) or not config_name.strip():
        raise ImproperlyConfigured(f"Plugin {plugin_name!r} must declare a non-empty PluginConfig.name")
    if config_name != plugin_name:
        raise ImproperlyConfigured(f"Plugin {plugin_name!r} PluginConfig.name must match the configured package name")
    base_url = getattr(config_cls, "base_url", None)
    if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
        raise TypeError(f"Plugin {plugin_name!r} base_url must be a non-empty string or None")


def _validate_plugin_api_version(plugin_name, config_cls):
    min_version = getattr(config_cls, "min_plugin_api_version", None)
    max_version = getattr(config_cls, "max_plugin_api_version", None)
    if min_version is None or max_version is None:
        raise PluginCompatibilityError(
            f"Plugin {plugin_name!r} must declare min_plugin_api_version and max_plugin_api_version for API {PLUGIN_API_VERSION}",
            "missing-plugin-api",
        )
    try:
        current_version = parse_version(PLUGIN_API_VERSION)
        min_supported = parse_version(min_version)
        max_supported = parse_version(max_version)
    except (InvalidVersion, TypeError) as exc:
        raise PluginCompatibilityError(
            f"Plugin {plugin_name!r} declares invalid plugin API compatibility metadata",
            "invalid-plugin-api",
        ) from exc
    if min_supported > current_version or max_supported < current_version:
        raise PluginCompatibilityError(
            f"Plugin {plugin_name!r} does not support ITAMbox plugin API {PLUGIN_API_VERSION}",
            "incompatible-plugin-api",
        )


def _resolve_plugin_settings(plugin_name, config_cls, plugins_config):
    merged_config = deep_merge(
        getattr(config_cls, "default_settings", {}),
        plugins_config.get(plugin_name, {}),
    )
    for key in getattr(config_cls, "required_settings", []):
        if key not in merged_config or merged_config[key] is None:
            raise ImproperlyConfigured(
                f"Plugin {plugin_name!r} requires setting {key!r} to be defined in PLUGINS_CONFIG."
            )
    return merged_config


def _validate_middleware(plugin_name, config_cls):
    middleware = getattr(config_cls, "middleware", ())
    if isinstance(middleware, (str, bytes)) or not isinstance(middleware, Sequence):
        raise TypeError(f"Plugin {plugin_name!r} middleware must be a sequence of dotted paths")
    for middleware_path in middleware:
        if not isinstance(middleware_path, str) or middleware_path.count(".") < 1:
            raise TypeError(f"Plugin {plugin_name!r} middleware entry must be a dotted import path")
        try:
            middleware_class = import_string(middleware_path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"Plugin {plugin_name!r} middleware {middleware_path!r} could not be imported"
            ) from exc
        if not callable(middleware_class):
            raise TypeError(f"Plugin {plugin_name!r} middleware {middleware_path!r} is not callable")


def _append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def load_plugins(settings_module):
    """Load configured plugins without allowing one failure to abort startup.

    Configuration and compatibility failures are isolated before Django app
    population. Failures during ``PluginConfig.ready`` are isolated by the
    wrapper in :class:`PluginConfig`; both paths publish the same diagnostic
    shape and remove only that plugin's contributions.
    """
    plugins = list(getattr(settings_module, "PLUGINS", ()))
    plugins_config = getattr(settings_module, "PLUGINS_CONFIG", {}) or {}
    if not hasattr(settings_module, "_PLUGIN_BASE_INSTALLED_APPS"):
        settings_module._PLUGIN_BASE_INSTALLED_APPS = list(settings_module.INSTALLED_APPS)
    if not hasattr(settings_module, "_PLUGIN_BASE_MIDDLEWARE"):
        settings_module._PLUGIN_BASE_MIDDLEWARE = list(settings_module.MIDDLEWARE)

    settings_module.PLUGINS_RESOLVED_CONFIG = {}
    settings_module.PLUGINS_ACTIVE = []
    settings_module.PLUGINS_DISABLED = []
    settings_module.PLUGINS_DIAGNOSTICS = []
    settings_module._PLUGINS_APPS_BY_PLUGIN = {}
    settings_module._PLUGINS_MIDDLEWARE_BY_PLUGIN = {}
    settings_module.INSTALLED_APPS = list(settings_module._PLUGIN_BASE_INSTALLED_APPS)
    settings_module.MIDDLEWARE = list(settings_module._PLUGIN_BASE_MIDDLEWARE)

    for plugin_name in plugins:
        try:
            config_cls = _load_plugin_config(plugin_name)
            _validate_config_metadata(plugin_name, config_cls)
            _validate_product_version(settings_module, plugin_name, config_cls)
            _validate_plugin_api_version(plugin_name, config_cls)
            resolved_config = _resolve_plugin_settings(plugin_name, config_cls, plugins_config)
            _validate_middleware(plugin_name, config_cls)
            plugin_apps = [*getattr(config_cls, "django_apps", ()), f"{plugin_name}.{config_cls.__name__}"]
            plugin_middleware = list(getattr(config_cls, "middleware", ()))
            if any(not isinstance(app_name, str) or not app_name.strip() for app_name in plugin_apps):
                raise TypeError(f"Plugin {plugin_name!r} django_apps must contain strings")
        except Exception as exc:  # broad except: boundary-isolation: one plugin must not abort app startup
            stage = (
                "import" if isinstance(exc, ImportError) or isinstance(exc.__cause__, ImportError) else "configuration"
            )
            if stage == "configuration" and isinstance(exc, PluginCompatibilityError):
                stage = "compatibility"
            if "middleware" in str(exc).lower():
                stage = "middleware"
            record_plugin_failure(plugin_name, exc, stage=stage, settings_module=settings_module)
            continue

        config_cls._plugin_settings_module = settings_module
        settings_module.PLUGINS_RESOLVED_CONFIG[plugin_name] = resolved_config
        settings_module._PLUGINS_APPS_BY_PLUGIN[plugin_name] = plugin_apps
        settings_module._PLUGINS_MIDDLEWARE_BY_PLUGIN[plugin_name] = plugin_middleware
        _append_unique(settings_module.INSTALLED_APPS, plugin_apps)
        _append_unique(settings_module.MIDDLEWARE, plugin_middleware)
        settings_module.PLUGINS_ACTIVE.append(plugin_name)

    return tuple(settings_module.PLUGINS_ACTIVE)


__all__ = [
    "PluginCompatibilityError",
    "deep_merge",
    "get_plugin_diagnostics",
    "is_plugin_active",
    "load_plugins",
    "record_plugin_failure",
]
