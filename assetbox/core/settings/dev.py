"""
Development settings override.
To use: set DJANGO_SETTINGS_MODULE=core.settings.dev or ASSETBOX_ENV=dev
"""

from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']
