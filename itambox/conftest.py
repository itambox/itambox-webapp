import os
import pytest

def pytest_configure(config):
    from django.conf import settings
    # Use a STABLE test-DB name so --reuse-db can find the prior database and
    # skip rebuilding ~200 migrations every run. (A per-PID name defeated reuse.)
    # The adversarial runner keeps its own distinct name so it never collides
    # with a concurrently-running main suite. Under pytest-xdist, pytest-django
    # appends the worker id (e.g. _gw0) to whichever name we set here.
    has_adversarial = any('test_graphql_adversarial' in arg for arg in getattr(config, 'args', []))
    if has_adversarial:
        db_name = os.environ.get('TEST_DATABASE_NAME_ADVERSARIAL', 'challenger2_adversarial')
    else:
        db_name = os.environ.get('TEST_DATABASE_NAME', 'challenger2_testing')
    settings.DATABASES['default']['TEST']['NAME'] = db_name


@pytest.fixture(scope='session', autouse=True)
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


@pytest.fixture(autouse=True)
def clear_thread_locals():
    yield
    try:
        from core.managers import (
            set_current_tenant,
            set_current_tenant_group,
            set_current_membership,
            set_current_all_accessible,
            _descendant_group_ids_cache,
        )
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_all_accessible(False)
        # Explicitly clear the descendant-group cache too: the setters above reset
        # it transitively, but resetting it here guarantees a clean slate even if a
        # setter raised partway through, avoiding order-dependent flakiness.
        _descendant_group_ids_cache.set(None)
    except Exception:
        pass

    try:
        from itambox.middleware import _request_id, _current_user
        _request_id.set(None)
        _current_user.set(None)
    except Exception:
        pass
