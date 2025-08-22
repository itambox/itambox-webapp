"""
Production settings override.
To use: set DJANGO_SETTINGS_MODULE=core.settings.prod or ASSETBOX_ENV=prod
"""

import os
from .base import *

DEBUG = os.environ.get('ASSETBOX_DEBUG', 'False').lower() in ('true', '1', 't')

if not DEBUG and SECRET_KEY == 'django-insecure-dev-only-change-me-in-production':
    raise RuntimeError(
        'SECRET_KEY environment variable must be set to a secure random value '
        'when DEBUG=False (production mode).'
    )

ALLOWED_HOSTS = os.environ.get('ASSETBOX_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get('ASSETBOX_SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 't')
    SECURE_HSTS_SECONDS = int(os.environ.get('ASSETBOX_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('ASSETBOX_HSTS_INCLUDE_SUBDOMAINS', 'True').lower() in ('true', '1', 't')
    SECURE_HSTS_PRELOAD = os.environ.get('ASSETBOX_HSTS_PRELOAD', 'True').lower() in ('true', '1', 't')
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
