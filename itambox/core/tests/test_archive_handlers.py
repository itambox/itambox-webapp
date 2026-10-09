"""Shared aggregate-archive plumbing and its behaviour-table system check (#619)."""

import uuid
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from core import archive_handlers
from core.archive_handlers import (
    ArchiveBehaviour,
    ArchiveBlocked,
    ArchiveOperation,
    ArchiveRelation,
    ArchiveResult,
    archive_table,
    archive_table_problems,
    reverse_relation_keys,
)
from core.checks import check_archive_behaviour_tables
from organization.models import AssetHolder

LABEL = AssetHolder._meta.label_lower


class ArchiveTableCheckTests(TestCase):
    def test_registered_holder_table_matches_model_graph(self):
        self.assertEqual(archive_table_problems(), [])
        self.assertEqual(check_archive_behaviour_tables(None), [])
        declared = {row.relation for row in archive_table(LABEL)}
        self.assertEqual(declared, reverse_relation_keys(AssetHolder))

    def test_undeclared_relation_is_an_error(self):
        table = tuple(row for row in archive_table(LABEL) if row.relation != "subscriptions")
        with patch.dict(archive_handlers._ARCHIVE_TABLES, {LABEL: table}):
            errors = check_archive_behaviour_tables(None)
        self.assertEqual([e.id for e in errors], ["core.E003"])
        self.assertIn("'subscriptions' has no archive behaviour", errors[0].msg)

    def test_stale_and_duplicate_rows_are_errors(self):
        table = archive_table(LABEL) + (
            ArchiveRelation("subscriptions", ArchiveBehaviour.DETACH),
            ArchiveRelation("gone.model.field", ArchiveBehaviour.KEEP),
        )
        with patch.dict(archive_handlers._ARCHIVE_TABLES, {LABEL: table}):
            problems = archive_table_problems()
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("declared twice" in p for p in problems))
        self.assertTrue(any("does not exist" in p for p in problems))


class ArchivePrimitiveTests(SimpleTestCase):
    def test_result_defaults_describe_a_noop(self):
        self.assertEqual(ArchiveResult(), ArchiveResult(0, 0, 0, None))

    def test_blocked_keeps_blockers_and_user_message(self):
        blocked = ArchiveBlocked("Nope", blockers=["x"])
        self.assertEqual(blocked.blockers, ("x",))
        self.assertEqual(blocked.user_message, "Nope")

    def test_operation_carries_root_identity_and_unique_id(self):
        holder = AssetHolder(pk=7)
        first, second = ArchiveOperation.begin(holder), ArchiveOperation.begin(holder)
        self.assertIsInstance(first.id, uuid.UUID)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.detach_message(), "Detached from organization.AssetHolder 7")
