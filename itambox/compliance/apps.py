from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "compliance"

    def ready(self):
        # inline import: app-registry: register the compliance settings check after app loading
        from . import checks  # noqa: F401
