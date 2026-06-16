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


@pytest.fixture(autouse=True)
def clear_thread_locals():
    yield
    try:
        from core.managers import (
            set_current_tenant,
            set_current_tenant_group,
            set_current_membership,
            _descendant_group_ids_cache,
        )
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
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



