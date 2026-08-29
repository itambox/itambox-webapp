"""Contracts for the read-only migration-baseline recognition preflight."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.migration_preflight import classify_applied_migrations, load_manifest


def _manifest(*, layout="transitional"):
    return {
        "schema_version": 1,
        "layout": layout,
        "first_party_apps": ["assets"],
        "historical_ids": ["assets.0001_initial", "assets.0002_second"],
        "replacement_ids": ["assets.0100_other", "assets.0100_shard"],
        "replacement_target_ids": ["assets.0001_initial", "assets.0002_second"],
        "baseline_ids": ["assets.0100_other", "assets.0100_shard"],
        "post_transition_ids": ["assets.0101_current"],
        "post_transition_leaf_ids": ["assets.0101_current"],
        "current_leaf_ids": ["assets.0101_current"],
        "transition_release_sha": "b" * 40,
        "supported_predecessors": [
            {
                "name": "test-predecessor",
                "revision": "b" * 40,
                "state": "complete-old-history",
            }
        ],
    }


class MigrationBaselineClassifierTests(SimpleTestCase):
    def assert_state(self, applied, expected_state, *, layout="transitional", exit_code=None):
        result = classify_applied_migrations(set(applied), _manifest(layout=layout))
        self.assertEqual(result.state, expected_state)
        if exit_code is not None:
            self.assertEqual(result.exit_code, exit_code)
        return result

    def test_complete_replacement_recognition_requires_both_transition_sets(self):
        result = self.assert_state(
            {
                "assets.0001_initial",
                "assets.0002_second",
                "assets.0100_other",
                "assets.0100_shard",
                "assets.0101_current",
            },
            "complete-replacement-recognition",
            exit_code=0,
        )
        self.assertEqual(result.reason_code, "REPLACEMENT_RECOGNIZED")

    def test_complete_old_history_without_replacement_is_rejected(self):
        result = self.assert_state(
            {"assets.0001_initial", "assets.0002_second"},
            "complete-old-history-no-replacement",
            exit_code=1,
        )
        self.assertEqual(result.reason_code, "TRANSITION_RELEASE_REQUIRED")
        self.assertIn("ordinary", result.remediation)

    def test_partial_old_history_is_rejected(self):
        result = self.assert_state({"assets.0001_initial"}, "partial-old-history", exit_code=1)
        self.assertEqual(result.reason_code, "PARTIAL_OLD_HISTORY")

    def test_partial_replacement_set_is_rejected(self):
        result = self.assert_state({"assets.0100_shard"}, "partial-replacement-set", exit_code=1)
        self.assertEqual(result.reason_code, "PARTIAL_REPLACEMENT_SET")

    def test_partial_post_transition_state_is_rejected(self):
        result = self.assert_state(
            {"assets.0001_initial", "assets.0002_second", "assets.0100_other", "assets.0100_shard"},
            "partial-post-transition-state",
            exit_code=1,
        )
        self.assertEqual(result.reason_code, "POST_TRANSITION_INCOMPLETE")

    def test_mixed_or_unknown_first_party_rows_are_rejected(self):
        result = self.assert_state(
            {"assets.0001_initial", "assets.0100_shard", "assets.9999_unknown"},
            "mixed-or-unknown-first-party-state",
            exit_code=1,
        )
        self.assertEqual(result.reason_code, "UNKNOWN_FIRST_PARTY_MIGRATION")
        self.assertEqual(result.unexpected_ids, ("assets.9999_unknown",))

    def test_empty_database_is_rejected_without_being_called_normalized(self):
        result = self.assert_state(set(), "empty-or-unmigrated", exit_code=1)
        self.assertEqual(result.reason_code, "NO_FIRST_PARTY_MIGRATIONS")

    def test_normalized_layout_is_a_distinct_success_state_and_allows_stale_history(self):
        result = self.assert_state(
            {
                "assets.0001_initial",
                "assets.0100_other",
                "assets.0100_shard",
                "assets.0101_current",
            },
            "current-normalized-baseline",
            layout="normalized",
            exit_code=0,
        )
        self.assertEqual(result.reason_code, "NORMALIZED_BASELINE")

    def test_normalized_layout_rejects_an_incomplete_baseline(self):
        result = self.assert_state(
            {"assets.0100_shard"},
            "partial-normalized-baseline",
            layout="normalized",
            exit_code=1,
        )
        self.assertEqual(result.reason_code, "NORMALIZED_BASELINE_INCOMPLETE")


class MigrationBaselineManifestTests(SimpleTestCase):
    def test_checked_manifest_has_the_expected_current_layout_and_complete_sets(self):
        manifest = load_manifest()
        self.assertEqual(manifest["layout"], "transitional")
        self.assertEqual(len(manifest["historical_ids"]), 262)
        self.assertEqual(len(manifest["replacement_ids"]), 62)
        self.assertEqual(len(manifest["replacement_target_ids"]), 262)
        self.assertEqual(len(manifest["post_transition_ids"]), 28)
        self.assertEqual(len(manifest["post_transition_leaf_ids"]), 8)
        self.assertEqual(manifest["baseline_ids"], manifest["replacement_ids"])


class MigrationBaselineCommandTests(SimpleTestCase):
    def test_command_emits_only_safe_json_and_never_records_state(self):
        manifest = _manifest()
        applied = {
            (item.split(".", 1)[0], item.split(".", 1)[1])
            for item in manifest["historical_ids"] + manifest["replacement_ids"] + manifest["post_transition_ids"]
        }
        stdout = io.StringIO()
        with (
            patch("core.management.commands.migration_baseline_preflight.load_manifest", return_value=manifest),
            patch("core.management.commands.migration_baseline_preflight.MigrationRecorder") as recorder_cls,
        ):
            recorder = recorder_cls.return_value
            recorder.has_table.return_value = True
            recorder.applied_migrations.return_value = applied
            call_command("migration_baseline_preflight", format="json", stdout=stdout)
            recorder.record_applied.assert_not_called()
            recorder.record_unapplied.assert_not_called()
            recorder.Migration.objects.create.assert_not_called()
            recorder.Migration.objects.update.assert_not_called()
            recorder.Migration.objects.delete.assert_not_called()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["state"], "complete-replacement-recognition")
        self.assertEqual(payload["reason_code"], "REPLACEMENT_RECOGNIZED")
        rendered = stdout.getvalue().lower()
        for forbidden in ("password", "database_url", "secret", "token"):
            self.assertNotIn(forbidden, rendered)

    def test_command_rejects_unsupported_state_with_safe_remediation(self):
        stdout = io.StringIO()
        with (
            patch("core.management.commands.migration_baseline_preflight.load_manifest", return_value=_manifest()),
            patch("core.management.commands.migration_baseline_preflight.MigrationRecorder") as recorder_cls,
        ):
            recorder = recorder_cls.return_value
            recorder.has_table.return_value = True
            recorder.applied_migrations.return_value = {
                ("assets", "0001_initial"),
                ("assets", "0002_second"),
            }
            with self.assertRaises(CommandError) as raised:
                call_command("migration_baseline_preflight", format="json", stdout=stdout)

        self.assertIn("TRANSITION_RELEASE_REQUIRED", str(raised.exception))
        self.assertIn("ordinary", str(raised.exception))


@pytest.mark.serial_only
class PostgreSQLMigrationBaselineCommandTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest("migration-baseline integration requires PostgreSQL")

    def test_current_database_recognition_is_read_only(self):
        recorder = MigrationRecorder(connection)
        before = recorder.applied_migrations()
        stdout = io.StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command("migration_baseline_preflight", format="json", stdout=stdout)
        after = recorder.applied_migrations()

        self.assertEqual(before, after)
        self.assertEqual(json.loads(stdout.getvalue())["state"], "complete-replacement-recognition")
        statements = [query["sql"].lstrip().upper() for query in queries.captured_queries]
        self.assertTrue(statements)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements), statements)
