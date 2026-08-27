"""RED ownership-cutover probes for issue #445."""

import importlib

import pytest
from django.test import TestCase

CANONICAL_PAIRS = (
    ("core.tasks.evaluate_alert_rules_task", "extras.tasks.alerts.evaluate_alert_rules_task"),
    ("core.tasks.run_alert_rule_now", "extras.tasks.alerts.run_alert_rule_now"),
    ("core.tasks.generate_scheduled_report_task", "extras.tasks.reports.generate_scheduled_report_task"),
    ("core.tasks.send_webhook_task", "extras.tasks.webhooks.send_webhook_task"),
    ("assets.tasks.notify_new_request_task", "assets.tasks.requests.notify_new_request_task"),
    ("core.tasks.bulk_checkin_task", "assets.tasks.checkin.bulk_checkin_task"),
    ("core.tasks.bulk_checkout_task", "assets.tasks.checkout.bulk_checkout_task"),
    ("core.tasks.calculate_depreciation", "assets.tasks.depreciation.calculate_depreciation"),
    ("core.tasks.bulk_dispose_task", "assets.tasks.disposal.bulk_dispose_task"),
    ("core.tasks.sync_tenant_intune", "assets.tasks.intune_sync.sync_tenant_intune"),
    ("core.tasks.labels.generate_label_batch_task", "assets.tasks.labels.generate_label_batch_task"),
    ("core.tasks.labels.generate_label_pdf_batch_task", "assets.tasks.labels.generate_label_pdf_batch_task"),
)

OLD_CONCRETE_ALIASES = (
    "core.tasks.alerts.evaluate_alert_rules_task",
    "core.tasks.alerts.run_alert_rule_now",
    "core.tasks.reports.generate_scheduled_report_task",
    "core.tasks.webhooks.send_webhook_task",
    "assets.tasks.notify_new_request_task",
    "core.tasks.checkin.bulk_checkin_task",
    "core.tasks.checkout.bulk_checkout_task",
    "core.tasks.depreciation.calculate_depreciation",
    "core.tasks.disposal.bulk_dispose_task",
    "core.tasks.intune_sync.sync_tenant_intune",
    "core.tasks.labels.generate_label_batch_task",
    "core.tasks.labels.generate_label_pdf_batch_task",
)

ZERO_QUERY_IMPORTS = (
    "core.events",
    "core.signals",
    "extras.services.events",
    "extras.signals",
    "extras.tasks.alerts",
    "extras.tasks.reports",
    "extras.tasks.webhooks",
    "assets.tasks.checkin",
    "assets.tasks.checkout",
    "assets.tasks.depreciation",
    "assets.tasks.disposal",
    "assets.tasks.intune_sync",
    "assets.tasks.labels",
    "core.reports.formatting",
    "core.tasks.context",
)


def _resolve(path):
    module_name, attribute = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


def _assert_unresolvable(path):
    try:
        _resolve(path)
    except (ImportError, AttributeError):
        return
    pytest.fail(f"missing issue445 predecessor-path removal contract: {path} still resolves")


@pytest.mark.parametrize(("predecessor", "canonical"), CANONICAL_PAIRS)
def test_candidate_resolves_only_canonical_cutover_paths(predecessor, canonical):
    try:
        resolved = _resolve(canonical)
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"missing issue445 canonical task ownership contract: {canonical} ({type(exc).__name__})")
    assert callable(resolved), f"missing issue445 callable contract: {canonical}"
    _assert_unresolvable(predecessor)


@pytest.mark.parametrize("old_alias", OLD_CONCRETE_ALIASES)
def test_candidate_rejects_concrete_old_task_aliases(old_alias):
    _assert_unresolvable(old_alias)


def test_task_packages_do_not_reexport_moved_callables():
    moved_names = {path.rsplit(".", 1)[1] for old, _new in CANONICAL_PAIRS for path in (old,)}
    for package_name in ("core.tasks", "assets.tasks"):
        package = importlib.import_module(package_name)
        leaked = sorted(name for name in moved_names if hasattr(package, name))
        assert not leaked, f"missing issue445 import-free task-package contract: {package_name} re-exports {leaked}"


class Issue445ZeroQueryImportTests(TestCase):
    """PASS/RED by module: importing a task boundary must never query the ORM."""

    def test_boundary_module_imports_perform_zero_queries(self):
        for module_name in ZERO_QUERY_IMPORTS:
            with self.subTest(module=module_name), self.assertNumQueries(0):
                importlib.reload(importlib.import_module(module_name))
