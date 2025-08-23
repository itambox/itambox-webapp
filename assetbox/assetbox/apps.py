from django.apps import AppConfig


class AssetBoxConfig(AppConfig):
    name = 'assetbox'
    verbose_name = 'AssetBox System Framework'

    def ready(self):
        from assetbox.registry import registry
        registry.execute_deferred()
