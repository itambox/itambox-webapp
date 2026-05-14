from django.apps import AppConfig

class PluginConfig(AppConfig):
    """
    Base configuration class for ITAMbox plugins.
    Plugins should subclass this in their __init__.py and set config = MyPluginConfig.
    """
    # Default settings for the plugin
    default_settings = {}

    # List of settings keys that must be configured by the user in settings.PLUGINS_CONFIG
    required_settings = []

    # List of middleware classes to be injected into the Django MIDDLEWARE list
    middleware = []

    # List of auxiliary django applications to register in settings.INSTALLED_APPS
    django_apps = []
