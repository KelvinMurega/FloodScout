"""
Django settings for the FloodScout project.

Matches the architecture in the proposal, Chapter 4 (System Analysis and
Design): GeoDjango + PostGIS as the Data Tier, Django REST Framework as the
Application Tier's REST API (IR-01), CORS enabled for the Leaflet.js
dashboard (IR-02), and one custom User model with a `role` field standing in
for both the System Administrator and End User actors from the Use Case and
Class Diagrams (Sections 4.3.1 and 4.3.4) — see accounts/models.py.
"""

import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env.bool("DJANGO_DEBUG", default=False)
TESTING = sys.argv[1:2] == ["test"]

# No fallback: a key everyone can read in this file signs nothing safely.
# Outside DEBUG, the placeholder from .env.example is refused as well.
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not DEBUG and SECRET_KEY.startswith(("change-me", "django-insecure")):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is still the example value. Generate one with: python -c "
        '"from django.core.management.utils import get_random_secret_key as k; print(k())"'
    )

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Where the Django admin lives. Set it to something not guessable in
# production, so the sign-in page is not where every scanner looks first.
ADMIN_PATH = env("DJANGO_ADMIN_PATH", default="admin/").strip("/") + "/"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",  # GeoDjango: spatial fields/queries behind FR-01/FR-02

    "rest_framework",
    "rest_framework.authtoken",  # required by TokenAuthentication in REST_FRAMEWORK below
    "corsheaders",

    "accounts",
    "geodata",
    "riskengine",
    "alerts",
    "api",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # The region shapes are megabytes of GeoJSON; gzip cuts them to about a
    # fifth, which is the difference between a phone loading the map or not.
    "django.middleware.gzip.GZipMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "floodscout.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "floodscout.wsgi.application"
ASGI_APPLICATION = "floodscout.asgi.application"

# Data Tier — PostgreSQL + PostGIS (Chapter 4, Section 4.4.1/4.4.2).
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("DB_NAME", default="floodscout"),
        "USER": env("DB_USER", default="floodscout"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        # Seconds to keep a connection open between requests. The hosted
        # database is across a network and every new connection pays for a
        # TLS handshake, so production keeps them (DB_CONN_MAX_AGE=60).
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=0),
        # Azure's database refuses connections without TLS: DB_SSLMODE=require.
        "OPTIONS": {"sslmode": env("DB_SSLMODE", default="prefer")},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CORS — allows the Leaflet.js dashboard (served separately in dev, per the
# System Architecture diagram's Presentation Tier) to call this API.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:5173", "http://127.0.0.1:5173"],
)

# Production runs behind Cloudflare and Caddy (deploy/Caddyfile), which ends
# HTTPS and says so in X-Forwarded-Proto. Trust that header only behind a
# proxy that sets it; reached directly, a client could claim HTTPS with it.
NUM_PROXIES = env.int("DJANGO_NUM_PROXIES", default=0)
if NUM_PROXIES:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
SESSION_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
# Off in production too: Cloudflare already sends every visitor to HTTPS,
# and a second redirect behind it can loop.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_CONTENT_TYPE_NOSNIFF = True
# What `manage.py check --deploy` may leave unsaid (CI runs it):
# W008, SECURE_SSL_REDIRECT is off on purpose (above).
# W021, HSTS preload is not set: joining browsers' preload list is close to
# permanent and is not wanted for a student project's domain.
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W021"]

REST_FRAMEWORK = {
    # Token first: DRF takes the challenge header from the first class, and
    # only a class that has one makes an anonymous request a 401 instead of
    # a 403. The dashboard signs in at api/auth/token/.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    # Every list is paged unless its view opts out (api/pagination.py).
    "DEFAULT_PAGINATION_CLASS": "api.pagination.Pages",
    # Every request counts against its client: by address when anonymous,
    # by account when signed in. One dashboard load is about 15 requests.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    # Sign-in attempts per client address (accounts/views.py), and alert
    # sign-ups and code entries (alerts/views.py), which send messages.
    # The test suite makes hundreds of requests a minute from one address,
    # so it runs without the two general limits (a test turns them back on).
    "DEFAULT_THROTTLE_RATES": {
        "anon": None if TESTING else "120/min",
        "user": None if TESTING else "240/min",
        "login": "10/min",
        "alerts": "20/hour",
    },
    # How many proxies sit in front of the API. With the DRF default (None)
    # the client's own X-Forwarded-For header names its address, so a new
    # made-up address per request escapes every limit above. 0 means the
    # API is reached directly and REMOTE_ADDR is the client; set it to 1
    # behind one reverse proxy that sets the header.
    "NUM_PROXIES": NUM_PROXIES,
}

# Alerts (alerts/). Email goes to an SMTP server when EMAIL_HOST is set, and
# is otherwise written as files under data/outbox/, so the flow can be shown
# without sending anything. Gmail works with an app password:
# EMAIL_HOST=smtp.gmail.com, EMAIL_PORT=587, EMAIL_USE_TLS=True.
EMAIL_HOST = env("EMAIL_HOST", default="")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = env.int("EMAIL_PORT", default=587)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
    EMAIL_FILE_PATH = BASE_DIR / "data" / "outbox"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="FloodScout <alerts@floodscout.local>")

# Africa's Talking SMS. AT_USERNAME "sandbox" sends to the simulator on the
# Africa's Talking dashboard instead of to phones. No key, no SMS.
AT_USERNAME = env("AT_USERNAME", default="sandbox")
AT_API_KEY = env("AT_API_KEY", default="")
AT_SENDER_ID = env("AT_SENDER_ID", default="")

# Where links in messages point: the API (unsubscribe) and the dashboard.
PUBLIC_API_URL = env("PUBLIC_API_URL", default="http://localhost:8000/api")
DASHBOARD_URL = env("DASHBOARD_URL", default="http://localhost:5173")

# Sign in with Google, Microsoft or GitHub (accounts/social.py). A provider
# is offered only when both its values are set. Register this redirect URI
# with each: <PUBLIC_API_URL>/auth/social/<google|microsoft|github>/callback/
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
MICROSOFT_CLIENT_ID = env("MICROSOFT_CLIENT_ID", default="")
MICROSOFT_CLIENT_SECRET = env("MICROSOFT_CLIENT_SECRET", default="")
GITHUB_CLIENT_ID = env("GITHUB_CLIENT_ID", default="")
GITHUB_CLIENT_SECRET = env("GITHUB_CLIENT_SECRET", default="")

# CAP status of the published alerts. "Exercise" says they are not official.
CAP_STATUS = env("CAP_STATUS", default="Exercise")
