import importlib
import sys

from django.core.exceptions import ImproperlyConfigured
from packaging.version import parse as parse_version

from itambox.dictutils import deep_merge
from itambox.plugins import PluginConfig


def _load_plugin_config(plugin_name):
    try:
        plugin_module = importlib.import_module(plugin_name)
    except ImportError as e:
        raise ImproperlyConfigured(f"Failed to import plugin {plugin_name!r}: {e}") from e

    config_cls = getattr(plugin_module, "config", None)
    if config_cls is None:
        raise ImproperlyConfigured(f"Plugin {plugin_name!r} does not declare a 'config' attribute in its __init__.py.")
    if not issubclass(config_cls, PluginConfig):
        raise ImproperlyConfigured(f"Plugin {plugin_name!r} config class is not a subclass of PluginConfig.")
    return config_cls


def _validate_plugin_version(settings_module, plugin_name, config_cls):
    current_version_str = getattr(settings_module, "VERSION", "0.0.0")
    current_version = parse_version(current_version_str)
    min_version = getattr(config_cls, "min_version", None)
    if min_version and current_version < parse_version(min_version):
        raise ImproperlyConfigured(
            f"Plugin {plugin_name!r} requires minimum ITAMbox version {min_version} (current version is {current_version_str})"
        )

    max_version = getattr(config_cls, "max_version", None)
    if max_version and current_version > parse_version(max_version):
        raise ImproperlyConfigured(
            f"Plugin {plugin_name!r} supports maximum ITAMbox version {max_version} (current version is {current_version_str})"
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


def _append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def load_plugins(settings_module):
    """
    Scans the PLUGINS list in settings, loads each plugin's config class,
    validates settings, merges defaults, registers the plugin and its dependencies
    in INSTALLED_APPS, and registers middlewares in MIDDLEWARE.
    """
    plugins = getattr(settings_module, "PLUGINS", [])
    plugins_config = getattr(settings_module, "PLUGINS_CONFIG", {})
    if not hasattr(settings_module, "PLUGINS_RESOLVED_CONFIG"):
        settings_module.PLUGINS_RESOLVED_CONFIG = {}

    resolved_config = settings_module.PLUGINS_RESOLVED_CONFIG
    installed_apps = list(settings_module.INSTALLED_APPS)
    middleware = list(settings_module.MIDDLEWARE)
    for plugin_name in plugins:
        config_cls = _load_plugin_config(plugin_name)
        _validate_plugin_version(settings_module, plugin_name, config_cls)
        resolved_config[plugin_name] = _resolve_plugin_settings(
            plugin_name,
            config_cls,
            plugins_config,
        )
        _append_unique(installed_apps, getattr(config_cls, "django_apps", []))
        _append_unique(installed_apps, [f"{plugin_name}.{config_cls.__name__}"])
        _append_unique(middleware, getattr(config_cls, "middleware", []))

    settings_module.INSTALLED_APPS = installed_apps
    settings_module.MIDDLEWARE = middleware
