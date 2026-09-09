import io
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import SimpleTestCase

from core.tests import migration_harness
from core.tests.migration_harness import (
    IsolatedMigrationTestCase,
    _child_database_name,
    _child_environment,
    _child_pytest_args,
    _cleanup_child_database,
    _create_child_database,
    _emit_phase,
    _is_owned_database_name,
    _run_isolated_test,
    isolate_migration_tests,
)


class MigrationHarnessContractTests(SimpleTestCase):
    def test_parent_process_is_db_free(self):
        self.assertEqual(IsolatedMigrationTestCase.databases, set())

    def test_empty_child_database_has_no_sequences_to_reset(self):
        with (
            patch("core.tests.migration_harness._is_isolated_child", return_value=True),
            patch.object(connection.introspection, "table_names", return_value=[]),
            patch("django.test.TransactionTestCase._reset_sequences") as reset,
        ):
            IsolatedMigrationTestCase._reset_sequences("default")
        reset.assert_not_called()

    def test_populated_child_database_keeps_normal_sequence_reset(self):
        with (
            patch("core.tests.migration_harness._is_isolated_child", return_value=True),
            patch.object(connection.introspection, "table_names", return_value=["auth_group"]),
            patch("django.test.TransactionTestCase._reset_sequences") as reset,
        ):
            IsolatedMigrationTestCase._reset_sequences("default")
        reset.assert_called_once_with("default")

    def test_child_database_names_are_scoped_and_distinct(self):
        first = _child_database_name("core/tests/test_issue183_alert_migration.py::TestA::test_one", 101)
        second = _child_database_name("core/tests/test_issue183_alert_migration.py::TestB::test_two", 102)

        self.assertTrue(first.startswith("test_479_ci_isolation_"))
        self.assertTrue(second.startswith("test_479_ci_isolation_"))
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 63)
        self.assertLessEqual(len(second), 63)

    def test_child_uses_empty_database_setup_without_current_schema_bootstrap(self):
        nodeid = "core/tests/test_issue479_migration_harness.py::MigrationSchemaIsolationTests::test_case"

        args = _child_pytest_args(nodeid)

        self.assertNotIn("--create-db", args)
        self.assertNotIn("--no-migrations", args)
        self.assertIn("-p", args)
        self.assertIn("no:django", args)
        self.assertIn("core.tests.migration_harness", args)
        self.assertEqual(args[:3], [os.sys.executable, "-m", "pytest"])
        self.assertEqual(args[-1], nodeid)

    def test_empty_database_is_created_by_an_explicit_bounded_command(self):
        database = _child_database_name("core/tests/test_issue479_migration_harness.py::test_case", 101)
        result = SimpleNamespace(returncode=0, stdout="CREATE DATABASE\n")
        with patch("core.tests.migration_harness.subprocess.run", return_value=result) as run:
            self.assertIsNone(_create_child_database(database, os.environ.copy()))

        command = run.call_args.args[0]
        self.assertEqual(command[0], os.sys.executable)
        self.assertEqual(command[1], "-c")
        self.assertIn("CREATE DATABASE", command[2])
        self.assertEqual(command[-1], database)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_child_binds_only_the_precreated_database(self):
        database = _child_database_name("core/tests/test_issue479_migration_harness.py::test_case", 102)
        database_settings = migration_harness.settings.DATABASES["default"]
        old_settings_name = database_settings["NAME"]
        old_connection_name = connection.settings_dict["NAME"]
        try:
            with patch.dict(os.environ, {"TEST_DATABASE_NAME": database}, clear=False):
                with patch("core.tests.migration_harness.connection.close") as close:
                    migration_harness._configure_child_database()

            self.assertEqual(database_settings["NAME"], database)
            self.assertEqual(connection.settings_dict["NAME"], database)
            close.assert_called_once_with()
        finally:
            database_settings["NAME"] = old_settings_name
            connection.settings_dict["NAME"] = old_connection_name

    def test_child_environment_preserves_coverage_and_records_node(self):
        nodeid = "assets/tests/test_issue479_foundation_migrations.py::AssetTypeFoundationMigrationTests::test_case"
        with patch.dict(
            os.environ,
            {
                "COVERAGE_FILE": "../artifacts/.coverage.serial",
                "COVERAGE_PROCESS_START": "pyproject.toml",
                "PYTEST_ADDOPTS": "--maxfail=1",
                "ITAMBOX_NODE_ID_MANIFEST": "../artifacts/parent-nodes.txt",
                "ITAMBOX_NODE_ID_MANIFEST_WRITE": "1",
            },
            clear=False,
        ):
            child_env = _child_environment(nodeid)

        self.assertEqual(child_env["COVERAGE_FILE"], "../artifacts/.coverage.serial")
        self.assertEqual(child_env["COVERAGE_PROCESS_START"], "pyproject.toml")
        self.assertEqual(child_env["ITAMBOX_ISSUE479_MIGRATION_NODE"], nodeid)
        self.assertEqual(child_env["ITAMBOX_ISSUE479_MIGRATION_CHILD"], "1")
        self.assertEqual(child_env["ITAMBOX_NODE_ID_MANIFEST"], "../artifacts/parent-nodes.txt")
        self.assertEqual(child_env["ITAMBOX_NODE_ID_MANIFEST_WRITE"], "0")
        self.assertNotIn("PYTEST_ADDOPTS", child_env)

    def test_phase_output_is_flushed_and_identifies_node_pid_and_timestamp(self):
        stream = io.StringIO()
        nodeid = "core/tests/test_issue479_migration_harness.py::MigrationSchemaIsolationTests::test_case"

        _emit_phase(nodeid, "schema-ready", stream=stream, database="test_479_ci_isolation_1")

        line = stream.getvalue()
        self.assertIn("phase=schema-ready", line)
        self.assertIn(f"node={nodeid}", line)
        self.assertRegex(line, r"pid=\d+")
        self.assertRegex(line, r"timestamp=\d{4}-\d{2}-\d{2}T")

    def test_captured_child_phases_are_forwarded_and_flushed(self):
        class FlushTrackingStream(io.StringIO):
            flush_count = 0

            def flush(self):
                self.flush_count += 1
                super().flush()

        stream = FlushTrackingStream()
        phase = "[issue479-migration] timestamp=now pid=123 node=node phase=schema-ready"
        with patch.object(migration_harness.sys, "stderr", stream):
            migration_harness._forward_phase_output(f"{phase}\nordinary child output")

        self.assertIn(phase, stream.getvalue())
        self.assertEqual(stream.flush_count, 1)

    def test_owned_database_cleanup_never_targets_public_or_unrelated_database(self):
        with patch("core.tests.migration_harness.subprocess.run") as run:
            self.assertIsNone(_cleanup_child_database("itambox", os.environ.copy()))
            self.assertIsNone(_cleanup_child_database("challenger2_testing", os.environ.copy()))

        self.assertFalse(run.called)
        self.assertTrue(_is_owned_database_name("test_479_ci_isolation_1_abc"))
        self.assertFalse(_is_owned_database_name("test_479_ci_isolation_1; DROP DATABASE itambox"))
        self.assertFalse(_is_owned_database_name("itambox"))

    def test_owned_database_cleanup_is_bounded_and_explicit(self):
        result = SimpleNamespace(returncode=0, stdout="DROP DATABASE\n")
        database = _child_database_name("core/tests/test_issue479_migration_harness.py::test_case", 101)
        with patch("core.tests.migration_harness.subprocess.run", return_value=result) as run:
            cleanup_error = _cleanup_child_database(database, os.environ.copy())

        self.assertIsNone(cleanup_error)
        command = run.call_args.args[0]
        self.assertEqual(command[0], os.sys.executable)
        self.assertEqual(command[1], "-c")
        self.assertIn("DROP DATABASE", command[2])
        self.assertEqual(command[-1], database)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_windows_process_tree_termination_is_native_and_bounded(self):
        process = SimpleNamespace(pid=4321)
        result = SimpleNamespace(returncode=0, stdout="SUCCESS")
        with patch.object(migration_harness.os, "name", "nt"):
            with patch("core.tests.migration_harness.subprocess.run", return_value=result) as run:
                with patch.object(process, "wait", return_value=0, create=True) as wait:
                    self.assertIsNone(migration_harness._terminate_process_tree(process))

        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4321", "/T", "/F"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        wait.assert_called_once_with(timeout=20)

    def test_cleanup_timeout_is_reported(self):
        database = _child_database_name("core/tests/test_issue479_migration_harness.py::test_case", 101)
        with patch(
            "core.tests.migration_harness.subprocess.run",
            side_effect=subprocess.TimeoutExpired("python", 30),
        ):
            cleanup_error = _cleanup_child_database(database, os.environ.copy())

        self.assertIn("database cleanup timed out", cleanup_error)

    def test_failed_child_keeps_original_failure_when_cleanup_also_fails(self):
        process = SimpleNamespace(pid=1234, returncode=17)
        process.communicate = lambda timeout=None: ("original child failure", None)
        created = SimpleNamespace(returncode=0, stdout="CREATE DATABASE\n")
        cleanup = SimpleNamespace(returncode=9, stdout="database cleanup failed")
        with patch("core.tests.migration_harness.subprocess.Popen", return_value=process):
            with patch("core.tests.migration_harness.subprocess.run", side_effect=[created, cleanup]):
                with self.assertRaises(AssertionError) as raised:
                    _run_isolated_test(lambda case: None, self)

        message = str(raised.exception)
        self.assertLess(message.index("exited 17"), message.index("database cleanup failed"))
        self.assertIn("original child failure", message)

    def test_database_creation_failure_is_primary_and_cleanup_is_attempted(self):
        database = _child_database_name("core/tests/test_issue479_migration_harness.py::test_case", 103)
        database_error = SimpleNamespace(returncode=11, stdout="database creation failed")
        cleanup = SimpleNamespace(returncode=0, stdout="DROP DATABASE\n")
        with patch("core.tests.migration_harness.subprocess.Popen") as popen:
            with patch("core.tests.migration_harness.subprocess.run", side_effect=[database_error, cleanup]) as run:
                with self.assertRaises(AssertionError) as raised:
                    with patch("core.tests.migration_harness._child_database_name", return_value=database):
                        _run_isolated_test(lambda case: None, self)

        self.assertFalse(popen.called)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][-1], database)
        message = str(raised.exception)
        self.assertIn("database creation exited 11", message)
        self.assertIn("database creation failed", message)

    def test_timed_out_child_output_collection_remains_bounded(self):
        calls = []

        class StuckProcess:
            pid = 4322
            returncode = -15

            def communicate(self, timeout=None):
                calls.append(timeout)
                raise subprocess.TimeoutExpired("pytest", timeout or 0)

        created = SimpleNamespace(returncode=0, stdout="CREATE DATABASE\n")
        cleanup = SimpleNamespace(returncode=0, stdout="DROP DATABASE\n")
        with patch.dict(os.environ, {"ITAMBOX_ISSUE479_MIGRATION_TIMEOUT": "0.1"}, clear=False):
            with patch("core.tests.migration_harness.subprocess.Popen", return_value=StuckProcess()):
                with patch("core.tests.migration_harness._terminate_process_tree", return_value=None):
                    with patch("core.tests.migration_harness.subprocess.run", side_effect=[created, cleanup]):
                        with self.assertRaises(AssertionError):
                            _run_isolated_test(lambda case: None, self)

        self.assertEqual(calls, [0.1, 20])

    def test_timed_out_child_is_terminated_and_reports_partial_output(self):
        class TimeoutProcess:
            pid = 4321
            returncode = -15

            def communicate(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("pytest", timeout, output="partial child output")
                return "final child output", None

            def wait(self, timeout=None):
                return self.returncode

        cleanup = SimpleNamespace(returncode=0, stdout="DROP DATABASE\n")
        created = SimpleNamespace(returncode=0, stdout="CREATE DATABASE\n")
        with patch.dict(os.environ, {"ITAMBOX_ISSUE479_MIGRATION_TIMEOUT": "0.1"}, clear=False):
            with patch("core.tests.migration_harness.subprocess.Popen", return_value=TimeoutProcess()):
                with patch("core.tests.migration_harness._terminate_process_tree", return_value=None) as terminate:
                    with patch("core.tests.migration_harness.subprocess.run", side_effect=[created, cleanup]):
                        with self.assertRaises(AssertionError) as raised:
                            _run_isolated_test(lambda case: None, self)

        self.assertTrue(terminate.called)
        self.assertIn("timed out", str(raised.exception))
        self.assertIn("partial child output", str(raised.exception))


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

    def test_each_case_starts_with_a_fresh_private_schema(self):
        name = "test_479_case_contamination_canary"
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE {name} (id integer)")
            cursor.execute("SELECT to_regclass(%s)", [name])
            self.assertIsNotNone(cursor.fetchone()[0])
            cursor.execute(f"DROP TABLE {name}")
