import os
import saml2
from saml2.config import SPConfig
from django.conf import settings
from core.managers import get_current_tenant

def load_saml_config():
    """
    Dynamically constructs and returns the pysaml2 SPConfig object
    configured specifically for the active tenant context.
    """
    tenant = get_current_tenant()
    saml_configs = getattr(settings, 'ITAMBOX_TENANT_SAML_CONFIGS', {})

    tenant_config = None
    if tenant:
        tenant_config = saml_configs.get(tenant.slug)

    if not tenant_config:
        if 'default' in saml_configs:
            tenant_config = saml_configs['default']
        elif saml_configs:
            first_key = list(saml_configs.keys())[0]
            tenant_config = saml_configs[first_key]
        else:
            # Fallback metadata/entityid configuration to allow initialization
            tenant_config = {
                'entityid': 'https://itambox.local/saml2/metadata/',
                'metadata': {'local': []}
            }

    # Resolve active hosts/base URLs
    base_url = tenant_config.get('base_url')
    if not base_url:
        base_url = f"https://{tenant.slug if tenant else 'itambox'}.local"

    sp_config = {
        'entityid': tenant_config.get('entityid', f'{base_url}/saml2/metadata/'),
        'service': {
            'sp': {
                'name': 'ITAMbox SP',
                'endpoints': {
                    'assertion_consumer_service': [
                        (f'{base_url}/saml2/acs/', saml2.BINDING_HTTP_POST),
                    ],
                    'single_logout_service': [
                        (f'{base_url}/saml2/ls/', saml2.BINDING_HTTP_REDIRECT),
                    ],
                },
                'allow_unsolicited': True,
                'authn_requests_signed': tenant_config.get('authn_requests_signed', False),
                'logout_requests_signed': tenant_config.get('logout_requests_signed', False),
                'want_assertions_signed': tenant_config.get('want_assertions_signed', False),
                'want_response_signed': tenant_config.get('want_response_signed', False),
            },
        },
        'metadata': tenant_config.get('metadata', {}),
        'debug': settings.DEBUG,
    }

    # Load and compile Saml2 SPConfig
    config = SPConfig()
    config.load(sp_config)
    return config
