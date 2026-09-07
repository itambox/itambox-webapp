import hashlib
import inspect
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

_ISOLATED_MIGRATION_ENV = "ITAMBOX_ISSUE479_MIGRATION_CHILD"
_NODE_ENV = "ITAMBOX_ISSUE479_MIGRATION_NODE"
_DATABASE_PREFIX = "test_479_ci_isolation_"
_SCHEMA_PREFIX = "test_479_ci_isolation_schema_"
_CLEANUP_TIMEOUT = 30
_TERMINATION_TIMEOUT = 20

_CREATE_DATABASE_SCRIPT = """
import sys

import django

django.setup()

from django.db import connections

connection = connections["default"]
with connection.creation._nodb_cursor() as cursor:
    cursor.execute("CREATE DATABASE " + connection.ops.quote_name(sys.argv[1]))
"""


_DROP_DATABASE_SCRIPT = """
import sys

import django

django.setup()

from django.db import connections

connection = connections["default"]
with connection.creation._nodb_cursor() as cursor:
    cursor.execute(
        "DROP DATABASE IF EXISTS "
        + connection.ops.quote_name(sys.argv[1])
        + " WITH (FORCE)"
    )
"""


def _is_isolated_child():
    return os.environ.get(_ISOLATED_MIGRATION_ENV) == "1"


def pytest_configure(config):
    del config
    if not _is_isolated_child():
        return

    nodeid = os.environ.get(_NODE_ENV, "<unknown>")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    _emit_phase(nodeid, "django-setup-start")
    try:
        import django

        django.setup()
    except BaseException as exc:
        _emit_phase(nodeid, "django-setup-failed", error=f"{type(exc).__name__}: {exc}")
        raise
    _emit_phase(nodeid, "django-setup-complete")


def _child_database_name(nodeid, pid=None):
    process_id = os.getpid() if pid is None else pid
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
    nonce = uuid4().hex[:8]
    return f"{_DATABASE_PREFIX}{process_id}_{digest}_{nonce}"[:63]


def _is_owned_database_name(database_name):
    return bool(
        isinstance(database_name, str)
        and len(database_name) <= 63
        and re.fullmatch(rf"{re.escape(_DATABASE_PREFIX)}[0-9]+(?:_[0-9a-f]+)+", database_name)
    )


def _child_schema_name(pid=None):
    process_id = os.getpid() if pid is None else pid
    return f"{_SCHEMA_PREFIX}{process_id}_{uuid4().hex[:12]}"


def _is_owned_schema_name(schema_name):
    return bool(
        isinstance(schema_name, str)
        and len(schema_name) <= 63
        and re.fullmatch(rf"{re.escape(_SCHEMA_PREFIX)}[0-9]+_[0-9a-f]{{12}}", schema_name)
    )


def _migration_test_nodeid(test_case, method):
    test_file = Path(inspect.getfile(type(test_case))).resolve()
    cwd = Path.cwd().resolve()
    try:
        relative_file = test_file.relative_to(cwd)
    except ValueError:
        relative_file = Path(os.path.relpath(test_file, cwd))
    return f"{relative_file.as_posix()}::{type(test_case).__name__}::{method.__name__}"


def _emit_phase(nodeid, phase, *, stream=None, pid=None, **details):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    process_id = os.getpid() if pid is None else pid
    safe_nodeid = str(nodeid).replace("\n", "\\n").replace("\r", "\\r")
    fields = [
        "[issue479-migration]",
        f"timestamp={timestamp}",
        f"pid={process_id}",
        f"node={safe_nodeid}",
        f"phase={phase}",
    ]
    for key, value in details.items():
        safe_value = str(value).replace("\n", "\\n").replace("\r", "\\r")
        fields.append(f"{key}={safe_value}")
    print(" ".join(fields), file=stream or sys.stderr, flush=True)


def _child_environment(nodeid):
    child_env = os.environ.copy()
    child_env[_ISOLATED_MIGRATION_ENV] = "1"
    child_env[_NODE_ENV] = nodeid
    child_env["TEST_DATABASE_NAME"] = _child_database_name(nodeid)
    child_env.pop("PYTEST_ADDOPTS", None)
    # The parent owns the collection manifest; a child must not overwrite it.
    child_env["ITAMBOX_NODE_ID_MANIFEST_WRITE"] = "0"
    return child_env


def _child_pytest_args(nodeid):
    return [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:django",
        "-p",
        "core.tests.migration_harness",
        "-o",
        "addopts=--tb=short -p no:warnings",
        nodeid,
    ]


