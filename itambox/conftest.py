import os
from pathlib import Path

import pytest


def pytest_configure(config):
    from django.conf import settings

    # Use a STABLE test-DB name so --reuse-db can find the prior database and
    # skip rebuilding ~200 migrations every run. (A per-PID name defeated reuse.)
    # The adversarial runner keeps its own distinct name so it never collides
    # with a concurrently-running main suite. Under pytest-xdist, pytest-django
    # appends the worker id (e.g. _gw0) to whichever name we set here.
    has_adversarial = any("test_graphql_adversarial" in arg for arg in getattr(config, "args", []))
    if has_adversarial:
        db_name = os.environ.get("TEST_DATABASE_NAME_ADVERSARIAL", "challenger2_adversarial")
    else:
        db_name = os.environ.get("TEST_DATABASE_NAME", "challenger2_testing")
    settings.DATABASES["default"]["TEST"]["NAME"] = db_name


def _canonical_junit_node_id(nodeid):
    """Convert pytest's path-based node ID to the label emitted by pytest's JUnit writer."""
    path, *suffix = nodeid.split("::")
    if path.endswith(".py"):
        path = path[:-3]
    module = path.replace("\\", ".").replace("/", ".")
    if not suffix:
        return module
    if len(suffix) == 1:
        return f"{module}::{suffix[0]}"
    return f"{'.'.join([module, *suffix[:-1]])}::{suffix[-1]}"


def _write_node_id_manifest(items, *, xdist_active=False):
    """Write an opt-in serial collection manifest without allowing worker races."""
    manifest_path = os.environ.get("ITAMBOX_NODE_ID_MANIFEST")
    if (
        not manifest_path
        or os.environ.get("ITAMBOX_NODE_ID_MANIFEST_WRITE") != "1"
        or xdist_active
        or os.environ.get("PYTEST_XDIST_WORKER")
        or os.environ.get("PYTEST_XDIST_WORKER_COUNT")
    ):
        return

    node_ids = sorted(_canonical_junit_node_id(item.nodeid) for item in items)
    if not node_ids:
        raise pytest.UsageError("node-ID manifest requested, but pytest collected no tests")
    if len(node_ids) != len(set(node_ids)):
        raise pytest.UsageError("node-ID manifest requested, but pytest collected duplicate node IDs")

    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(node_ids) + "\n", encoding="utf-8", newline="\n")


def pytest_collection_finish(session):
    option = session.config.option
    xdist_active = bool(getattr(option, "numprocesses", 0) or getattr(option, "dist", "no") != "no")
    _write_node_id_manifest(session.items, xdist_active=xdist_active)


@pytest.fixture(scope="session", autouse=True)
def _isolate_media_root(tmp_path_factory):
    """Give every pytest process/xdist worker a disposable media root."""
    from django.test.utils import override_settings

    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
    media_root = tmp_path_factory.mktemp(f"itambox-media-{worker_id}")
    with override_settings(MEDIA_ROOT=str(media_root)):
        yield


@pytest.fixture(scope="session", autouse=True)
def _prime_urlconf_without_tenant_context():
    """Import the root URLconf (and thus every view module) once at session start,
    while NO tenant context is active.

    Many views carry a class-level ``queryset = Model.objects.all()``. That
    attribute is evaluated when the view module is imported, and the tenant-scoping
    manager reads the *current* tenant context at that moment. On a real server the
    URLconf loads at startup with no active request, so those querysets bake
    UNSCOPED (per-request ``filter_by_tenant()`` then scopes them correctly). In the
    test process, without this, whichever test first calls ``reverse()`` inside an
    active tenant context would freeze every view's queryset to that tenant —
    producing order-dependent cross-tenant 404s (see the "import-baked view
    querysets" hazard). Forcing the import here makes the bake deterministic and
    context-free, matching production.
    """
    from django.urls import get_resolver

    get_resolver().url_patterns  # noqa: B018 — accessing the property triggers the full import
    yield


def _reset_test_context():
    """Reset every request/task ContextVar and fail if any reset cannot run."""
    from django.conf import settings
    from django.core.cache import caches

    from core.auth.cache import _request_invalidation_state
    from core.context import (
        _csp_nonce,
        _current_all_accessible,
        _current_membership,
        _current_tenant,
        _current_tenant_group,
        _current_user,
        _deletion_cascade_permit,
        _descendant_group_ids_cache,
        _issued_system_authorizations,
        _request_id,
        _system_authorization_scope,
    )
    from core.navigation.menu import get_menus

    resets = (
        ("current_tenant", lambda: _current_tenant.set(None)),
        ("current_tenant_group", lambda: _current_tenant_group.set(None)),
        ("current_membership", lambda: _current_membership.set(None)),
        ("current_all_accessible", lambda: _current_all_accessible.set(False)),
        ("descendant_group_ids_cache", lambda: _descendant_group_ids_cache.set(None)),
        ("current_user", lambda: _current_user.set(None)),
        ("request_id", lambda: _request_id.set(None)),
        ("csp_nonce", lambda: _csp_nonce.set(None)),
        ("system_authorization_scope", lambda: _system_authorization_scope.set(None)),
        ("issued_system_authorizations", lambda: _issued_system_authorizations.set(())),
        ("deletion_cascade_permit", lambda: _deletion_cascade_permit.set(None)),
        ("request_invalidation_state", lambda: _request_invalidation_state.set(None)),
        ("navigation_menus_cache", get_menus.cache_clear),
    ) + tuple((f"cache:{alias}", lambda alias=alias: caches[alias].clear()) for alias in settings.CACHES)
    failures = []
    for name, reset in resets:
        try:
            reset()
        except Exception as exc:  # pragma: no cover - exercised through the failure-path test
            failures.append((name, exc))
    if failures:
        details = "; ".join(f"{name}: {type(exc).__name__}: {exc}" for name, exc in failures)
        raise AssertionError(f"Test context cleanup failed: {details}") from failures[0][1]


@pytest.fixture(autouse=True)
def clear_thread_locals():
    yield
    _reset_test_context()
