from itambox.plugins import PluginConfig

class EsignPluginConfig(PluginConfig):
    name = 'itambox_esign'
    verbose_name = 'DocuSign Integration'
    version = '1.0.0'
    author = 'DocuSign Dev Team'
    author_email = 'dev@docusign.com'
    min_version = '1.0.0-alpha'
    graphql_schema = 'itambox_esign.graphql.schema'
    required_settings = [
        'DOCUSIGN_INTEGRATION_KEY',
        'DOCUSIGN_USER_ID',
        'DOCUSIGN_ACCOUNT_ID',
        'DOCUSIGN_RSA_PRIVATE_KEY',
    ]
    default_settings = {
        'DOCUSIGN_SANDBOX': True,
        'DOCUSIGN_BASE_URL': 'https://demo.docusign.net/restapi',
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
