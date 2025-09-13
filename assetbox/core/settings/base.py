"""
Django base settings for AssetBox.
Contains all common settings shared between dev and prod.
"""

import os
from pathlib import Path
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

VERSION = '0.1.0'

SECRET_KEY = os.environ.get('ASSETBOX_SECRET_KEY', '')

if not SECRET_KEY:
    SECRET_KEY = 'django-insecure-dev-only-change-me-in-production'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'assets',
    'components',
    'inventory',
    'compliance',
    'organization',
    'software',
    'subscriptions',
    'licenses',
    'django_tables2',
    'template_partials',
    'django_htmx',
    'django_filters',
    'crispy_forms',
    'crispy_bootstrap5',
    'assetbox.apps.AssetBoxConfig',
    'core.apps.CoreConfig',
    'extras.apps.ExtrasConfig',
    'rest_framework',
    'drf_spectacular',
    'users',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'assetbox.middleware.CSPMiddleware',
    'assetbox.middleware.CurrentUserMiddleware',
]

ROOT_URLCONF = 'core.urls'

default_loaders = [
    "django.template.loaders.filesystem.Loader",
    "django.template.loaders.app_directories.Loader",
]

partial_loaders = [("template_partials.loader.Loader", default_loaders)]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'assetbox.context_processors.breadcrumbs',
                'assetbox.context_processors.notifications_processor',
            ],
            'loaders': partial_loaders,
            'libraries': {
                'navigation': 'core.templatetags.navigation',
            },
            'builtins': [
                'core.templatetags.panels',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('ASSETBOX_DB_ENGINE', 'django.db.backends.sqlite3'),
        'NAME': os.environ.get('ASSETBOX_DB_NAME', BASE_DIR / 'db.sqlite3'),
    }
}

if DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
    DATABASES['default'].update({
        'USER': os.environ.get('ASSETBOX_DB_USER', ''),
        'PASSWORD': os.environ.get('ASSETBOX_DB_PASSWORD', ''),
        'HOST': os.environ.get('ASSETBOX_DB_HOST', 'localhost'),
        'PORT': os.environ.get('ASSETBOX_DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('ASSETBOX_DB_CONN_MAX_AGE', '300')),
        'OPTIONS': {
            'sslmode': os.environ.get('ASSETBOX_DB_SSLMODE', 'prefer'),
        },
    })
else:
    DATABASES['default']['CONN_MAX_AGE'] = 60

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ('en', _('English')),
    ('de', _('German')),
]

LOCALE_PATHS = [
    BASE_DIR / 'locale',
]

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Local static end-user documentation root
DOCS_ROOT = os.environ.get('ASSETBOX_DOCS_ROOT', BASE_DIR / 'docs')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'assetbox.api.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'assetbox.api.permissions.TokenPermissions',
    ],
    'DEFAULT_PAGINATION_CLASS': 'assetbox.api.pagination.AssetBoxPagination',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
    'VIEW_NAME_FUNCTION': 'assetbox.api.utils.get_view_name',
}

PAGINATE_COUNT = 50
MAX_PAGE_SIZE = 1000

CACHE_BACKEND = os.environ.get('ASSETBOX_CACHE_BACKEND', 'locmem')
if CACHE_BACKEND == 'redis':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': os.environ.get('ASSETBOX_REDIS_URL', 'redis://127.0.0.1:6379/1'),
            'TIMEOUT': int(os.environ.get('ASSETBOX_CACHE_TIMEOUT', '300')),
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'assetbox-cache',
        }
    }

SEARCH_BACKEND = 'core.search_backends.DatabaseBackend'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

AUTHENTICATION_BACKENDS = [
    'core.auth.AssetBoxPermissionBackend',
    'django.contrib.auth.backends.ModelBackend',
]

DEFAULT_PAGINATE_COUNT = 25
PAGINATE_COUNT_CHOICES = (
    (25, '25'),
    (50, '50'),
    (100, '100'),
    (250, '250'),
    (500, '500'),
    (1000, '1000'),
)

SAML_ACTIVE = False

try:
    from core.models import SAMLSettings
    saml_config = SAMLSettings.load()
    if saml_config and saml_config.is_active:
        SAML_ACTIVE = True
        SAML_DJANGO_USER_MAIN_ATTRIBUTE = 'username'
        SAML_USE_NAME_ID_AS_USERNAME = True
        SAML_CREATE_UNKNOWN_USER = True
        SAML_ATTRIBUTE_MAPPING = {
            'email': ('email',),
            'first_name': ('first_name', 'givenName'),
            'last_name': ('last_name', 'sn'),
        }
        if saml_config.strict:
            SAML_STRICT = True
        if saml_config.sp_entity_id:
            SAML_SP_ENTITY_ID = saml_config.sp_entity_id
        if saml_config.idp_entity_id:
            SAML_IDP_ENTITY_ID = saml_config.idp_entity_id
except Exception:
    SAML_ACTIVE = False
