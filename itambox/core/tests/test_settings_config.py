import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.settings import base
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


class RequisitionAutoApprovalConfigTests(SimpleTestCase):
    def test_legacy_setting_is_loaded_with_a_startup_deprecation_warning(self):
        loader = getattr(base, "_load_requisition_auto_approval_thresholds", None)
        self.assertTrue(callable(loader), "requisition threshold loader is missing")

        with patch.dict(
            os.environ,
            {"REQUISITION_AUTO_APPROVAL_THRESHOLDS": '{"accessory": 3, "consumable": 5}'},
            clear=True,
        ):
            with self.assertWarnsRegex(
                UserWarning,
                "ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS",
            ) as captured:
                thresholds = loader()
        self.assertEqual(thresholds, {"accessory": 3, "consumable": 5})
        self.assertIn("deprecated", str(captured.warning).lower())

    def test_canonical_setting_wins_over_legacy_fallback(self):
        loader = base._load_requisition_auto_approval_thresholds
        with patch.dict(
            os.environ,
            {
                "ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS": '{"accessory": 1}',
                "REQUISITION_AUTO_APPROVAL_THRESHOLDS": '{"accessory": 9}',
            },
            clear=True,
        ):
            value = loader()

        self.assertEqual(value, {"accessory": 1})

    def test_absent_setting_disables_automatic_approval(self):
        with patch.dict(os.environ, {}, clear=True):
            value = base._load_requisition_auto_approval_thresholds()

        self.assertIsNone(value)

    def test_malformed_setting_fails_closed_without_disclosing_value(self):
        invalid_value = "not-json-private-thresholds"
        with patch.dict(
            os.environ,
            {"ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS": invalid_value},
            clear=True,
        ):
            with self.assertRaises(ImproperlyConfigured) as captured:
                base._load_requisition_auto_approval_thresholds()

        self.assertNotIn(invalid_value, str(captured.exception))

    def test_threshold_values_must_be_non_negative_integers_for_supported_items(self):
        loader = base._load_requisition_auto_approval_thresholds

        invalid_values = (
            '{"accessory": -1}',
            '{"consumable": "5"}',
            '{"component": 2}',
        )
        for raw in invalid_values:
            with self.subTest(raw=raw):
                with patch.dict(
                    os.environ,
                    {"ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS": raw},
                    clear=True,
                ):
                    with self.assertRaises(ImproperlyConfigured):
                        loader()
