import hashlib
import inspect
import os
import signal
import subprocess
import sys
from functools import wraps
from pathlib import Path
from uuid import uuid4

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

_ISOLATED_MIGRATION_ENV = "ITAMBOX_ISSUE479_MIGRATION_CHILD"
_DATABASE_PREFIX = "test_479_ci_isolation_"


def _is_isolated_child():
    return os.environ.get(_ISOLATED_MIGRATION_ENV) == "1"


def _child_database_name(nodeid, pid=None):
    process_id = os.getpid() if pid is None else pid
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
    return f"{_DATABASE_PREFIX}{process_id}_{digest}"[:63]


def _migration_test_nodeid(test_case, method):
    test_file = Path(inspect.getfile(type(test_case))).resolve()
    cwd = Path.cwd().resolve()
    try:
        relative_file = test_file.relative_to(cwd)
    except ValueError:
        relative_file = Path(os.path.relpath(test_file, cwd))
    return f"{relative_file.as_posix()}::{type(test_case).__name__}::{method.__name__}"


def _run_isolated_test(method, self, *args, **kwargs):
    if _is_isolated_child():
        return method(self, *args, **kwargs)

    nodeid = _migration_test_nodeid(self, method)
    child_env = os.environ.copy()
    child_env[_ISOLATED_MIGRATION_ENV] = "1"
    child_env["TEST_DATABASE_NAME"] = _child_database_name(nodeid)
    child_env.pop("PYTEST_ADDOPTS", None)
    timeout = float(os.environ.get("ITAMBOX_ISSUE479_MIGRATION_TIMEOUT", "900"))
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

    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=--tb=short -p no:warnings",
            "--create-db",
            nodeid,
        ],
        **popen_kwargs,
    )
    try:
        output, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            child.kill()
        else:
            os.killpg(child.pid, signal.SIGTERM)
        try:
            output, _ = child.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                child.kill()
            else:
                os.killpg(child.pid, signal.SIGKILL)
            output, _ = child.communicate()
        self.fail(f"isolated migration test timed out after {timeout:g}s: {nodeid}\n{output}")
    if child.returncode:
        self.fail(f"isolated migration test exited {child.returncode}: {nodeid}\n{output}")


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
    def _pre_setup(cls):
        if _is_isolated_child():
            super()._pre_setup()

    def setUp(self):
        if not _is_isolated_child():
            return
        super().setUp()
        self._migration_schema_name = f"test_479_ci_isolation_schema_{os.getpid()}_{uuid4().hex[:12]}"
        quoted_schema = connection.ops.quote_name(self._migration_schema_name)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {quoted_schema}")
            cursor.execute(f"SET search_path TO {quoted_schema}")
            MigrationRecorder(connection).ensure_schema()
            # Do not let current public tables satisfy historical-schema lookups.

    def tearDown(self):
        if not _is_isolated_child():
            return
        quoted_schema = connection.ops.quote_name(self._migration_schema_name)
        try:
            super().tearDown()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")

    def _post_teardown(self):
        if _is_isolated_child():
            super()._post_teardown()
