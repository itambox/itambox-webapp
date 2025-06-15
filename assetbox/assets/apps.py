from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assets'

    def ready(self):
        # Import signals
        # import assets.signals # Removed signal import
        # Import search indexes to register them
        import assets.search
