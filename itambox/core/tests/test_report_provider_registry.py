"""Provider registry contract tests."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from assets.reports import AssetSummaryReportProvider
from core.reports.contracts import PUBLIC_REPORT_TYPES, ReportDefinition
from core.reports.registry import get_registered_report_types, get_report_provider, register_report_provider


class ReportProviderRegistryTests(SimpleTestCase):
    def test_every_public_identifier_has_a_provider(self):
        self.assertEqual(set(get_registered_report_types()), set(PUBLIC_REPORT_TYPES))
        self.assertIsInstance(get_report_provider("asset_summary"), AssetSummaryReportProvider)

    def test_duplicate_identifiers_fail_loudly(self):
        duplicate = ReportDefinition()
        duplicate.report_type = "asset_summary"

        with self.assertRaises(ImproperlyConfigured):
            register_report_provider(duplicate)

    def test_compiler_has_no_domain_model_imports(self):
        compiler = Path(__file__).parents[1] / "reports" / "compiler.py"
        source = compiler.read_text(encoding="utf-8")

        self.assertNotIn("from assets.models", source)
        self.assertNotIn("from licenses.models", source)
        self.assertNotIn("from subscriptions.models", source)
        self.assertNotIn("ReportTemplate.REPORT_TYPE_", source)
