import os
import pytest

def pytest_configure(config):
    from django.conf import settings
    # Isolate using a unique name for the adversarial test runner to avoid collisions
    has_adversarial = any('test_graphql_adversarial' in arg for arg in getattr(config, 'args', []))
    if has_adversarial:
        settings.DATABASES['default']['TEST']['NAME'] = f'challenger2_adversarial_{os.getpid()}'
    else:
        db_name = os.environ.get('TEST_DATABASE_NAME', f'challenger2_testing_{os.getpid()}')
        settings.DATABASES['default']['TEST']['NAME'] = db_name


@pytest.fixture(autouse=True)
def clear_thread_locals():
    yield
    try:
        from core.managers import set_current_tenant, set_current_tenant_group, set_current_membership
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
    except Exception:
        pass

    try:
        from itambox.middleware import _request_id, _current_user
        _request_id.set(None)
        _current_user.set(None)
    except Exception:
        pass



