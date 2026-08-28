"""RED contracts for issue #446 domain-internal boundary removal."""

import ast
import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ITAMBOX_ROOT = REPO_ROOT / "itambox"


def _source(relative_path: str) -> str:
    return (ITAMBOX_ROOT / relative_path).read_text(encoding="utf-8")


def _has_import_from(source: str, module: str) -> bool:
    tree = ast.parse(source)
    return any(isinstance(node, ast.ImportFrom) and node.module == module for node in ast.walk(tree))


class Issue446ModuleBoundaryRedTests(unittest.TestCase):
    def test_classifier_native_support_and_port_modules_exist(self):
        expected = (
            "inventory.models_mixins",
            "inventory.models_stock",
            "inventory.models_kit_checkout",
            "assets.model_book_value",
            "software.models_reconciliation",
            "subscriptions.seat_services",
            "subscriptions.models_seat_usage",
            "compliance.audit_services",
        )
        for module in expected:
            with self.subTest(module=module):
                self.assertIsNotNone(
                    importlib.util.find_spec(module),
                    f"issue #446 requires classifier-native module {module}",
                )

    def test_legacy_boundary_modules_are_removed(self):
        for module in (
            "inventory.mixins",
            "inventory.stock",
            "inventory.kit_checkout",
        ):
            with self.subTest(module=module):
                self.assertIsNone(
                    importlib.util.find_spec(module),
                    f"legacy mixed/service module {module} must not remain",
                )

    def test_inventory_models_import_model_support_leaves(self):
        source = _source("inventory/models.py")
        self.assertTrue(_has_import_from(source, "models_stock"))
        self.assertTrue(_has_import_from(source, "models_kit_checkout"))
        self.assertFalse(_has_import_from(source, "stock"))
        self.assertFalse(_has_import_from(source, "kit_checkout"))

    def test_inventory_model_mixin_is_not_imported_from_presentation(self):
        source = _source("inventory/abstract_models.py")
        self.assertTrue(_has_import_from(source, "models_mixins"))
        self.assertFalse(_has_import_from(source, "mixins"))

    def test_asset_model_imports_book_value_leaf_at_module_top(self):
        source = _source("assets/models/asset.py")
        self.assertTrue(_has_import_from(source, "assets.model_book_value"))
        self.assertFalse(_has_import_from(source, "assets.depreciation"))

    def test_software_model_uses_model_owned_reconciliation_port(self):
        source = _source("software/models.py")
        self.assertTrue(_has_import_from(source, "software.models_reconciliation"))
        self.assertFalse(_has_import_from(source, "licenses.reconciliation"))

    def test_subscription_model_uses_model_owned_seat_port(self):
        source = _source("subscriptions/models.py")
        self.assertTrue(_has_import_from(source, "subscriptions.models_seat_usage"))
        self.assertFalse(_has_import_from(source, "licenses.models"))

    def test_compliance_model_has_no_actorless_expected_assets_property(self):
        source = _source("compliance/models.py")
        tree = ast.parse(source)
        names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("expected_assets_queryset", names)

    def test_production_consumers_do_not_read_raw_reconciliation_rows(self):
        forbidden = '.reconciliation_report.get("rows", [])'
        offenders = []
        for path in ITAMBOX_ROOT.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if forbidden in source:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [], offenders)

    def test_license_registration_is_owned_by_licenses_config(self):
        licenses_source = _source("licenses/apps.py")
        software_source = _source("software/apps.py")
        self.assertIn("reconcile_software", licenses_source)
        self.assertIn("register_software_reconciliation", licenses_source)
        self.assertNotIn("reconcile_software", software_source)


if __name__ == "__main__":
    unittest.main()
