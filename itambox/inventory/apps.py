from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"

    def ready(self):
        import inventory.search  # noqa

        # inline import: app-registry: register handlers only after inventory models are loaded.
        from core.purge_handlers import register_purge_handler
        from inventory.services import ASSIGNMENT_MODELS, purge_inventory_assignment

        for model in ASSIGNMENT_MODELS:
            register_purge_handler(model._meta.label_lower, purge_inventory_assignment)
