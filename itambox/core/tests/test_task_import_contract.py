"""Runtime guarantees for task-module import behavior."""

import os
import subprocess
import sys
from pathlib import Path

from django.db import connection
from django.test import TestCase

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


class TaskModuleImportTests(TestCase):
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
print(f"TASK_IMPORT_QUERY_COUNT={len(captured)}")
"""
        database = connection.settings_dict
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_SETTINGS_MODULE": os.environ.get("DJANGO_SETTINGS_MODULE", "core.settings"),
                "ITAMBOX_ENV": "dev",
                "ITAMBOX_SECRET_KEY": os.environ.get("ITAMBOX_SECRET_KEY", "task-import-probe-secret"),
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
                    (line for line in result.stdout.splitlines() if line.startswith("TASK_IMPORT_QUERY_COUNT=")),
                    None,
                )
                self.assertEqual(marker, "TASK_IMPORT_QUERY_COUNT=0", result.stdout)
