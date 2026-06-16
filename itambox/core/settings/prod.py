"""
Production settings override.
To use: set DJANGO_SETTINGS_MODULE=core.settings.prod or ITAMBOX_ENV=prod
"""

import os
from .base import *

DEBUG = os.environ.get('ITAMBOX_DEBUG', 'False').lower() in ('true', '1', 't')

if SECRET_KEY == 'django-insecure-dev-only-change-me-in-production':
    raise RuntimeError(
        'ITAMBOX_SECRET_KEY environment variable must be set to a secure random '
        'value in production. Refusing to start with the insecure fallback key.'
    )

ALLOWED_HOSTS = os.environ.get('ITAMBOX_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Origins trusted for unsafe (POST/PUT/...) cross-origin requests. Behind an
# HTTPS proxy on a custom domain Django needs the scheme-qualified host here,
# e.g. ITAMBOX_CSRF_TRUSTED_ORIGINS=https://itam.example.com
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('ITAMBOX_CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

SECURE_SSL_REDIRECT = os.environ.get('ITAMBOX_SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 't')
SECURE_HSTS_SECONDS = int(os.environ.get('ITAMBOX_HSTS_SECONDS', '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('ITAMBOX_HSTS_INCLUDE_SUBDOMAINS', 'True').lower() in ('true', '1', 't')
SECURE_HSTS_PRELOAD = os.environ.get('ITAMBOX_HSTS_PRELOAD', 'True').lower() in ('true', '1', 't')
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Cap idle session lifetime in production (default 8h) — the 2-week Django
# default is too long for a multi-tenant asset system. Override via env.
SESSION_COOKIE_AGE = int(os.environ.get('ITAMBOX_SESSION_COOKIE_AGE', '28800'))

# ------------------------------------------------------------------------------
# Static files: served by WhiteNoise straight from gunicorn (compressed +
# content-hashed). Add the middleware immediately after SecurityMiddleware.
# ------------------------------------------------------------------------------
if 'whitenoise.middleware.WhiteNoiseMiddleware' not in MIDDLEWARE:
    _security_idx = MIDDLEWARE.index('django.middleware.security.SecurityMiddleware')
    MIDDLEWARE.insert(_security_idx + 1, 'whitenoise.middleware.WhiteNoiseMiddleware')

# Compression-only WhiteNoise storage (no manifest hashing). Manifest storage
# post-processes every CSS/JS file and hard-fails collectstatic when a vendored
# stylesheet references an asset that isn't shipped (e.g. a .css.map source map).
# Compression keeps assets small and WhiteNoise still serves them with ETag-based
# caching, without the brittle reference resolution.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# ------------------------------------------------------------------------------
# Email — env driven. Without this, Django falls back to SMTP on localhost:25
# and password resets / invitations / alert + report notifications fail.
# ------------------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get('ITAMBOX_EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.environ.get('ITAMBOX_EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('ITAMBOX_EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('ITAMBOX_EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('ITAMBOX_EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('ITAMBOX_EMAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
EMAIL_USE_SSL = os.environ.get('ITAMBOX_EMAIL_USE_SSL', 'False').lower() in ('true', '1', 't')
DEFAULT_FROM_EMAIL = os.environ.get('ITAMBOX_DEFAULT_FROM_EMAIL', 'ITAMbox <no-reply@localhost>')
SERVER_EMAIL = os.environ.get('ITAMBOX_SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# Drop BasicAuthentication in production — token + session auth is sufficient.
# BasicAuthentication transmits credentials on every request and is only needed
# for dev tooling (browsable API, curl one-liners).
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'itambox.api.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
}

# Rate limiting and SAML replay protection share the 'default' cache. Under
# multi-worker gunicorn a per-process LocMemCache makes counters per-worker
# (login limit x workers) and weakens SAML replay protection. Warn loudly.
if CACHE_BACKEND == 'locmem':
    import logging
    logging.getLogger(__name__).warning(
        'ITAMBOX_CACHE_BACKEND=locmem in production: rate-limit counters and SAML '
        'replay protection are per-worker. Set ITAMBOX_CACHE_BACKEND=redis '
        '(+ ITAMBOX_REDIS_URL) for multi-worker deployments.'
    )
