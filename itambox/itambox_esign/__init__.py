from itambox.plugins import PluginConfig

class EsignPluginConfig(PluginConfig):
    name = 'itambox_esign'
    verbose_name = 'DocuSign Integration'
    required_settings = ['DOCUSIGN_API_KEY']
    default_settings = {
        'DOCUSIGN_API_KEY': None,
        'DOCUSIGN_SANDBOX': True,
    }

    def ready(self):
        super().ready()

        # 1. Register Template Injections
        from itambox.registry import registry
        from .template_content import AssetDocuSignContent
        registry.register_plugin_template_content('assets.asset', AssetDocuSignContent)

        # 2. Register REST API viewset
        from itambox.registry import registry
        registry.register_plugin_viewset('itambox_esign', '', 'itambox_esign.api.views.DocuSignViewSet', basename='itambox_esign')

        # 3. Register Sidebar menu items
        from .navigation import EsignNavigationMenu
        registry.register_plugin_menu(EsignNavigationMenu)

config = EsignPluginConfig
