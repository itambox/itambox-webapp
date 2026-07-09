import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.core.management import call_command, CommandError
from django.contrib.auth import get_user_model
from core.managers import set_current_tenant, get_current_tenant
from core.auth.ldap import MultiTenantLDAPBackend
import ldap
from core.auth.saml import load_saml_config
from organization.models import Tenant, Membership

User = get_user_model()


# Dummy tenant LDAP settings
TEST_LDAP_CONFIGS = {
    "tenant-alpha": {
        "SERVER_URI": "ldap://ldap.alpha.com",
        "BIND_DN": "cn=admin,dc=alpha,dc=com",
        "BIND_PASSWORD": "alphapassword",
        "USER_SEARCH": {
            "base_dn": "ou=users,dc=alpha,dc=com",
            "filter": "(uid=%(user)s)",
            "scope": "SUBTREE"
        },
        "REQUIRE_GROUP": "cn=itambox-users,ou=groups,dc=alpha,dc=com"
    },
    "tenant-beta": {
        "SERVER_URI": "ldaps://ldap.beta.org",
        "BIND_DN": "uid=binduser,dc=beta,dc=org",
        "BIND_PASSWORD": "betapassword",
        "USER_SEARCH": {
            "base_dn": "ou=staff,dc=beta,dc=org",
            "filter": "(sAMAccountName=%(user)s)",
            "scope": "ONELEVEL"
        }
    }
}

# Dummy tenant SAML settings
TEST_SAML_CONFIGS = {
    "tenant-alpha": {
        "entityid": "https://alpha.example.com/saml2/metadata/",
        "base_url": "https://alpha.example.com",
        "metadata": {
            "remote": [{"url": "https://idp.alpha.com/metadata"}]
        }
    },
    "tenant-beta": {
        "entityid": "https://beta.example.com/saml2/metadata/",
        "base_url": "https://beta.example.com",
        "metadata": {
            "local": ["/etc/saml/beta_metadata.xml"]
        }
    }
}


@override_settings(
    ITAMBOX_TENANT_LDAP_CONFIGS=TEST_LDAP_CONFIGS,
    ITAMBOX_TENANT_SAML_CONFIGS=TEST_SAML_CONFIGS
)
class MultiTenantAuthTestCase(TestCase):
    def setUp(self):
        set_current_tenant(None)
        self.tenant_alpha = Tenant.objects.create(name="Alpha Tenant", slug="tenant-alpha")
        self.tenant_beta = Tenant.objects.create(name="Beta Tenant", slug="tenant-beta")
        
        # Patch xmlsec binary lookup to avoid SigverError on environments without xmlsec
        import sys
        self.xmlsec_patcher = patch('saml2.sigver.get_xmlsec_binary', return_value=sys.executable)
        self.xmlsec_patcher.start()

        # Patch requests.request to return dummy metadata XML
        self.requests_patcher = patch('requests.request')
        self.mock_request = self.requests_patcher.start()
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<?xml version="1.0" encoding="utf-8"?>
<EntityDescriptor ID="_abc" entityID="https://idp.alpha.com/metadata" xmlns="urn:oasis:names:tc:SAML:2.0:metadata">
    <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
        <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.alpha.com/sso"/>
    </IDPSSODescriptor>
