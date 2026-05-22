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


