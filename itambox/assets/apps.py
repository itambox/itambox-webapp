from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assets"

    def ready(self):
        # Import signals
        # Import search indexes to register them
        import assets.search
        import assets.signals

        # inline imports: app-registry: assets.services / inventory.kit_checkout pull in
        # model modules, so they can only be imported once the app registry is ready.
        # Publishes checkout_kit to inventory.models without inventory depending on
        # assets.services (issue #87, phase D).
        from assets.services import checkout_kit
        from inventory.kit_checkout import register_kit_checkout

        register_kit_checkout(checkout_kit)
