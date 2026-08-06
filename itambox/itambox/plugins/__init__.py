from functools import wraps

from django.apps import AppConfig

from itambox.plugins.runtime import record_plugin_failure
from itambox.registry import registry

PLUGIN_API_VERSION = "1.0"


class PluginConfig(AppConfig):
    """Base configuration class for the Experimental ITAMbox plugin API.

    Plugin packages expose a subclass from ``__init__.py`` as ``config``. The
    metadata below is the complete ITAMbox-owned configuration contract for 1.0.
    Django's inherited ``AppConfig`` fields remain implementation details unless
    they are named in the plugin API documentation.
    """

    default_settings = {}
    required_settings = []
    middleware = []
    django_apps = []

    version = ""
    author = ""
    author_email = ""
    base_url = None
    min_version = None
    max_version = None
    min_plugin_api_version = None
    max_plugin_api_version = None
    graphql_schema = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._plugin_active = True

    def ready(self):
        """The default lifecycle hook; subclasses may register supported hooks."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        ready = cls.__dict__.get("ready")
        if ready is None or getattr(ready, "_itambox_isolated", False):
            return

        @wraps(ready)
        def isolated_ready(self):
            self._plugin_active = True
            try:
                with registry.plugin_registration(self.name):
                    return ready(self)
            except Exception as exc:  # broad except: boundary-isolation: one plugin must not abort app startup
                self._plugin_active = False
                record_plugin_failure(
                    self.name,
                    exc,
                    stage="ready",
                    settings_module=getattr(type(self), "_plugin_settings_module", None),
                )
                return None

        isolated_ready._itambox_isolated = True
        cls.ready = isolated_ready
