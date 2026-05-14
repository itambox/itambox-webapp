import copy
import importlib
import sys
from django.core.exceptions import ImproperlyConfigured
from itambox.plugins import PluginConfig

def deep_merge(dict1, dict2):
    """
    Recursively merge dict2 into dict1.
    """
    result = copy.deepcopy(dict1)
    for key, value in dict2.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result

def load_plugins(settings_module):
    """
    Scans the PLUGINS list in settings, loads each plugin's config class,
    validates settings, merges defaults, registers the plugin and its dependencies
    in INSTALLED_APPS, and registers middlewares in MIDDLEWARE.
    """
    plugins = getattr(settings_module, 'PLUGINS', [])
    plugins_config = getattr(settings_module, 'PLUGINS_CONFIG', {})

    # Ensure PLUGINS_RESOLVED_CONFIG exists
    if not hasattr(settings_module, 'PLUGINS_RESOLVED_CONFIG'):
        settings_module.PLUGINS_RESOLVED_CONFIG = {}

    resolved_config = settings_module.PLUGINS_RESOLVED_CONFIG

    installed_apps = list(settings_module.INSTALLED_APPS)
    middleware = list(settings_module.MIDDLEWARE)

    for plugin_name in plugins:
        try:
            plugin_module = importlib.import_module(plugin_name)
        except ImportError as e:
            raise ImproperlyConfigured(f"Failed to import plugin '{plugin_name}': {e}")

        config_cls = getattr(plugin_module, 'config', None)
        if config_cls is None:
            raise ImproperlyConfigured(f"Plugin '{plugin_name}' does not declare a 'config' attribute in its __init__.py.")

        if not issubclass(config_cls, PluginConfig):
            raise ImproperlyConfigured(f"Plugin '{plugin_name}' config class is not a subclass of PluginConfig.")

        # Deep-merge default settings with user-supplied settings
        default_settings = getattr(config_cls, 'default_settings', {})
        required_settings = getattr(config_cls, 'required_settings', [])

        user_config = plugins_config.get(plugin_name, {})
        merged_config = deep_merge(default_settings, user_config)

        # Validate required settings
        for key in required_settings:
            if key not in merged_config or merged_config[key] is None:
                raise ImproperlyConfigured(
                    f"Plugin '{plugin_name}' requires setting '{key}' to be defined in PLUGINS_CONFIG."
                )

        resolved_config[plugin_name] = merged_config

        # Register auxiliary django apps
        plugin_django_apps = getattr(config_cls, 'django_apps', [])
        for app in plugin_django_apps:
            if app not in installed_apps:
                installed_apps.append(app)

        # Register the plugin itself as its config class path (importable via __init__)
        plugin_app_config = f"{plugin_name}.{config_cls.__name__}"
        if plugin_app_config not in installed_apps:
            installed_apps.append(plugin_app_config)

        # Register middleware
        plugin_middleware = getattr(config_cls, 'middleware', [])
        for mw in plugin_middleware:
            if mw not in middleware:
                middleware.append(mw)

    settings_module.INSTALLED_APPS = installed_apps
    settings_module.MIDDLEWARE = middleware
