"""
Django base settings for ITAMbox.
Contains all common settings shared between dev and prod.
"""

import os
from pathlib import Path
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env_path = BASE_DIR / '.env'
if not env_path.exists():
    env_path = BASE_DIR.parent / '.env'
if env_path.exists():
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            os.environ.setdefault(k, v)

VERSION = '1.0.0-alpha'
DEBUG = False

SECRET_KEY = os.environ.get('ITAMBOX_SECRET_KEY', '')

if not SECRET_KEY:
    import warnings
    SECRET_KEY = 'django-insecure-dev-only-change-me-in-production'
    warnings.warn(
        "ITAMBOX_SECRET_KEY environment variable is not set. Using insecure default key. "
        "Do NOT use this configuration in production!",
        UserWarning
    )

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'assets',
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
    'itambox.apps.ITAMBoxConfig',
    'core.apps.CoreConfig',
    'extras.apps.ExtrasConfig',
    'procurement.apps.ProcurementConfig',
    'rest_framework',
    'drf_spectacular',
    'users',
    'django_q',
    'graphene_django',
    'mozilla_django_oidc',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'itambox.middleware.RateLimitMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'itambox.middleware.CSPMiddleware',
    'itambox.middleware.CurrentUserMiddleware',
    'itambox.middleware.TenantMiddleware',
]

INTERNAL_IPS = [
    "127.0.0.1",
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
                'itambox.context_processors.breadcrumbs',
                'itambox.context_processors.notifications_processor',
                'itambox.context_processors.tenant_switcher_processor',
                'itambox.context_processors.base_template_processor',
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

DB_ENGINE = os.environ.get('ITAMBOX_DB_ENGINE', 'django.db.backends.postgresql')

if 'sqlite' in DB_ENGINE:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "SQLite is strictly deprecated and not supported in ITAMbox. "
        "Please use PostgreSQL 15+ for all environments."
    )

DATABASES = {
    'default': {
        'ENGINE': DB_ENGINE,
        'NAME': os.environ.get('ITAMBOX_DB_NAME', 'itambox'),
        'USER': os.environ.get('ITAMBOX_DB_USER', 'itambox'),
        'PASSWORD': os.environ.get('ITAMBOX_DB_PASSWORD', 'itambox'),
        'HOST': os.environ.get('ITAMBOX_DB_HOST', 'localhost'),
        'PORT': os.environ.get('ITAMBOX_DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('ITAMBOX_DB_CONN_MAX_AGE', '300')),
        'OPTIONS': {
            'sslmode': os.environ.get('ITAMBOX_DB_SSLMODE', 'prefer'),
        },
        'TEST': {
            'NAME': 'oidc_test_db',
        },
    }
}

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
# STATIC_ROOT is the collectstatic target. Required for `manage.py collectstatic`
# (and therefore for the production image build) — Django raises without it.
STATIC_ROOT = os.environ.get('ITAMBOX_STATIC_ROOT', str(BASE_DIR / 'staticfiles'))
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

MEDIA_URL = '/media/'
MEDIA_ROOT = os.environ.get('ITAMBOX_MEDIA_ROOT', str(BASE_DIR / 'media'))

# Local static end-user documentation root
DOCS_ROOT = os.environ.get('ITAMBOX_DOCS_ROOT', BASE_DIR / 'docs')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'itambox.api.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'itambox.api.permissions.TokenPermissions',
        'itambox.api.permissions.StrictTenantPermission',
    ],
    'DEFAULT_PAGINATION_CLASS': 'itambox.api.pagination.ITAMBoxPagination',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
    'VIEW_NAME_FUNCTION': 'itambox.api.utils.get_view_name',
}


SPECTACULAR_SETTINGS = {
    'TITLE': 'ITAMbox API',
    'DESCRIPTION': 'IT Asset Management API',
    'VERSION': '1.0.0',
    'COMPONENT_SPLIT_REQUEST': True,  # Critical for clean client gen
    'ENUM_ADD_EXPLICIT_BLANK_NULL_CHOICE': False,
    'SCHEMA_PATH_PREFIX': r'/api/',
}


PAGINATE_COUNT = 50
MAX_PAGE_SIZE = 1000

CACHE_BACKEND = os.environ.get('ITAMBOX_CACHE_BACKEND', 'locmem')
if CACHE_BACKEND == 'redis':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': os.environ.get('ITAMBOX_REDIS_URL', 'redis://127.0.0.1:6379/1'),
            'TIMEOUT': int(os.environ.get('ITAMBOX_CACHE_TIMEOUT', '300')),
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'itambox-cache',
        }
    }

SEARCH_BACKEND = 'core.search_backends.DatabaseBackend'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# Suppress djangosaml2 django-csp warnings since we manage CSP headers manually
SAML_CSP_HANDLER = ''

