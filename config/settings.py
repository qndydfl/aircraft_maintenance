import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

ENV_FILE = os.getenv("DJANGO_ENV_FILE", BASE_DIR / ".env")
load_dotenv(ENV_FILE)


# Environment
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost").split(",")]
USE_R2 = os.getenv("USE_R2", "FALSE").upper() == "TRUE"

# 개발 환경
# DEBUG=True
# ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
# USE_R2 = False


DEFAULT_SECRET_KEY = "django-insecure-local-dev-key"

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEFAULT_SECRET_KEY)

AWS_ACCOUNT_ID = os.environ.get("CLOUDFLARE_R2_ACCOUNT_ID")
AWS_ACCESS_KEY_ID = os.environ.get("CLOUDFLARE_R2_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("CLOUDFLARE_R2_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.environ.get("CLOUDFLARE_R2_BUCKET_NAME")

AWS_S3_ENDPOINT_URL = (
    f"https://{AWS_ACCOUNT_ID}.r2.cloudflarestorage.com"
    if AWS_ACCOUNT_ID
    else ""
)
AWS_S3_REGION_NAME = "auto"
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = False
AWS_S3_FILE_OVERWRITE = False


if USE_R2:
    missing_r2_settings = [
        name
        for name, value in [
            ("CLOUDFLARE_R2_ACCOUNT_ID", AWS_ACCOUNT_ID),
            ("CLOUDFLARE_R2_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID),
            ("CLOUDFLARE_R2_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY),
            ("CLOUDFLARE_R2_BUCKET_NAME", AWS_STORAGE_BUCKET_NAME),
        ]
        if not value
    ]

    if missing_r2_settings:
        raise ImproperlyConfigured(
            "USE_R2=True 이지만 R2 설정이 누락되었습니다: "
            + ", ".join(missing_r2_settings)
        )

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "manuals",
    "dispatch",
    "storages",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.site_dates",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 30,
        },
        "CONN_MAX_AGE": 60,
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "ko-kr"

TIME_ZONE = "Asia/Seoul"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "static/"

STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# 세션 만료 시간 설정
SESSION_COOKIE_AGE = 3600      # 1시간
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True


TESSERACT_OCR_LANG = "eng+kor"


SITE_CREATED_DATE = os.getenv("SITE_CREATED_DATE", "Jun 2026")
SITE_REVISION_DATE = os.getenv("SITE_REVISION_DATE", "01 Jul 2026")
SITE_REVISION_NO = os.getenv("SITE_REVISION_NO", "Rev. 01")
