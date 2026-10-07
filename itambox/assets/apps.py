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

        # inline import: app-registry: attach dynamic Asset/AssetType definitions to the shared value validator.
        from assets.customfields import asset_custom_field_definitions, asset_type_custom_field_definitions

        registry.register_custom_field_data_definition_provider(
            self.get_model("AssetType"), asset_type_custom_field_definitions
        )
        registry.register_custom_field_data_definition_provider(self.get_model("Asset"), asset_custom_field_definitions)
