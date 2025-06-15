from django.apps import AppConfig


class OrganizationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'organization'

    def ready(self):
        # Import signals
        # import organization.signals # Removed signal import
        # Import search indexes to register them
        import organization.search
