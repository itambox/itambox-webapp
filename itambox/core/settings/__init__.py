"""
Environment-aware settings loader.

Selects the settings module using a *fail-closed* strategy:

1. ``DJANGO_SETTINGS_MODULE`` set to ``core.settings.dev``/``core.settings.prod``
   bypasses this loader entirely.
2. ``ITAMBOX_ENV`` (``dev``/``prod``) takes precedence when set.
3. Otherwise ``ITAMBOX_DEBUG`` is honoured when explicitly set
   (truthy -> dev, falsy -> prod).
4. Test runs (pytest / ``manage.py test``) default to ``dev``.
5. When nothing is configured we default to ``prod`` so an unconfigured
   deployment never silently runs with ``DEBUG=True`` and the insecure
   fallback secret key.
"""

import os
import sys

ENV = os.environ.get('ITAMBOX_ENV', None)

if ENV is None:
    _is_testing = 'test' in sys.argv or any('test' in arg or 'pytest' in arg for arg in sys.argv)
    _debug_raw = os.environ.get('ITAMBOX_DEBUG')
    if _is_testing:
        ENV = 'dev'
    elif _debug_raw is not None:
        ENV = 'dev' if _debug_raw.lower() in ('true', '1', 't') else 'prod'
    else:
        # Nothing configured: fail closed to production.
        ENV = 'prod'

if ENV == 'prod':
    from .prod import *
elif ENV == 'dev':
    from .dev import *
else:
    from .base import *
