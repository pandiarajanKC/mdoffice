"""Application configuration loaded from environment variables.

No credentials or server details are hardcoded here — everything comes
from the process environment (populated from .env in development).
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

# Project root (one level above the app/ package) — anchors UPLOAD_FOLDER
# below so saved files land in the same place regardless of the process's
# current working directory when it was launched (a plain "uploads"
# relative path would otherwise resolve differently depending on whether
# the server was started from the project root, from inside app/, etc.,
# silently splitting uploads across two different folders).
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_mssql_uri(server: str, database: str, username: str, password: str, driver: str) -> str:
    odbc_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
        f"Encrypt=yes;"
    )
    return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development")
    DEBUG = APP_ENV == "development"
    TESTING = False

    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY is not set. Define it in your .env file.")

    PRODUCT_NAME = os.environ.get("PRODUCT_NAME", "MD Office")
    PRODUCT_SUBTITLE = os.environ.get("PRODUCT_SUBTITLE", "Executive Follow-up & Action Management")

    # ---- Database (application) ----
    DB_SERVER = os.environ.get("DB_SERVER", "")
    DB_USERNAME = os.environ.get("DB_USERNAME", "")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_DRIVER = os.environ.get("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    DB_APP_DATABASE = os.environ.get("DB_APP_DATABASE", "mdoffice")

    SQLALCHEMY_DATABASE_URI = _build_mssql_uri(
        DB_SERVER, DB_APP_DATABASE, DB_USERNAME, DB_PASSWORD, DB_DRIVER
    )
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- Database (employee master, read-only) ----
    DB_EMPLOYEE_DATABASE = os.environ.get("DB_EMPLOYEE_DATABASE", "CUSTOMER_MASTER")
    DB_EMPLOYEE_TABLE = os.environ.get("DB_EMPLOYEE_TABLE", "EmployeeList")
    EMPLOYEE_DATABASE_URI = _build_mssql_uri(
        DB_SERVER, DB_EMPLOYEE_DATABASE, DB_USERNAME, DB_PASSWORD, DB_DRIVER
    )

    # ---- Sessions / cookies ----
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * int(os.environ.get("REMEMBER_COOKIE_DURATION_DAYS", 7))
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    # ---- Login throttling ----
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", 5))
    LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", 15))

    # ---- Uploads ----
    # os.path.join discards _BASE_DIR automatically if UPLOAD_FOLDER is
    # already an absolute path (e.g. set that way for production), so this
    # is safe either way — see the _BASE_DIR comment above for why a bare
    # relative default isn't.
    UPLOAD_FOLDER = os.path.join(_BASE_DIR, os.environ.get("UPLOAD_FOLDER", "uploads"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH_MB", 100)) * 1024 * 1024

    # ---- AI ----
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "openai")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    OPENAI_TRANSCRIBE_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "tts-1")
    OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "nova")  # "nova" reads as warm/friendly per OpenAI's voice guide

    # ---- Meeting audio uploads ----
    # Broad on purpose: most recordings arrive as WhatsApp voice notes
    # (.opus, .ogg) or phone voice-memo exports (.aac, .3gp), not just the
    # "standard" web formats. See app/services/ai/openai_transcription_service.py
    # for how .opus is relabeled to .ogg before it reaches the Whisper API,
    # since OpenAI's documented format list doesn't include ".opus" directly.
    ALLOWED_AUDIO_EXTENSIONS = {
        ".mp3", ".wav", ".m4a", ".webm", ".ogg", ".oga", ".opus",
        ".flac", ".mp4", ".mpeg", ".mpga", ".aac", ".3gp", ".amr",
    }
    MAX_AUDIO_SIZE_MB = int(os.environ.get("MAX_AUDIO_SIZE_MB", 100))

    # ---- Google Calendar OAuth ----
    GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    GOOGLE_OAUTH_REDIRECT_URI = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")

    # ---- Token encryption (OAuth refresh/access tokens at rest) ----
    TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

    # ---- Background scheduler (reminders, escalations) ----
    ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "true").lower() == "true"

    # ---- Logging ----
    LOG_DIR = os.environ.get("LOG_DIR", "logs")


class DevelopmentConfig(Config):
    DEBUG = True


class UatConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    ENABLE_SCHEDULER = False


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "uat": UatConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    env = os.environ.get("APP_ENV", "development")
    return CONFIG_MAP.get(env, DevelopmentConfig)
