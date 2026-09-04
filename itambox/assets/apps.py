from django.apps import AppConfig

from itambox.registry import registry


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assets"

    def ready(self):
        # Import signals
        # Import search indexes to register them
        # inline import: app-registry: curated import forms load only after the app registry is ready.
        import assets.forms.import_forms
        import assets.search
        import assets.signals

        # inline imports: app-registry: assets.services / inventory.models_kit_checkout pull in
        # model modules, so they can only be imported once the app registry is ready.
        # Publishes checkout_kit to inventory.models without inventory depending on
        # assets.services (issue #87, phase D).
        from assets.services import checkout_kit
        from inventory.models_kit_checkout import register_kit_checkout

        register_kit_checkout(checkout_kit)

        # inline imports: app-registry: register custom-field validation after all models are loaded.
        from assets.customfields import validate_asset_custom_field_data, validate_asset_type_custom_field_data

        registry.register_custom_field_data_validator(
            self.get_model("AssetType"),
            validate_asset_type_custom_field_data,
        )
        registry.register_custom_field_data_validator(
            self.get_model("Asset"),
            validate_asset_custom_field_data,
        )
