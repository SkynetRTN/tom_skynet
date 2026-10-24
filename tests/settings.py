"""Minimal TOM settings for testing tom_skynet in isolation"""

from tom_common.default_settings import *

SECRET_KEY = "tom-skynet-tests"
DEBUG = True
INSTALLED_APPS = [*TOMTOOLKIT_INSTALLED_APPS, "tom_skynet"]
MIDDLEWARE = TOMTOOLKIT_MIDDLEWARE
AUTHENTICATION_BACKENDS = TOMTOOLKIT_AUTHENTICATION_BACKENDS
ROOT_URLCONF = "tom_common.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
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
            ]
        },
    }
]
SITE_ID = 1
USE_TZ = True
# Not UTC, on purpose: the form's times are UTC whatever the TOM's zone is.
TIME_ZONE = "America/New_York"
CRISPY_TEMPLATE_PACK = "bootstrap5"
STATIC_URL = "/static/"
TARGET_TYPE = "SIDEREAL"
FACILITIES = {}
TOM_FACILITY_CLASSES = []
HOOKS = {}
EXTRA_FIELDS = []
MATCH_MANAGERS = {}
TARGET_PERMISSIONS_ONLY = True
TARGET_DEFAULT_PERMISSION = "PRIVATE"
AUTH_STRATEGY = "READ_ONLY"
