from rest_framework.routers import DefaultRouter
from django.utils.module_loading import import_string
from itambox.registry import registry

class PluginRouter(DefaultRouter):
    """
    Sub-router for REST API viewsets registered by plugins.
    Allows registering viewsets mapped to /api/plugins/<plugin_name>/<prefix>/.
    """
    pass

router = PluginRouter()
app_name = 'plugins'

for plugin_name, registrations in registry.get_plugin_viewsets().items():
    for prefix, viewset_item, basename in registrations:
        if isinstance(viewset_item, str):
            viewset = import_string(viewset_item)
        else:
            viewset = viewset_item

        if prefix:
            route_path = f"{plugin_name}/{prefix}"
        else:
            route_path = plugin_name
        router.register(route_path, viewset, basename=basename)

urlpatterns = router.urls
