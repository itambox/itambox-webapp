"""RED ownership-cutover probes for issue #445."""

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.db import connection
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


def _producer_source_files(source_root):
    for source in source_root.rglob("*.py"):
        relative_parts = source.relative_to(source_root).parts
        if not {"tests", "migrations", "__pycache__"}.intersection(relative_parts):
            yield source


def _async_task_literal(node):
    if not isinstance(node, ast.Call) or not node.args:
        return None
    call_name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
    value = node.args[0]
    if call_name == "async_task" and isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value
    return None


def _schedule_func_literal(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "create" or ast.unparse(node.func.value) != "Schedule.objects":
        return None
    return next(
        (
            keyword.value
            for keyword in node.keywords
            if keyword.arg == "func"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ),
        None,
    )


def _literal_producer_paths():
    source_root = Path(__file__).resolve().parents[2]
    for source in _producer_source_files(source_root):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        relative = source.relative_to(source_root).as_posix()
        for node in ast.walk(tree):
            for value in (_async_task_literal(node), _schedule_func_literal(node)):
                if value is not None:
                    yield relative, value.lineno, value.value


def test_every_literal_task_producer_resolves_to_a_callable():
    invalid = []
    for source, line, task_path in _literal_producer_paths():
        try:
            resolved = _resolve(task_path)
        except (ImportError, AttributeError, ValueError) as exc:
            invalid.append(f"{source}:{line}: {task_path} ({type(exc).__name__})")
            continue
        if not callable(resolved):
            invalid.append(f"{source}:{line}: {task_path} (not callable)")
    assert not invalid, "unresolvable literal task producer(s):\n  " + "\n  ".join(sorted(invalid))


class Issue445ZeroQueryImportTests(TestCase):
    """Import probes run out-of-process so class identities in the suite stay stable."""

    def test_boundary_module_imports_perform_zero_queries(self):
        probe = """
import importlib
import sys
import django

django.setup()
from django.db import connection
from django.test.utils import CaptureQueriesContext

with CaptureQueriesContext(connection) as captured:
    importlib.reload(importlib.import_module(sys.argv[1]))
print(f"ISSUE445_QUERY_COUNT={len(captured)}")
"""
        database = connection.settings_dict
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_SETTINGS_MODULE": os.environ.get("DJANGO_SETTINGS_MODULE", "core.settings"),
                "ITAMBOX_ENV": "dev",
                "ITAMBOX_SECRET_KEY": os.environ.get("ITAMBOX_SECRET_KEY", "issue445-subprocess-secret"),
                "ITAMBOX_DB_NAME": str(database["NAME"]),
                "ITAMBOX_DB_USER": str(database["USER"]),
                "ITAMBOX_DB_PASSWORD": str(database["PASSWORD"]),
                "ITAMBOX_DB_HOST": str(database["HOST"]),
                "ITAMBOX_DB_PORT": str(database["PORT"]),
                "ITAMBOX_DB_SSLMODE": str(database.get("OPTIONS", {}).get("sslmode", "disable")),
            }
        )
        for module_name in ZERO_QUERY_IMPORTS:
            with self.subTest(module=module_name):
                result = subprocess.run(
                    [sys.executable, "-c", probe, module_name],
                    cwd=Path(__file__).resolve().parents[2],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                marker = next(
                    (line for line in result.stdout.splitlines() if line.startswith("ISSUE445_QUERY_COUNT=")),
                    None,
                )
                self.assertEqual(marker, "ISSUE445_QUERY_COUNT=0", result.stdout)
