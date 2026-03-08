"""
Development settings override.
To use: set DJANGO_SETTINGS_MODULE=core.settings.dev or ASSETBOX_ENV=dev
"""

from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# Mailpit local SMTP catcher settings for local development
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = '127.0.0.1'
EMAIL_PORT = 1025
EMAIL_USE_TLS = False
EMAIL_USE_SSL = False

