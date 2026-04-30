"""
Django settings for EnjazIMS project.
"""

import os
import json
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from django.contrib.messages import constants as messages

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load secrets from JSON file
SECRETS_FILE = BASE_DIR / 'secrets.json'

with open(SECRETS_FILE) as json_file:
    creds = json.load(json_file)

def get_secret(key: str, default=None):
    if key in creds:
        return creds[key]
    if default is not None:
        return default
    raise ImproperlyConfigured(f'Missing "{key}" in secrets.json')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = get_secret('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = get_secret('DEBUG')

ALLOWED_HOSTS = get_secret('ALLOWED_HOSTS')

# CSRF & Security
CSRF_TRUSTED_ORIGINS = [
    "127.0.0.1",
    "localhost",
    "https://imspro.enjaztechnology.com",
    "https://www.imspro.enjaztechnology.com"
]
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'corsheaders',
    
    # Local apps
    'apps.core',
    'apps.accounts',
    'apps.customers',
    'apps.suppliers',
    
    # Local apps (will be added as we create them)
    # 'apps.branches',
    'apps.items',
    'apps.purchases',
    'apps.sales',
    'apps.treasury',
    'apps.expenses',
    'apps.stocks',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # CORS must be before CommonMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    
    # Custom middleware
    'apps.core.middleware.TenantMiddleware',
    'apps.core.middleware.ActiveTenantMiddleware',
]

ROOT_URLCONF = 'PROJECT.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],  # Templates now inside each app
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',
                
                # Custom context processors
                'apps.core.context_processors.tenant_context',
                'apps.core.context_processors.app_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'PROJECT.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': get_secret('DATABASE')
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators


# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Login URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = 'core:dashboard'
LOGOUT_REDIRECT_URL = '/accounts/login/'


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'ar'

TIME_ZONE = 'Africa/Cairo'

USE_I18N = True

# Keep Arabic UI language while forcing numeric display format to 1,000.00
USE_THOUSAND_SEPARATOR = True
THOUSAND_SEPARATOR = ','
DECIMAL_SEPARATOR = '.'
NUMBER_GROUPING = 3

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

if DEBUG:
    STATICFILES_DIRS = [
        os.path.join(BASE_DIR, 'static/'),
        *[
            str(path)
            for path in (BASE_DIR / 'apps').glob('*/css')
            if path.is_dir()
        ],
    ]
else:
    STATIC_ROOT = os.path.join(BASE_DIR, 'static/')

STATIC_URL = '/static/'

# Media files
MEDIA_ROOT = os.path.join(BASE_DIR, 'media/')
MEDIA_URL = '/media/'

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==========================================
# REST Framework Configuration
# ==========================================

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
}

# ==========================================
# CORS Configuration
# ==========================================

def _build_default_csrf_trusted_origins(hosts):
    trusted_origins = []
    for host in hosts:
        host = str(host).strip()
        if not host or host == '*':
            continue
        if '://' in host:
            trusted_origins.append(host)
            continue
        if host.startswith('.'):
            host = f'*.{host[1:]}'
        trusted_origins.append(f'https://{host}')
        trusted_origins.append(f'http://{host}')
    return list(dict.fromkeys(trusted_origins))

CSRF_TRUSTED_ORIGINS = get_secret(
    'CSRF_TRUSTED_ORIGINS',
    _build_default_csrf_trusted_origins(ALLOWED_HOSTS),
)

if get_secret('USE_REVERSE_PROXY_SSL_HEADER', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

CORS_ALLOW_CREDENTIALS = True

# ==========================================
# Email Configuration
# ==========================================

email_settings = get_secret('EMAIL', {})
default_email_account = email_settings.get('INFO', {})


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


EMAIL_BACKEND = get_secret(
    'EMAIL_BACKEND',
    email_settings.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
)
EMAIL_HOST = get_secret('EMAIL_HOST', default_email_account.get('HOST', 'localhost'))
EMAIL_PORT = int(get_secret('EMAIL_PORT', default_email_account.get('PORT', 25)))
EMAIL_USE_SSL = _as_bool(get_secret('EMAIL_USE_SSL', email_settings.get('EMAIL_USE_SSL')), default=False)
EMAIL_HOST_USER = get_secret('EMAIL_HOST_USER', default_email_account.get('USER', ''))
EMAIL_HOST_PASSWORD = get_secret('EMAIL_HOST_PASSWORD', default_email_account.get('PASSWORD', ''))
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

# ==========================================
# Messages Framework
# ==========================================

MESSAGE_TAGS = {
    messages.DEBUG: 'alert-secondary',
    messages.INFO: 'alert-info',
    messages.SUCCESS: 'alert-success',
    messages.WARNING: 'alert-warning',
    messages.ERROR: 'alert-danger',
}

# ==========================================
# Session Configuration
# ==========================================

SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_SAVE_EVERY_REQUEST = False

# ==========================================
# Internal IPs (for Debug Toolbar)
# ==========================================

INTERNAL_IPS = get_secret('internal_ips', ['127.0.0.1'])
