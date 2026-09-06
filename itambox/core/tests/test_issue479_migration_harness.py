import pytest
from django.db import connection
from django.test import SimpleTestCase

from core.tests.migration_harness import IsolatedMigrationTestCase, _child_database_name, isolate_migration_tests


class MigrationHarnessContractTests(SimpleTestCase):
    def test_parent_process_is_db_free(self):
        self.assertEqual(IsolatedMigrationTestCase.databases, set())

    def test_child_database_names_are_scoped_and_distinct(self):
        first = _child_database_name("core/tests/test_issue183_alert_migration.py::TestA::test_one", 101)
        second = _child_database_name("core/tests/test_issue183_alert_migration.py::TestB::test_two", 102)

        self.assertTrue(first.startswith("test_479_ci_isolation_"))
        self.assertTrue(second.startswith("test_479_ci_isolation_"))
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 63)
        self.assertLessEqual(len(second), 63)


@pytest.mark.serial_only
@isolate_migration_tests
class MigrationSchemaIsolationTests(IsolatedMigrationTestCase):
    def test_public_relation_cannot_satisfy_historical_lookup(self):
        name = "test_479_public_fallback_canary"
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE public.{name} (id integer)")
            try:
                cursor.execute("SELECT to_regclass(%s)", [f"public.{name}"])
                self.assertIsNotNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass(%s)", [name])
                self.assertIsNone(cursor.fetchone()[0])
                self.assertNotIn(name, connection.introspection.table_names())
            finally:
                cursor.execute(f"DROP TABLE public.{name}")
