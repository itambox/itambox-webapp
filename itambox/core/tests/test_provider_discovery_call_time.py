import ast
import importlib
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core import tenant_scope
from core.auth import providers
from organization.models import Tenant

SAML_CONFIG = {
    "entityid": "https://acme.example.com/saml2/metadata/",
    "base_url": "https://acme.example.com",
    "metadata": {"remote": [{"url": "https://idp.example.com/metadata"}]},
}
OIDC_CONFIG = {
    "OIDC_RP_CLIENT_ID": "itambox",
    "OIDC_RP_CLIENT_SECRET": "secret",
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example.com/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example.com/token",
    "OIDC_OP_USER_ENDPOINT": "https://idp.example.com/userinfo",
    "OIDC_OP_ISSUER": "https://idp.example.com/",
    "OIDC_OP_JWKS_ENDPOINT": "https://idp.example.com/jwks",
}


class ProviderDiscoveryArchitectureTests(TestCase):
    def test_provider_discovery_has_no_direct_organization_model_import(self):
        source = Path(providers.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "organization.models":
                forbidden.append(ast.unparse(node))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr == "get_model"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "apps"
                ):
                    forbidden.append(ast.unparse(node))
        self.assertEqual(forbidden, [], "provider discovery must resolve its model through core.tenant_scope")

    def test_provider_module_does_not_resolve_or_capture_a_model_at_import(self):
        with patch.object(tenant_scope, "tenant_model", side_effect=AssertionError("import-time model lookup")):
            importlib.reload(providers)

    def test_tenant_model_is_resolved_by_the_owner_at_each_discovery_call(self):
        Tenant.objects.create(name="Acme Corp", slug="acme")
        with (
            override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"acme": SAML_CONFIG}, ITAMBOX_TENANT_OIDC_CONFIGS={}),
            patch.object(tenant_scope, "tenant_model", wraps=tenant_scope.tenant_model) as resolver,
        ):
            first = providers.get_login_providers()
            second = providers.get_login_providers()

        expected = [("SAML", "acme", reverse("saml2_login_tenant", kwargs={"tenant_slug": "acme"}))]
        self.assertEqual([(p["protocol"], p["key"], p["url"]) for p in first], expected)
        self.assertEqual([(p["protocol"], p["key"], p["url"]) for p in second], expected)
        self.assertEqual(resolver.call_count, 2)


class ProviderDiscoveryQueryAndBehaviorTests(TestCase):
    def _configured_saml(self, count, prefix):
        tenants = [
            Tenant(name=f"{prefix} Tenant {index:02d}", slug=f"{prefix}-tenant-{index:02d}") for index in range(count)
        ]
        Tenant.objects.bulk_create(tenants)
        return {tenant.slug: SAML_CONFIG for tenant in tenants}

    def test_live_tenant_query_count_does_not_scale_with_button_count(self):
        one = self._configured_saml(1, "one")
        with override_settings(ITAMBOX_TENANT_SAML_CONFIGS=one, ITAMBOX_TENANT_OIDC_CONFIGS={}):
            with CaptureQueriesContext(connection) as one_queries:
                one_result = providers.get_login_providers()

        many = self._configured_saml(8, "many")
        with override_settings(ITAMBOX_TENANT_SAML_CONFIGS=many, ITAMBOX_TENANT_OIDC_CONFIGS={}):
            with CaptureQueriesContext(connection) as many_queries:
                many_result = providers.get_login_providers()

        self.assertEqual(len(one_result), 1)
        self.assertEqual(len(many_result), 8)
        self.assertEqual(len(one_queries), 1)
        self.assertEqual(len(many_queries), 1)

    def test_behavior_matrix_preserves_suppression_defaults_and_global_oidc(self):
        acme = Tenant.objects.create(name="Acme Corp", slug="acme")
        cases = (
            ({}, {}, []),
            ({"ghost": SAML_CONFIG}, {}, []),
            ({"acme": {**SAML_CONFIG, "enabled": False}}, {}, []),
            ({"acme": SAML_CONFIG}, {}, [("SAML", "acme")]),
            ({"default": SAML_CONFIG}, {}, [("SAML", "acme")]),
            ({}, {"default": OIDC_CONFIG}, []),
            ({}, {}, []),
        )
        for saml, oidc, expected in cases:
            with self.subTest(saml=tuple(saml), oidc=tuple(oidc)):
                with override_settings(
                    ITAMBOX_TENANT_SAML_CONFIGS=saml,
                    ITAMBOX_TENANT_OIDC_CONFIGS=oidc,
                ):
                    result = providers.get_login_providers()
                self.assertEqual([(p["protocol"], p["key"]) for p in result], expected)

        with override_settings(
            ITAMBOX_TENANT_SAML_CONFIGS={},
            ITAMBOX_TENANT_OIDC_CONFIGS={},
            **OIDC_CONFIG,
        ):
            result = providers.get_login_providers()
        self.assertEqual([(p["protocol"], p["key"]) for p in result], [("OIDC", "default")])

        Tenant.objects.create(name="Other Corp", slug="other")
        with override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"default": SAML_CONFIG}, ITAMBOX_TENANT_OIDC_CONFIGS={}):
            self.assertEqual(providers.get_login_providers(), [])
        acme.delete()
        with override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"acme": SAML_CONFIG}, ITAMBOX_TENANT_OIDC_CONFIGS={}):
            self.assertEqual(providers.get_login_providers(), [])