AUTHENTICATION_BACKENDS = [
    'core.auth.TenantMembershipBackend',
    'core.auth.ldap.MultiTenantLDAPBackend',
    'core.auth.saml.TenantSaml2Backend',
    'core.auth.oidc.TenantOIDCBackend',
    'core.auth.PasswordLoginOnlyBackend',
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

import json
import logging
try:
    ITAMBOX_TENANT_LDAP_CONFIGS = json.loads(os.environ.get('ITAMBOX_TENANT_LDAP_CONFIGS', '{}'))
except Exception:
    ITAMBOX_TENANT_LDAP_CONFIGS = {}

try:
    ITAMBOX_TENANT_SAML_CONFIGS = json.loads(os.environ.get('ITAMBOX_TENANT_SAML_CONFIGS', '{}'))
except Exception:
    ITAMBOX_TENANT_SAML_CONFIGS = {}

try:
    ITAMBOX_TENANT_OIDC_CONFIGS = json.loads(os.environ.get('ITAMBOX_TENANT_OIDC_CONFIGS', '{}'))
except Exception as e:
    logging.getLogger(__name__).warning('Failed to parse ITAMBOX_TENANT_OIDC_CONFIGS: %s', e)
    ITAMBOX_TENANT_OIDC_CONFIGS = {}


# SAML SSO Configuration Loader
SAML_CONFIG_LOADER = 'core.auth.saml.load_saml_config'
SAML_USE_NAME_ID_AS_USERNAME = True
SAML_CREATE_UNKNOWN_USER = True
SAML_ATTRIBUTE_MAPPING = {
    'email': ('email',),
    'first_name': ('first_name', 'givenName'),
    'last_name': ('last_name', 'sn'),
}



# ==============================================================================
# Django-Q2 Background Tasks Cluster Settings
# ==============================================================================
Q_CLUSTER = {
    'name': 'ITAMbox-Cluster',
    'workers': 2,            # Safe count for local developer workloads
    'recycle': 500,
    'timeout': 600,
    'retry': 660,
    'compress': True,
    'cpu_affinity': 1,
    'label': 'Django Q Cluster',
    'orm': 'default',        # Standard database ORM broker
}

import sys
if 'test' in sys.argv or any('test' in arg or 'pytest' in arg for arg in sys.argv):
    Q_CLUSTER['sync'] = True


ALLOW_GLOBAL_CUSTODY_TEMPLATES = os.environ.get('ITAMBOX_ALLOW_GLOBAL_CUSTODY_TEMPLATES', 'True') == 'True'
REQUIRE_CUSTODY_SIGNIN = os.environ.get('ITAMBOX_REQUIRE_CUSTODY_SIGNIN', 'True') == 'True'


# ==============================================================================
# Plugins Configuration
# ==============================================================================
# Plugins are optional Django apps loaded at settings time (see
# itambox/plugins/utils.py). They are configured via a comma-separated env var
# so the core image boots without bundling any plugin. A missing/uninstalled
# plugin listed here raises ImproperlyConfigured at startup, so do not hardcode
# plugins that are not guaranteed to be installed.
#   Example: ITAMBOX_PLUGINS=itambox_esign
PLUGINS = [
    p.strip()
    for p in os.environ.get('ITAMBOX_PLUGINS', '').split(',')
    if p.strip()
]

IS_TESTING = 'test' in sys.argv or any('test' in arg or 'pytest' in arg for arg in sys.argv)

PLUGINS_CONFIG = {
    'itambox_esign': {
        'DOCUSIGN_INTEGRATION_KEY': os.environ.get('DOCUSIGN_INTEGRATION_KEY', 'mock-integration-key-guid' if IS_TESTING else ''),
        'DOCUSIGN_USER_ID': os.environ.get('DOCUSIGN_USER_ID', 'mock-user-id-guid' if IS_TESTING else ''),
        'DOCUSIGN_ACCOUNT_ID': os.environ.get('DOCUSIGN_ACCOUNT_ID', 'mock-account-id-guid' if IS_TESTING else ''),
        'DOCUSIGN_RSA_PRIVATE_KEY': os.environ.get('DOCUSIGN_RSA_PRIVATE_KEY', '-----BEGIN RSA PRIVATE KEY-----\nMockKey\n-----END RSA PRIVATE KEY-----' if IS_TESTING else ''),
        'DOCUSIGN_SANDBOX': os.environ.get('DOCUSIGN_SANDBOX', 'True').lower() in ('true', '1', 't'),
    }
}

# Load and validate plugins dynamically
import sys
from itambox.plugins.utils import load_plugins
load_plugins(sys.modules[__name__])

GRAPHENE = {
    'SCHEMA': 'core.schema.schema',
    'MIDDLEWARE': [],
}

# ==============================================================================
# Logging
# ==============================================================================
# Console logging is the right default for containers (the orchestrator
# captures stdout/stderr). Level is env-tunable; Django request 5xx errors and
# our app loggers are surfaced explicitly.
LOG_LEVEL = os.environ.get('ITAMBOX_LOG_LEVEL', 'INFO').upper()

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'itambox': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
}


# ==============================================================================
# CORS Configuration
# ==============================================================================
CORS_ALLOW_ALL_ORIGINS = os.environ.get('ITAMBOX_CORS_ALLOW_ALL_ORIGINS', 'False').lower() in ('true', '1', 'yes')
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('ITAMBOX_CORS_ALLOWED_ORIGINS', '').split(',')
    if origin.strip()
]




