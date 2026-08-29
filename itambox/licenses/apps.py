from django.apps import AppConfig


class LicensesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "licenses"

    def ready(self):
        import licenses.search  # noqa

        # inline imports: app-registry: the concrete reconciliation provider and
        # model-owned port are wired only after every app model is registered.
        from licenses.reconciliation import reconcile_software
        from software.models_reconciliation import register_software_reconciliation

        register_software_reconciliation(reconcile_software)