def _output_text(*parts):
    output = []
    for part in parts:
        if not part:
            continue
        if isinstance(part, bytes):
            part = part.decode("utf-8", errors="replace")
        output.append(str(part))
    return "\n".join(output)


def _forward_phase_output(output):
    for line in _output_text(output).splitlines():
        if line.startswith("[issue479-migration]"):
            print(line, file=sys.stderr, flush=True)


def _process_error(prefix, result):
    output = _output_text(getattr(result, "stdout", None))
    if output:
        return f"{prefix}: {output}"
    return prefix


def _wait_for_child(child, *, error_prefix):
    try:
        child.wait(timeout=_TERMINATION_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{error_prefix}: {type(exc).__name__}: {exc}"
    return None


def _terminate_windows_process_tree(child):
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_TERMINATION_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"process-tree termination failed: {type(exc).__name__}: {exc}"
    if result.returncode:
        return _process_error(f"process-tree termination exited {result.returncode}", result)
    return _wait_for_child(child, error_prefix="process-tree did not stop")


def _terminate_posix_process_tree(child):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    except OSError as exc:
        return f"process-group SIGTERM failed: {type(exc).__name__}: {exc}"

    try:
        child.wait(timeout=_TERMINATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            return None
        except OSError as exc:
            return f"process-group SIGKILL failed: {type(exc).__name__}: {exc}"
        return _wait_for_child(child, error_prefix="process-group did not stop")
    except OSError as exc:
        return f"process-group wait failed: {type(exc).__name__}: {exc}"
    return None


def _terminate_process_tree(child):
    if os.name == "nt":
        return _terminate_windows_process_tree(child)
    return _terminate_posix_process_tree(child)


def _terminate_child_safely(child):
    try:
        return _terminate_process_tree(child)
    except Exception as exc:  # pragma: no cover - defensive process cleanup
        return f"process-tree termination raised: {type(exc).__name__}: {exc}"


def _create_child_database(database_name, child_env):
    if not _is_owned_database_name(database_name):
        return f"refusing to create unowned database {database_name!r}"

    create_env = child_env.copy()
    create_env.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CREATE_DATABASE_SCRIPT, database_name],
            cwd=os.getcwd(),
            env=create_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_CLEANUP_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        return f"database creation timed out after {_CLEANUP_TIMEOUT}s: {exc}"
    except OSError as exc:
        return f"database creation could not start: {type(exc).__name__}: {exc}"
    if result.returncode:
        return _process_error(f"database creation exited {result.returncode}", result)
    return None


def _cleanup_child_database(database_name, child_env):
    if not _is_owned_database_name(database_name):
        return None

    cleanup_env = child_env.copy()
    cleanup_env.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _DROP_DATABASE_SCRIPT, database_name],
            cwd=os.getcwd(),
            env=cleanup_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_CLEANUP_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        return f"database cleanup timed out after {_CLEANUP_TIMEOUT}s: {exc}"
    except OSError as exc:
        return f"database cleanup could not start: {type(exc).__name__}: {exc}"
    if result.returncode:
        return _process_error(f"database cleanup exited {result.returncode}", result)
    return None


def _add_failure_note(error, note):
    add_note = getattr(error, "add_note", None)
    if add_note is not None:
        add_note(note)


def _safe_create_child_database(database_name, child_env):
    try:
        return _create_child_database(database_name, child_env)
    except Exception as exc:  # pragma: no cover - defensive creation boundary
        return f"database creation raised: {type(exc).__name__}: {exc}"


def _safe_cleanup_child_database(database_name, child_env):
    try:
        return _cleanup_child_database(database_name, child_env)
    except Exception as exc:  # pragma: no cover - defensive cleanup boundary
        return f"database cleanup raised: {type(exc).__name__}: {exc}"


def _prepare_isolated_database(test_case, nodeid, database_name, child_env):
    _emit_phase(nodeid, "database-create-start", database=database_name)
    database_error = _safe_create_child_database(database_name, child_env)
    if database_error:
        _emit_phase(nodeid, "database-create-failed", database=database_name, error=database_error)
        cleanup_error = _safe_cleanup_child_database(database_name, child_env)
        _emit_phase(
            nodeid,
            "parent-complete",
            database=database_name,
            child_pid="<none>",
            returncode="<none>",
            status="database-create-failed",
        )
        details = [database_error]
        if cleanup_error:
            details.append(cleanup_error)
        creation_detail_text = "\n".join(details)
        test_case.fail(f"isolated migration database creation failed: {nodeid}\n{creation_detail_text}")
    _emit_phase(nodeid, "database-create-complete", database=database_name)


