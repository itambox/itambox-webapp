from django.apps import AppConfig


class ExtrasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'extras'

    def ready(self):
        # Import search indexes to register them
        import extras.search
        # Import signals to register them
        import extras.signals
