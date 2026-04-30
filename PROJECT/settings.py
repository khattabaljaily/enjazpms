"""
Django settings for EnjazIMS project.
"""

import json
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from django.contrib.messages import constants as messages

# ─────────────────────────────────────────────
# Base paths
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
SECRETS_FILE = BASE_DIR / 'secrets.json'


# ─────────────────────────────────────────────
# Load secrets safely
# ─────────────────────────────────────────────
def load_secrets():
    try:
        with SECRETS_FILE.open(encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise ImproperlyConfigured("Create secrets.json in project root") from exc


creds = load_secrets()


def get_secret(key, default=None):
    if key in creds:
        return creds[key]
    if default is not None:
        return default
    raise ImproperlyConfigured(f'Missing "{key}" in secrets.json')


# ─────────────────────────────────────────────
# Core security
# ─────────────────────────────────────────────
SECRET_KEY = get_secret('SECRET_KEY')
DEBUG = get_secret('DEBUG', False)

ALLOWED_HOSTS = get_secret('ALLOWED_HOSTS', [])

# Normalize host names and remove ports like :443
ALLOWED_HOSTS = [host.split(':')[0].strip() for host in ALLOWED_HOSTS if host]

# Build CSRF trusted origins from allowed hosts for production
CSRF_TRUSTED_ORIGINS = [
    f'https://{host}'
    for host in ALLOWED_HOSTS
    if host and host not in ('127.0.0.1', 'localhost')
]


# ─────────────────────────────────────────────
# Proxy (IMPORTANT)
# ─────────────────────────────────────────────
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# مهم جداً عشان Cloudflare
SECURE_SSL_REDIRECT = False


# ─────────────────────────────────────────────
# Apps
# ─────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'corsheaders',

    'apps.core',
    'apps.accounts',
    'apps.customers',
    'apps.suppliers',
    'apps.items',
    'apps.purchases',
    'apps.sales',
    'apps.treasury',
    'apps.expenses',
    'apps.stocks',
]


# ─────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',

    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    'apps.core.middleware.TenantMiddleware',
    'apps.core.middleware.ActiveTenantMiddleware',
]


ROOT_URLCONF = 'PROJECT.urls'


# ─────────────────────────────────────────────
# Templates
# ─────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',

                'apps.core.context_processors.tenant_context',
                'apps.core.context_processors.app_context',
            ],
        },
    },
]


WSGI_APPLICATION = 'PROJECT.wsgi.application'


# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
DATABASES = {
    'default': get_secret('DATABASE')
}


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────
AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = 'core:dashboard'
LOGOUT_REDIRECT_URL = '/accounts/login/'


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ─────────────────────────────────────────────
# Internationalization
# ─────────────────────────────────────────────
LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Africa/Cairo'

USE_I18N = True
USE_TZ = True


# ─────────────────────────────────────────────
# Static / Media
# ─────────────────────────────────────────────
STATIC_URL = '/static/'

if DEBUG:
    STATICFILES_DIRS = [BASE_DIR / 'static']
else:
    STATIC_ROOT = BASE_DIR / 'static'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ─────────────────────────────────────────────
# REST Framework
# ─────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'PAGE_SIZE': 25,
}


# ─────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────
CORS_ALLOW_CREDENTIALS = True


# ─────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────
MESSAGE_TAGS = {
    messages.DEBUG: 'alert-secondary',
    messages.INFO: 'alert-info',
    messages.SUCCESS: 'alert-success',
    messages.WARNING: 'alert-warning',
    messages.ERROR: 'alert-danger',
}


# ─────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 86400


# ─────────────────────────────────────────────
# Internal IPs
# ─────────────────────────────────────────────
INTERNAL_IPS = get_secret('internal_ips', ['127.0.0.1'])