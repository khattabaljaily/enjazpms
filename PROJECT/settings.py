import json
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from django.contrib.messages import constants as messages

BASE_DIR = Path(__file__).resolve().parent.parent
SECRETS_FILE = BASE_DIR / 'secrets.json'


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


SECRET_KEY = get_secret('SECRET_KEY')
DEBUG = get_secret('DEBUG', False)
ALLOWED_HOSTS = get_secret('ALLOWED_HOSTS', [])


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
    'apps.agents',
    'apps.items',
    'apps.purchases',
    'apps.sales',
    'apps.treasury',
    'apps.expenses',
    'apps.stocks',
    'apps.notifications',
    'django.contrib.humanize',
    'apps.portal',
    'apps.ai',
    'apps.store',
    'apps.employees',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.MaintenanceModeMiddleware',
    'apps.core.middleware.TenantMiddleware',
    'apps.core.middleware.TermsMiddleware',
    'apps.core.middleware.TimezoneMiddleware',
    'apps.core.middleware.ActiveTenantMiddleware',
    'apps.accounts.activity_middleware.ActivityLogMiddleware',
]

ROOT_URLCONF = 'PROJECT.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'OPTIONS': {
            'builtins': ['apps.core.templatetags.permission_tags', 'apps.core.templatetags.number_format'],
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',

                'apps.core.context_processors.tenant_context',
                'apps.core.context_processors.app_context',
                'apps.core.context_processors.platform_context',
                'apps.core.context_processors.impersonation_context',
                'apps.core.context_processors.training_context',
            ],
        },
    },
]


WSGI_APPLICATION = 'PROJECT.wsgi.application'

DATABASE_CONFIG = get_secret('DATABASE')

DATABASES = {
    'default': get_secret('DATABASE')
}

AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Africa/Cairo'
USE_I18N = True
USE_TZ = True

STATIC_URL  = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL   = 'media/'
MEDIA_ROOT  = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Authentication ─────────────────────────────────────────────
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'core:home'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30   # 30 days
SESSION_SAVE_EVERY_REQUEST = True

CSRF_FAILURE_VIEW = 'PROJECT.error_views.csrf_failure'

# ── Email ──────────────────────────────────────────────────────
_email_cfg     = get_secret('EMAIL', {})
_email_noreply = _email_cfg.get('NOREPLY', {})
EMAIL_BACKEND       = _email_cfg.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST          = _email_noreply.get('HOST', '')
EMAIL_PORT          = int(_email_noreply.get('PORT', 465))
EMAIL_HOST_USER     = _email_noreply.get('USER', '')
EMAIL_HOST_PASSWORD = _email_noreply.get('PASSWORD', '')
EMAIL_USE_SSL       = str(_email_cfg.get('EMAIL_USE_SSL', 'True')).lower() == 'true'
EMAIL_USE_TLS       = False
DEFAULT_FROM_EMAIL  = f'ENJAZ <{EMAIL_HOST_USER}>'

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


# ─────────────────────────────────────────────
# Internal IPs
# ─────────────────────────────────────────────
INTERNAL_IPS = get_secret('internal_ips', ['127.0.0.1'])

# ── AI / DeepSeek ──────────────────────────────────────────────
DEEPSEEK_API_KEY = get_secret('DEEPSEEK_API_KEY', '')
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'
DEEPSEEK_MODEL   = 'deepseek-chat'

CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# ── Terms of Service ───────────────────────────────────────────
TERMS_VERSION = '2026-06'