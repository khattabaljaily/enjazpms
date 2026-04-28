"""
Django settings for EnjazIMS project.
"""

import os
import json
from pathlib import Path
from django.contrib.messages import constants as messages

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load secrets from JSON file
with open(os.path.join(BASE_DIR, 'secrets.json')) as json_file:
    creds = json.load(json_file)

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = creds['SECRET_KEY']

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = creds['DEBUG']

ALLOWED_HOSTS = creds['ALLOWED_HOSTS']

# CSRF & Security
CSRF_TRUSTED_ORIGINS = ['https://' + i for i in ALLOWED_HOSTS if i not in ['127.0.0.1', 'localhost']]
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
    'apps.sales',
    # 'apps.purchases',
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
    'default': creds['DATABASE']
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

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

CORS_ALLOW_CREDENTIALS = True

# ==========================================
# Email Configuration
# ==========================================

email_settings = creds.get('EMAIL', {})
default_email_account = email_settings.get('INFO', {})


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


EMAIL_BACKEND = creds.get(
    'EMAIL_BACKEND',
    email_settings.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
)
EMAIL_HOST = creds.get('EMAIL_HOST', default_email_account.get('HOST', 'localhost'))
EMAIL_PORT = int(creds.get('EMAIL_PORT', default_email_account.get('PORT', 25)))
EMAIL_USE_SSL = _as_bool(creds.get('EMAIL_USE_SSL', email_settings.get('EMAIL_USE_SSL')), default=False)
EMAIL_HOST_USER = creds.get('EMAIL_HOST_USER', default_email_account.get('USER', ''))
EMAIL_HOST_PASSWORD = creds.get('EMAIL_HOST_PASSWORD', default_email_account.get('PASSWORD', ''))
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

INTERNAL_IPS = creds.get('internal_ips', ['127.0.0.1'])
