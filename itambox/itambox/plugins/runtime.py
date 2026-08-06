import logging
from collections.abc import Mapping, Sequence

from django.apps import apps
from django.conf import settings
from django.utils.html import escape

from itambox.registry import registry

logger = logging.getLogger(__name__)


def _setting_value_strings(value):
    if isinstance(value, str):
        return {value} if len(value) >= 3 else set()
    if isinstance(value, Mapping):
        result = set()
        for item in value.values():
            result.update(_setting_value_strings(item))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result = set()
        for item in value:
            result.update(_setting_value_strings(item))
        return result
    return set()


def _redacted_error(exc, settings_module):
    message = str(exc)
    secret_values = set()
    if settings_module is not None:
        secret_values.update(_setting_value_strings(getattr(settings_module, "PLUGINS_CONFIG", {})))
        secret_values.update(_setting_value_strings(getattr(settings_module, "PLUGINS_RESOLVED_CONFIG", {})))
        secret_values.update(_setting_value_strings(getattr(settings_module, "SECRET_KEY", "")))
    for secret in sorted(secret_values, key=len, reverse=True):
        message = message.replace(secret, "[redacted]")
    message = "[redacted]" if not message else message
    return escape(message)


def _settings_object(settings_module):
    if settings_module is not None:
        return settings_module
    if settings.configured:
        return settings
    return None


def _active_plugin_middleware(settings_module):
    base = list(getattr(settings_module, "_PLUGIN_BASE_MIDDLEWARE", settings_module.MIDDLEWARE))
    for plugin_name in getattr(settings_module, "PLUGINS_ACTIVE", []):
        values = getattr(settings_module, "_PLUGINS_MIDDLEWARE_BY_PLUGIN", {}).get(plugin_name, ())
        for value in values:
            if value not in base:
                base.append(value)
    return base


def _active_plugin_apps(settings_module):
    base = list(getattr(settings_module, "_PLUGIN_BASE_INSTALLED_APPS", settings_module.INSTALLED_APPS))
    for plugin_name in getattr(settings_module, "PLUGINS_ACTIVE", []):
        values = getattr(settings_module, "_PLUGINS_APPS_BY_PLUGIN", {}).get(plugin_name, ())
        for value in values:
            if value not in base:
                base.append(value)
    return base


def record_plugin_failure(plugin_name, exc, *, stage, settings_module=None, compatibility=None):
    """Disable one plugin and publish a safe, operator-visible diagnostic."""
    settings_module = _settings_object(settings_module)
    if settings_module is not None:
        active_plugins = list(getattr(settings_module, "PLUGINS_ACTIVE", []))
        if plugin_name in active_plugins:
            active_plugins.remove(plugin_name)
        settings_module.PLUGINS_ACTIVE = active_plugins
        disabled_plugins = list(getattr(settings_module, "PLUGINS_DISABLED", []))
        if plugin_name not in disabled_plugins:
            disabled_plugins.append(plugin_name)
        settings_module.PLUGINS_DISABLED = disabled_plugins
        settings_module.MIDDLEWARE = _active_plugin_middleware(settings_module)
        settings_module.INSTALLED_APPS = _active_plugin_apps(settings_module)

    registry.clear_plugin(plugin_name)
    failure = exc.__cause__ if isinstance(exc.__cause__, ImportError) else exc
    diagnostic = {
        "plugin": plugin_name,
        "plugin_name": plugin_name,
        "failure_class": type(failure).__name__,
        "stage": stage,
        "compatibility": compatibility or getattr(exc, "compatibility", "not-evaluated"),
        "activation_mode": "opt-in",
        "activation_state": "disabled",
        "active": False,
        "activation_source": "settings.PLUGINS",
        "source": "settings.PLUGINS",
        "value_present": True,
        "error": _redacted_error(exc, settings_module),
    }
    if settings_module is not None:
        diagnostics = [
            row for row in getattr(settings_module, "PLUGINS_DIAGNOSTICS", []) if row["plugin"] != plugin_name
        ]
        diagnostics.append(diagnostic)
        settings_module.PLUGINS_DIAGNOSTICS = diagnostics
    logger.warning(
        "Plugin %s disabled at %s (%s): %s",
        plugin_name,
        stage,
        diagnostic["failure_class"],
        diagnostic["error"],
    )
    return diagnostic


def get_plugin_diagnostics(settings_module=None):
    settings_module = _settings_object(settings_module)
    if settings_module is None:
        return ()
    return tuple(dict(row) for row in getattr(settings_module, "PLUGINS_DIAGNOSTICS", ()))


def is_plugin_active(plugin_name, settings_module=None):
    settings_module = _settings_object(settings_module)
    if settings_module is None or plugin_name not in getattr(settings_module, "PLUGINS_ACTIVE", ()):
        return False
    try:
        return bool(getattr(apps.get_app_config(plugin_name), "_plugin_active", True))
    except LookupError:
        return True
