import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.settings.base import _load_tenant_json_config


class TenantIntegrationConfigTests(SimpleTestCase):
    def test_malformed_json_fails_closed_without_disclosing_value(self):
        secret_value = '{"client_secret": "do-not-log"'

        with patch.dict(os.environ, {"ITAMBOX_TENANT_SAML_CONFIGS": secret_value}):
            with self.assertRaises(ImproperlyConfigured) as raised:
                _load_tenant_json_config("ITAMBOX_TENANT_SAML_CONFIGS")

        message = str(raised.exception)
        self.assertIn("ITAMBOX_TENANT_SAML_CONFIGS", message)
        self.assertIn("column", message)
        self.assertNotIn(secret_value, message)
        self.assertNotIn("do-not-log", message)

    def test_non_object_json_fails_closed(self):
        with patch.dict(os.environ, {"ITAMBOX_TENANT_OIDC_CONFIGS": "[]"}):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "ITAMBOX_TENANT_OIDC_CONFIGS must contain a JSON object.",
            ):
                _load_tenant_json_config("ITAMBOX_TENANT_OIDC_CONFIGS")
