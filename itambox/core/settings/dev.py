"""
Development settings override.
To use: set DJANGO_SETTINGS_MODULE=core.settings.dev or ITAMBOX_ENV=dev
"""

from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '192.168.50.54']

# Local/CI Postgres instances typically lack TLS. Override the base default
# ('require') so that dev and test runs connect without a certificate.
# Production keeps 'require' (or overrides via ITAMBOX_DB_SSLMODE env var).
import os as _os
if not _os.environ.get('ITAMBOX_DB_SSLMODE'):
    DATABASES['default']['OPTIONS']['sslmode'] = 'disable'

# Mailpit local SMTP catcher settings for local development
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = '127.0.0.1'
EMAIL_PORT = 1025
EMAIL_USE_TLS = False
EMAIL_USE_SSL = False

# Add debug_toolbar dynamically for development
if 'debug_toolbar' not in INSTALLED_APPS:
    INSTALLED_APPS.append('debug_toolbar')
if 'debug_toolbar.middleware.DebugToolbarMiddleware' not in MIDDLEWARE:
    MIDDLEWARE.append('debug_toolbar.middleware.DebugToolbarMiddleware')


