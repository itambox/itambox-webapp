"""
Production settings-posture assertions (WS7-4).

The whole pytest run executes under ``core.settings`` which resolves to the
*dev* settings module (the loader forces ENV='dev' for test runs), so
``core/settings/prod.py`` is otherwise never imported and its hardening is never
verified. A regression here — a secure cookie flipped off, HSTS dropped,
BasicAuthentication re-added, the sentinel-key guard removed — would ship
silently.

These tests import the prod settings module *in isolation*, independent of
``sys.argv``: the environment is patched and ``core.settings.base`` +
``core.settings.prod`` are re-imported fresh so the prod module evaluates against
the patched environment. The live Django ``settings`` object is unaffected — it
copied the dev names into its own namespace at startup and reloading the source
modules here does not re-mutate it. An autouse fixture restores ``sys.modules``
and reloads ``base`` against the real environment after each test so no polluted
module leaks into the rest of the suite.
"""

import importlib
import logging
import os
import sys
import warnings
from unittest import mock

import pytest

# A non-sentinel secret key so the prod guard does not raise in the positive
# cases. Any value other than the insecure dev fallback works.
SECURE_KEY = 'prod-posture-test-stable-secret-key-0123456789abcdef'
# 32 url-safe-base64-decodable bytes -> a valid Fernet key, so the encryption
# layer treats ITAMBOX_FIELD_ENCRYPTION_KEYS as configured (not derived).
FERNET_KEY = 'a' * 43 + '='


def _load_prod(extra_env):
    """
    Re-import core.settings.prod under a patched environment and return the
    freshly evaluated module.

    Baseline env pins the security-relevant inputs (secret key set, redis cache,
    explicit encryption key) so each test starts from a clean, deterministic
    posture and only flips what it asserts on. ``base`` is reloaded first because
    ``prod`` does ``from .base import *`` and otherwise re-uses the cached
    (startup-time) base namespace instead of re-reading the patched env.
    """
    env = {
        'ITAMBOX_SECRET_KEY': SECURE_KEY,
        'ITAMBOX_CACHE_BACKEND': 'redis',
        'ITAMBOX_FIELD_ENCRYPTION_KEYS': FERNET_KEY,
    }
    env.update(extra_env)
    with mock.patch.dict(os.environ, env, clear=False):
        sys.modules.pop('core.settings.prod', None)
        base = importlib.import_module('core.settings.base')
        with warnings.catch_warnings():
            # base re-warns about a missing secret key etc. when reloaded; the
            # individual tests assert on prod's own logging, not base's warnings.
            warnings.simplefilter('ignore')
            importlib.reload(base)
        return importlib.import_module('core.settings.prod')


@pytest.fixture(autouse=True)
def _restore_settings_modules():
    """
    Reloading core.settings.base/prod under a patched env mutates the cached
    modules. Restore them to the real environment afterwards so the rest of the
    suite (and any code importing those module attributes) sees the true values.
    """
    yield
    sys.modules.pop('core.settings.prod', None)
    base = importlib.import_module('core.settings.base')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        importlib.reload(base)


class TestProdSettingsPosture:
    """Security-critical production settings must hold."""

    def test_secure_cookies_enabled(self):
        prod = _load_prod({})
        assert prod.SESSION_COOKIE_SECURE is True
        assert prod.CSRF_COOKIE_SECURE is True

    def test_ssl_redirect_enabled(self):
        prod = _load_prod({})
        assert prod.SECURE_SSL_REDIRECT is True

    def test_hsts_configured(self):
        prod = _load_prod({})
        assert prod.SECURE_HSTS_SECONDS > 0

    def test_content_type_and_xss_hardening(self):
        prod = _load_prod({})
        # Defensive: only assert when the attribute exists, but these are set
        # unconditionally in prod.py so they must be present.
        assert getattr(prod, 'SECURE_CONTENT_TYPE_NOSNIFF', None) is True
        assert getattr(prod, 'SECURE_BROWSER_XSS_FILTER', None) is True

    def test_basic_authentication_dropped(self):
        prod = _load_prod({})
        auth_classes = prod.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        assert not any('BasicAuthentication' in cls for cls in auth_classes), (
            'BasicAuthentication must not be a default authentication class in '
            'production (credentials are sent on every request).'
        )
        # Token + session auth must still be present.
        assert any('TokenAuthentication' in cls for cls in auth_classes)

    def test_debug_is_never_true_in_prod(self):
        """
        DEBUG must be hardcoded False in prod.py, not env-toggleable. A stray
        ITAMBOX_DEBUG=True in a leftover/templated .env (e.g. copied from
        .env.example and only half-edited when switching to prod) must not be
        able to flip it on.
        """
        assert _load_prod({}).DEBUG is False
        assert _load_prod({'ITAMBOX_DEBUG': 'True'}).DEBUG is False
        assert _load_prod({'ITAMBOX_DEBUG': 'true'}).DEBUG is False
        assert _load_prod({'ITAMBOX_DEBUG': '1'}).DEBUG is False

    def test_sentinel_secret_key_raises(self):
        """An unset (sentinel) SECRET_KEY must refuse to boot in prod."""
        with pytest.raises(RuntimeError, match='ITAMBOX_SECRET_KEY'):
            _load_prod({'ITAMBOX_SECRET_KEY': ''})

    def test_locmem_cache_warns_in_prod(self):
        """locmem cache in prod must emit a loud warning (per-worker counters)."""
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logger = logging.getLogger('core.settings.prod')
        logger.addHandler(handler)
        try:
            _load_prod({'ITAMBOX_CACHE_BACKEND': 'locmem'})
        finally:
            logger.removeHandler(handler)
        assert any('locmem' in msg for msg in records), (
            'Expected a startup warning about locmem cache in production.'
        )

    def test_derived_encryption_key_warns_in_prod(self):
        """No ITAMBOX_FIELD_ENCRYPTION_KEYS in prod must emit a loud warning."""
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logger = logging.getLogger('core.settings.prod')
        logger.addHandler(handler)
        try:
            _load_prod({'ITAMBOX_FIELD_ENCRYPTION_KEYS': ''})
        finally:
            logger.removeHandler(handler)
        assert any('ITAMBOX_FIELD_ENCRYPTION_KEYS' in msg for msg in records), (
            'Expected a startup warning about the derived field-encryption key '
            'in production.'
        )

    def test_no_spurious_warnings_when_hardened(self):
        """A fully hardened prod config (redis + explicit keys) must be quiet."""
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logger = logging.getLogger('core.settings.prod')
        logger.addHandler(handler)
        try:
            prod = _load_prod({})
        finally:
            logger.removeHandler(handler)
        assert records == [], f'Unexpected prod warnings: {records}'
        assert prod.DEBUG is False
