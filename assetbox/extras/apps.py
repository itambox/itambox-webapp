from django.apps import AppConfig


class ExtrasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'extras'

    def ready(self):
        # Import signals
        import extras.signals
        # Import search indexes to register them
        import extras.search