</EntityDescriptor>"""
        mock_resp.content = mock_resp.text.encode('utf-8')
        self.mock_request.return_value = mock_resp

        # Patch builtins.open to return dummy metadata XML for local file checks
        original_open = open
        def mock_open_file(file, *args, **kwargs):
            if isinstance(file, str) and 'beta_metadata.xml' in file:
                from io import BytesIO
                return BytesIO(mock_resp.content)
            return original_open(file, *args, **kwargs)

        self.open_patcher = patch('builtins.open', mock_open_file)
        self.open_patcher.start()

    def tearDown(self):
        set_current_tenant(None)
        self.xmlsec_patcher.stop()
        self.requests_patcher.stop()
        self.open_patcher.stop()

    def test_ldap_settings_routing(self):
        """Test that TenantLDAPSettings dynamically route settings based on the active tenant context."""
        backend = MultiTenantLDAPBackend()

        # 1. No active tenant context
        set_current_tenant(None)
        # Should fallback to global defaults or raises default attribute access
        self.assertIsNone(backend.settings.SERVER_URI)

        # 2. Alpha Tenant active
        set_current_tenant(self.tenant_alpha)
        self.assertEqual(backend.settings.SERVER_URI, "ldap://ldap.alpha.com")
        self.assertEqual(backend.settings.BIND_DN, "cn=admin,dc=alpha,dc=com")
        self.assertEqual(backend.settings.BIND_PASSWORD, "alphapassword")
        # Search base check
        search = backend.settings.USER_SEARCH
        self.assertEqual(search.base_dn, "ou=users,dc=alpha,dc=com")
        self.assertEqual(search.filter_format, "(uid=%(user)s)")

        # 3. Beta Tenant active
        set_current_tenant(self.tenant_beta)
        self.assertEqual(backend.settings.SERVER_URI, "ldaps://ldap.beta.org")
        self.assertEqual(backend.settings.BIND_DN, "uid=binduser,dc=beta,dc=org")
        self.assertEqual(backend.settings.BIND_PASSWORD, "betapassword")
        # Search base check
        search = backend.settings.USER_SEARCH
        self.assertEqual(search.base_dn, "ou=staff,dc=beta,dc=org")
        self.assertEqual(search.filter_format, "(sAMAccountName=%(user)s)")

    def test_saml_config_loader_routing(self):
        """Test that load_saml_config compiles the correct SPConfig per active tenant."""
        # 1. No active tenant context - fallback
        set_current_tenant(None)
        config = load_saml_config()
        self.assertEqual(config.entityid, "https://alpha.example.com/saml2/metadata/")

        # 2. Alpha Tenant active
        set_current_tenant(self.tenant_alpha)
        config_alpha = load_saml_config()
        self.assertEqual(config_alpha.entityid, "https://alpha.example.com/saml2/metadata/")
        self.assertIn("https://alpha.example.com/saml2/acs/", config_alpha.endpoint("assertion_consumer_service"))

        # 3. Beta Tenant active
        set_current_tenant(self.tenant_beta)
        config_beta = load_saml_config()
        self.assertEqual(config_beta.entityid, "https://beta.example.com/saml2/metadata/")
        self.assertIn("https://beta.example.com/saml2/acs/", config_beta.endpoint("assertion_consumer_service"))

    def test_ldap_authenticate_tenant_resolution_by_username(self):
        """Test that MultiTenantLDAPBackend resolves and binds the correct tenant based on UPN suffix."""
        backend = MultiTenantLDAPBackend()

        # Mock simple_bind_s and search results to prevent actual LDAP connection
        with patch('django_auth_ldap.backend.LDAPBackend.authenticate') as mock_auth:
            mock_auth.return_value = None
            
            # Auth query with UPN username matching tenant alpha slug
            backend.authenticate(request=None, username="user1@tenant-alpha", password="password")
            self.assertEqual(get_current_tenant(), self.tenant_alpha)

            set_current_tenant(None)
            # Auth query with domain matching tenant beta slug
            backend.authenticate(request=None, username="user1@tenant-beta.org", password="password")
            self.assertEqual(get_current_tenant(), self.tenant_beta)

    @patch('ldap.initialize')
    def test_sync_tenant_ldap_command(self, mock_ldap_init):
        """Test that sync_tenant_ldap command fetches configurations and syncs users for the specified tenant."""
        # Setup LDAP connection mocks
        mock_conn = MagicMock()
        mock_ldap_init.return_value = mock_conn
        
        # Mock search results returning 1 user entry: 'john.doe'
        mock_conn.search.return_value = 1
        
        # Mock connection results generator: first search result entry then None
        mock_conn.result.side_effect = [
            (ldap.RES_SEARCH_ENTRY, [
                ("uid=john.doe,ou=users,dc=alpha,dc=com", {
                    "uid": [b"john.doe"],
                    "mail": [b"john.doe@alpha.com"],
                    "givenName": [b"John"],
                    "sn": [b"Doe"],
                    "memberOf": [b"cn=itambox-users,ou=groups,dc=alpha,dc=com"]
                })
            ]),
            (None, None)
        ]

        # Call synchronization command for tenant-alpha
        call_command('sync_tenant_ldap', tenant='tenant-alpha')

        # Check that user was created and sync was successful
        user = User.objects.get(username='john.doe')
        self.assertEqual(user.email, 'john.doe@alpha.com')
        self.assertEqual(user.first_name, 'John')
        self.assertEqual(user.last_name, 'Doe')

        # Check tenant membership was created
        membership = Membership.objects.get(user=user, tenant=self.tenant_alpha)
        self.assertEqual(membership.roles.first().name, 'Member')

    def test_sync_tenant_ldap_command_invalid_tenant(self):
        """Test that sync_tenant_ldap raises CommandError for non-existent tenants."""
        with self.assertRaises(CommandError) as context:
            call_command('sync_tenant_ldap', tenant='non-existent')
        self.assertIn("does not exist", str(context.exception))
