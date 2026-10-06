"""Regression contracts for the issue #100 import-cycle boundaries."""

from django.test import SimpleTestCase

from core.tests.test_import_boundaries import _edges, _imports


class Issue100ImportBoundaryTests(SimpleTestCase):
    def test_import_service_does_not_import_presentation_or_domains(self):
        forbidden_prefixes = ("assets", "extras", "inventory", "itambox.views", "licenses", "organization", "users")
        self.assertFalse(
            any(
                name == prefix or name.startswith(f"{prefix}.")
                for name in _edges("core.importers.bulk_forms", False)
                for prefix in forbidden_prefixes
            )
        )

    def test_csv_worker_does_not_import_views(self):
        self.assertFalse(
            _imports("core.tasks.csv_import", "itambox.views"),
            "background import workers must depend on the import service, not CBVs",
        )

    def test_report_exporter_does_not_import_tasks(self):
        self.assertFalse(
            _imports("core.reports.exporters", "core.tasks"),
            "report exporters must depend on a renderer below reports and tasks",
        )

    def test_generic_view_package_does_not_import_feature_views(self):
        self.assertFalse(
            _imports("itambox.views.generic.__init__", "itambox.views.features"),
            "generic package initialization must not import feature views",
        )

    def test_feature_views_do_not_import_generic_package_initializer(self):
        self.assertFalse(
            _imports("itambox.views.features", "itambox.views.generic.__init__"),
            "feature views must import concrete generic modules",
        )