def _collect_after_termination(child, output, *, error_prefix):
    try:
        final_output, _ = child.communicate(timeout=_TERMINATION_TIMEOUT)
    except Exception as collect_error:  # pragma: no cover - defensive process cleanup
        return output, f"{error_prefix}: {type(collect_error).__name__}: {collect_error}"
    return _output_text(output, final_output), None


def _communicate_with_child(child, timeout):
    try:
        output, _ = child.communicate(timeout=timeout)
        return output, False, None, None
    except subprocess.TimeoutExpired as exc:
        output = _output_text(getattr(exc, "output", None))
        termination_error = _terminate_child_safely(child)
        output, run_error = _collect_after_termination(
            child, output, error_prefix="timed-out child output collection failed"
        )
        return output, True, run_error, termination_error
    except Exception as exc:  # pragma: no cover - defensive process cleanup
        run_error = f"child execution failed: {type(exc).__name__}: {exc}"
        termination_error = _terminate_child_safely(child)
        output, collection_error = _collect_after_termination(child, "", error_prefix="child output collection failed")
        if collection_error:
            run_error = f"{run_error}; {collection_error}"
        return output, False, run_error, termination_error


def _run_child_process(nodeid, database_name, child_env, timeout):
    popen_kwargs = {
        "cwd": os.getcwd(),
        "env": child_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    child = None
    output = ""
    timed_out = False
    run_error = None
    termination_error = None
    try:
        child = subprocess.Popen(_child_pytest_args(nodeid), **popen_kwargs)
        _emit_phase(nodeid, "child-start", pid=child.pid, database=database_name)
        output, timed_out, run_error, termination_error = _communicate_with_child(child, timeout)
    except OSError as exc:
        run_error = f"child process could not start: {type(exc).__name__}: {exc}"
    return child, output, timed_out, run_error, termination_error


def _isolated_status(timed_out, run_error, returncode, cleanup_error):
    if timed_out:
        return "timeout"
    if run_error:
        return "runner-error"
    if returncode:
        return "failed"
    if cleanup_error:
        return "cleanup-error"
    return "passed"


def _raise_isolated_failure(test_case, nodeid, timeout, timed_out, run_error, returncode, cleanup_error, details):
    detail_text = "\n".join(details)
    if timed_out:
        test_case.fail(f"isolated migration test timed out after {timeout:g}s: {nodeid}\n{detail_text}")
    if run_error:
        test_case.fail(f"isolated migration test runner failed: {nodeid}\n{detail_text}")
    if returncode:
        test_case.fail(f"isolated migration test exited {returncode}: {nodeid}\n{detail_text}")
    if cleanup_error:
        test_case.fail(f"isolated migration test passed but cleanup failed: {nodeid}\n{detail_text}")


def _run_isolated_test(method, self, *args, **kwargs):
    nodeid = os.environ.get(_NODE_ENV) or _migration_test_nodeid(self, method)
    if _is_isolated_child():
        _emit_phase(nodeid, "test-start", database=os.environ.get("TEST_DATABASE_NAME", "<unset>"))
        return method(self, *args, **kwargs)

    child_env = _child_environment(nodeid)
    database_name = child_env["TEST_DATABASE_NAME"]
    timeout = float(os.environ.get("ITAMBOX_ISSUE479_MIGRATION_TIMEOUT", "900"))
    _prepare_isolated_database(self, nodeid, database_name, child_env)
    _emit_phase(nodeid, "spawn", database=database_name, timeout=timeout)
    child, output, timed_out, run_error, termination_error = _run_child_process(
        nodeid, database_name, child_env, timeout
    )

    if output:
        _forward_phase_output(output)

    cleanup_error = _safe_cleanup_child_database(database_name, child_env)

    returncode = getattr(child, "returncode", None)
    status = _isolated_status(timed_out, run_error, returncode, cleanup_error)
    _emit_phase(
        nodeid,
        "parent-complete",
        database=database_name,
        child_pid=getattr(child, "pid", "<none>"),
        returncode=returncode,
        status=status,
    )

    details = []
    if output:
        details.append(output)
    if termination_error:
        details.append(f"{termination_error}")
    if run_error:
        details.append(run_error)
    if cleanup_error:
        details.append(cleanup_error)
    _raise_isolated_failure(self, nodeid, timeout, timed_out, run_error, returncode, cleanup_error, details)


def _configure_child_database():
    database_name = os.environ.get("TEST_DATABASE_NAME")
    if not _is_owned_database_name(database_name):
        raise RuntimeError(f"missing or unowned child database {database_name!r}")

    connection.close()
    settings.DATABASES["default"]["NAME"] = database_name
    connection.settings_dict["NAME"] = database_name


def _isolate_test(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        return _run_isolated_test(method, self, *args, **kwargs)

    return wrapper


def _guard_outer_lifecycle(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if not _is_isolated_child():
            return None
        return method(self, *args, **kwargs)

    return wrapper


def isolate_migration_tests(test_class):
    for name, method in tuple(vars(test_class).items()):
        if name.startswith("test_"):
            setattr(test_class, name, _isolate_test(method))
        elif name in {"setUp", "tearDown"}:
            setattr(test_class, name, _guard_outer_lifecycle(method))
    return test_class


class IsolatedMigrationTestCase(TransactionTestCase):
    databases = {"default"} if _is_isolated_child() else set()

    @classmethod
    def setUpClass(cls):
        nodeid = os.environ.get(_NODE_ENV, "<unknown>")
        if not _is_isolated_child():
            super().setUpClass()
            return

        database_name = os.environ.get("TEST_DATABASE_NAME", "<unset>")
        _emit_phase(nodeid, "child-database-config-start", database=database_name)
        try:
            _configure_child_database()
            super().setUpClass()
        except BaseException as exc:
            _emit_phase(nodeid, "child-database-config-failed", error=f"{type(exc).__name__}: {exc}")
            raise
        _emit_phase(nodeid, "child-database-config-complete", database=database_name)

    @classmethod
    def _pre_setup(cls):
        if not _is_isolated_child():
            return
        nodeid = os.environ.get(_NODE_ENV, "<unknown>")
        _emit_phase(nodeid, "pre-setup-start")
        try:
            super()._pre_setup()
        except BaseException as exc:
            _emit_phase(nodeid, "pre-setup-failed", error=f"{type(exc).__name__}: {exc}")
            raise
        _emit_phase(nodeid, "pre-setup-complete")

    def setUp(self):
        if not _is_isolated_child():
            return
        self._migration_schema_name = _child_schema_name()
        nodeid = os.environ.get(_NODE_ENV, "<unknown>")
        _emit_phase(nodeid, "schema-create-start", schema=self._migration_schema_name)
        try:
            super().setUp()
            quoted_schema = connection.ops.quote_name(self._migration_schema_name)
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA {quoted_schema}")
                cursor.execute(f"SET search_path TO {quoted_schema}")
                MigrationRecorder(connection).ensure_schema()
        except BaseException as exc:
            cleanup_error = self._cleanup_schema()
            if cleanup_error:
                _add_failure_note(exc, f"migration schema cleanup after setup failure: {cleanup_error}")
            _emit_phase(
                nodeid, "schema-create-failed", schema=self._migration_schema_name, error=f"{type(exc).__name__}: {exc}"
            )
            raise
        _emit_phase(nodeid, "schema-ready", schema=self._migration_schema_name)

    def _cleanup_schema(self):
        schema_name = getattr(self, "_migration_schema_name", None)
        if not schema_name:
            return None
        if not _is_owned_schema_name(schema_name):
            self._migration_schema_name = None
            return f"refusing to clean unowned schema {schema_name!r}"

        quoted_schema = connection.ops.quote_name(schema_name)
        errors = []
        try:
            connection.close()
        except Exception as exc:
            errors.append(f"connection close failed: {type(exc).__name__}: {exc}")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
        except Exception as exc:
            errors.append(f"schema drop failed: {type(exc).__name__}: {exc}")
        self._migration_schema_name = None
        return "; ".join(errors) if errors else None

    def tearDown(self):
        if not _is_isolated_child():
            return
        nodeid = os.environ.get(_NODE_ENV, "<unknown>")
        _emit_phase(nodeid, "test-teardown-start", schema=getattr(self, "_migration_schema_name", "<unset>"))
        try:
            super().tearDown()
        except BaseException as exc:
            cleanup_error = self._cleanup_schema()
            _emit_phase(nodeid, "schema-cleanup-complete", error=cleanup_error or "none")
            if cleanup_error:
                _add_failure_note(exc, f"migration schema cleanup after test failure: {cleanup_error}")
            raise
        cleanup_error = self._cleanup_schema()
        _emit_phase(nodeid, "schema-cleanup-complete", error=cleanup_error or "none")
        if cleanup_error:
            raise AssertionError(f"migration schema cleanup failed: {cleanup_error}")

    def _post_teardown(self):
        if not _is_isolated_child():
            return
        nodeid = os.environ.get(_NODE_ENV, "<unknown>")
        _emit_phase(nodeid, "post-teardown-start")
        try:
            connection.close()
        except BaseException as exc:
            _emit_phase(nodeid, "post-teardown-failed", error=f"{type(exc).__name__}: {exc}")
            raise
        _emit_phase(nodeid, "post-teardown-complete")
