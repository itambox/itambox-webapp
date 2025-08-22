"""
Environment-aware settings loader.

Selects settings module based on:
1. DJANGO_SETTINGS_MODULE env var (if set to core.settings.dev/prod)
2. ASSETBOX_ENV env var (dev/prod)
3. DEBUG env var (True → dev, False → prod)
4. Defaults to dev
"""

import os

ENV = os.environ.get('ASSETBOX_ENV', None)

if ENV is None:
    DEBUG_FLAG = os.environ.get('ASSETBOX_DEBUG', 'true').lower() in ('true', '1', 't')
    ENV = 'dev' if DEBUG_FLAG else 'prod'

if ENV == 'prod':
    from .prod import *
elif ENV == 'dev':
    from .dev import *
else:
    from .base import *
