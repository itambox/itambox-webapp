from django.conf import settings
from itambox.plugins.views import PluginTemplateContent

class AssetDocuSignContent(PluginTemplateContent):
    model = 'assets.asset'

    def left_panel(self):
        # Retrieve settings resolved during startup
        plugin_config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
        api_key = plugin_config.get('DOCUSIGN_API_KEY', 'Unknown')
        sandbox = plugin_config.get('DOCUSIGN_SANDBOX', True)
        sandbox_text = "Sandbox" if sandbox else "Production"

        return f"""
        <div class="card mb-3 shadow-sm">
            <div class="card-header border-0 bg-transparent pb-0">
                <h3 class="card-title text-secondary">DocuSign Status</h3>
            </div>
            <div class="card-body">
                <div class="mb-2">
                    <strong>Status:</strong>
                    <span class="badge bg-success-lt text-success ms-2">
                        <i class="mdi mdi-check-circle-outline"></i> Configured
                    </span>
                </div>
                <div class="small text-muted">
                    <div><strong>API Key:</strong> {api_key}</div>
                    <div><strong>Environment:</strong> {sandbox_text}</div>
                </div>
            </div>
        </div>
        """
