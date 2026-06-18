"""
config.py — Portfolio CMS v5.0 — Production-Ready Configuration

Separation of concerns: development, production, and testing environments.
All secrets are environment variables - NEVER commit secrets to repository.

Environment Variables Required:
  Production:
    - SECRET_KEY (use secrets.token_urlsafe(32))
    - FERNET_KEY (for API key encryption: cryptography.fernet.Fernet.generate_key())
    - CORE_DATABASE_URL
    - TENANT_DATABASE_URL
    - PAYMONGO_SECRET_KEY
    - PAYMONGO_WEBHOOK_SECRET
    - MAILERSEND_API_KEY
  
  Optional:
    - REDIS_URL (for caching/rate limiting)
    - SENTRY_DSN (for error tracking)
"""

import os
import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote
from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


def _normalize_postgres_url(url: str) -> str:
    """Normalize postgres:// → postgresql:// (Render/Heroku/Supabase quirk)."""
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


class BaseConfig:
    """Base configuration with security defaults."""
    
    # ─────────────────────────────────────────────────────────────────
    # SECURITY
    # ─────────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get('SECRET_KEY') or ''

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour
    WTF_CSRF_CHECK_DEFAULT = True
    WTF_CSRF_SSL_STRICT = True  # Enforce HTTPS in production
    
    # Session & Cookie Security (single source of truth)
    # SESSION_COOKIE_SECURE=False here; overridden to True in ProductionConfig
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    
    # ─────────────────────────────────────────────────────────────────
    # DATABASE — DUAL-DB ARCHITECTURE
    # ─────────────────────────────────────────────────────────────────
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_RECORD_QUERIES = False  # Enable in development for profiling
    SQLALCHEMY_SLOW_QUERY_THRESHOLD = 0.5
    
    # Connection Pool (overridden per environment)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 10,
    }
    
    # ─────────────────────────────────────────────────────────────────
    # AUTHENTICATION & ENCRYPTION
    # ─────────────────────────────────────────────────────────────────
    FERNET_KEY = os.environ.get('FERNET_KEY') or ''
    
    # OTP/TOTP Settings
    TOTP_ISSUER = os.environ.get('TOTP_ISSUER', 'Portfolio CMS')
    TOTP_VALID_WINDOW = int(os.environ.get('TOTP_VALID_WINDOW', '1'))
    OTP_EXPIRATION_SECONDS = int(os.environ.get('OTP_EXPIRATION_SECONDS', '600'))  # 10 minutes
    OTP_MAX_ATTEMPTS = int(os.environ.get('OTP_MAX_ATTEMPTS', '5'))
    
    # Password Policy
    MIN_PASSWORD_LENGTH = 12
    REQUIRE_UPPERCASE = True
    REQUIRE_NUMBERS = True
    REQUIRE_SPECIAL_CHARS = True
    
    # ─────────────────────────────────────────────────────────────────
    # RATE LIMITING & CACHING
    # ─────────────────────────────────────────────────────────────────
    # FIX (config-redis-crash): the previous `raise RuntimeError(...)` here ran
    # at MODULE IMPORT time inside the BaseConfig class body — i.e. on every
    # `import config`, in every environment (dev, test, CLI scripts, Alembic),
    # not just production. Any shell without REDIS_URL set could not even
    # import the app. Production already enforces REDIS_URL via
    # ProductionConfig.init_app()'s required_vars check below (request-time,
    # environment-scoped, not import-time, not global). Redis is genuinely
    # optional for dev/test; Limiter falls back to memory:// at app-factory
    # time in app/__init__.py (see create_limiter_storage()).
    # NOTE (config-key-mismatch): flask-limiter's actual config key is
    # 'RATELIMIT_STORAGE_URI' (see flask_limiter.constants.ConfigVars.
    # STORAGE_URI) -- NOT 'RATELIMIT_STORAGE_URL'. This key is kept for
    # backward-compat / readability elsewhere in the codebase, but it is
    # NOT what flask-limiter consumes. The actual storage backend is
    # resolved and pushed into app.config['RATELIMIT_STORAGE_URI'] at
    # app-factory time in app/__init__.py:create_app(), after a Redis
    # pre-flight PING (see resolve_limiter_storage_uri()). Do not rely on
    # this key for flask-limiter behavior.
    RATELIMIT_STORAGE_URL = os.environ.get('REDIS_URL', 'memory://')
    RATELIMIT_DEFAULT = '100 per hour'
    RATELIMIT_HEADERS_ENABLED = True
    
    # Rate Limits (per minute unless specified)
    RATELIMIT_LOGIN = '5 per 15 minutes'
    RATELIMIT_REGISTER = '3 per 30 minutes'
    RATELIMIT_PASSWORD_RESET = '3 per 30 minutes'
    RATELIMIT_OTP_SEND = '3 per 30 minutes'
    RATELIMIT_OTP_VERIFY = '5 per 15 minutes'
    RATELIMIT_CONTACT_FORM = '5 per hour'
    RATELIMIT_WEBHOOKS = '200 per minute'
    
    # Caching
    CACHE_TYPE = 'SimpleCache'
    CACHE_DEFAULT_TIMEOUT = 300  # 5 minutes
    CACHE_REDIS_URL = os.environ.get('REDIS_URL', '')
    
    # ─────────────────────────────────────────────────────────────────
    # FILE UPLOADS & STORAGE
    # ─────────────────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
    ALLOWED_MIME_TYPES = {
        'image/png',
        'image/jpeg',
        'image/gif',
        'image/webp',
        'image/svg+xml',
    }
    
    # Supabase Storage
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
    SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
    SUPABASE_BUCKET = os.environ.get('SUPABASE_BUCKET', 'portfolio-media')
    USE_SUPABASE_STORAGE = os.environ.get('USE_SUPABASE_STORAGE', 'false').lower() == 'true'
    
    # ─────────────────────────────────────────────────────────────────
    # INTEGRATIONS
    # ─────────────────────────────────────────────────────────────────
    # Resend Email
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
    RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', '')
    
    # PayMongo Billing
    PAYMONGO_ENABLED = os.environ.get('PAYMONGO_ENABLED', 'false').lower() == 'true'
    PAYMONGO_PUBLIC_KEY = os.environ.get('PAYMONGO_PUBLIC_KEY', '')
    PAYMONGO_SECRET_KEY = os.environ.get('PAYMONGO_SECRET_KEY', '')
    PAYMONGO_WEBHOOK_SECRET = os.environ.get('PAYMONGO_WEBHOOK_SECRET', '')
    
    # Web3Forms
    WEB3FORMS_ACCESS_KEY = os.environ.get('WEB3FORMS_ACCESS_KEY', '')
    
    # Sentry Error Tracking
    SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
    
    # BetterStack Heartbeat
    BETTERSTACK_HEARTBEAT_URL = os.environ.get('BETTERSTACK_HEARTBEAT_URL', '')
    HEARTBEAT_SECRET = os.environ.get('HEARTBEAT_SECRET', '')
    
    # ─────────────────────────────────────────────────────────────────
    # BILLING & SUBSCRIPTIONS
    # ─────────────────────────────────────────────────────────────────
    APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')
    BILLING_GRACE_PERIOD_DAYS = int(os.environ.get('BILLING_GRACE_PERIOD_DAYS', '3'))
    PAYMENT_TIMEOUT_SECONDS = int(os.environ.get('PAYMENT_TIMEOUT_SECONDS', '600'))
    
    # ─────────────────────────────────────────────────────────────────
    # LOGGING & MONITORING
    # ─────────────────────────────────────────────────────────────────
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_DIR = os.path.join(basedir, 'logs')
    
    # ─────────────────────────────────────────────────────────────────
    # STATIC FILES
    # ─────────────────────────────────────────────────────────────────
    SEND_FILE_MAX_AGE_DEFAULT = 31536000  # 1 year for versioned assets
    
    @staticmethod
    def init_app(app):
        """Initialize upload directories."""
        upload_base = os.path.join(basedir, 'storage', 'uploads')
        os.makedirs(upload_base, exist_ok=True)
        os.makedirs(os.path.join(upload_base, 'profiles'), exist_ok=True)
        os.makedirs(os.path.join(upload_base, 'projects'), exist_ok=True)
        os.makedirs(os.path.join(upload_base, 'avatars'), exist_ok=True)
        os.makedirs(BaseConfig.LOG_DIR, exist_ok=True)
        
        app.config['UPLOAD_FOLDER'] = upload_base
        app.config['PROFILE_UPLOAD_FOLDER'] = os.path.join(upload_base, 'profiles')
        app.config['PROJECT_UPLOAD_FOLDER'] = os.path.join(upload_base, 'projects')
        app.config['AVATAR_UPLOAD_FOLDER'] = os.path.join(upload_base, 'avatars')


class DevelopmentConfig(BaseConfig):
    """Development environment configuration."""
    
    DEBUG = True
    TESTING = False
    SESSION_COOKIE_SECURE = False
    SEND_FILE_MAX_AGE_DEFAULT = 0
    
    # Detailed logging
    SQLALCHEMY_ECHO = True
    SQLALCHEMY_RECORD_QUERIES = True
    LOG_LEVEL = 'DEBUG'
    
    # Disable HTTPS requirement for development
    WTF_CSRF_SSL_STRICT = False
    
    # Loose rate limiting for development
    RATELIMIT_ENABLED = False
    
    # Loose cache for development
    CACHE_TYPE = 'SimpleCache'
    
    # Database configuration
    _core_db_file = Path(basedir) / 'storage' / 'portfolio_core_dev.db'
    _core_db_file.parent.mkdir(parents=True, exist_ok=True)
    _core_uri = _normalize_postgres_url(
        os.environ.get('DEV_CORE_DATABASE_URL', '')
    ) or f"sqlite:///{_core_db_file.resolve()}".replace('\\', '/')
    
    _tenant_db_file = Path(basedir) / 'storage' / 'portfolio_tenant_dev.db'
    _tenant_db_file.parent.mkdir(parents=True, exist_ok=True)
    _tenant_uri = _normalize_postgres_url(
        os.environ.get('DEV_TENANT_DATABASE_URL', '')
    ) or f"sqlite:///{_tenant_db_file.resolve()}".replace('\\', '/')
    
    SQLALCHEMY_DATABASE_URI = _core_uri
    SQLALCHEMY_BINDS = {'tenant': _tenant_uri}
    
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
    }


class ProductionConfig(BaseConfig):
    """Production environment configuration."""
    
    DEBUG = False
    TESTING = False
    
    # Enforce HTTPS
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    WTF_CSRF_SSL_STRICT = True
    
    # Optimize logging for production
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_RECORD_QUERIES = False
    LOG_LEVEL = 'WARNING'
    
    # Enable Redis caching
    CACHE_TYPE = 'RedisCache' if os.environ.get('REDIS_URL') else 'SimpleCache'
    
    # PostgreSQL for production
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': NullPool,
        'pool_pre_ping': True,
        'connect_args': {
        'sslmode': 'require',
        'connect_timeout': 10,
        'options': '-c statement_timeout=30000',
        'application_name': 'portfolio_cms_prod',
        },
    }

    # FIX (dead-validation-block): this block previously read
    # `app.config.get(...)` against the throwaway local Flask instance
    # created at module scope (line 33 of this file) for url_for context —
    # NOT the real application built by app/__init__.py:create_app(). It
    # always evaluated against `{}` and therefore never validated anything;
    # it also silently set a stray `ProductionConfig.options` class
    # attribute as a side effect. Real validation now lives in init_app()
    # below, where `app` is the actual Flask instance being configured.

    @classmethod
    def _validate_engine_options(cls, app):
        options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {})
        if isinstance(options, str):
            raise RuntimeError("SQLALCHEMY_ENGINE_OPTIONS must be dict, not string")
        if "poolclass" in options and isinstance(options["poolclass"], str):
            raise RuntimeError("poolclass must be SQLAlchemy class, not string")
        
    @classmethod
    def init_app(cls, app):
        """Initialize production configuration with validation."""
        BaseConfig.init_app(app)
        cls._validate_engine_options(app)
        
        # ─────────────────────────────────────────────────────────────
        # VALIDATE REQUIRED ENVIRONMENT VARIABLES
        # ─────────────────────────────────────────────────────────────
        required_vars = [
            'SECRET_KEY',
            'FERNET_KEY',
            'CORE_DATABASE_URL',
            'TENANT_DATABASE_URL',
            'PAYMONGO_SECRET_KEY',
            'PAYMONGO_WEBHOOK_SECRET',
            # FIX REDIS: memory:// fallback does not share state across workers;
            # rate limiting is ineffective without Redis in production.
            'REDIS_URL',
        ]
        
        missing = [var for var in required_vars if not os.environ.get(var)]
        if missing:
            raise ValueError(
                f"Production environment missing required variables: {', '.join(missing)}\n"
                "Configure these in your hosting platform's environment settings."
            )
        
        # ─────────────────────────────────────────────────────────────
        # CONFIGURE DATABASES
        # ─────────────────────────────────────────────────────────────
        core_url = _normalize_postgres_url(os.environ['CORE_DATABASE_URL'].strip())
        tenant_url = _normalize_postgres_url(os.environ['TENANT_DATABASE_URL'].strip())
        
        app.config['SQLALCHEMY_DATABASE_URI'] = core_url
        app.config['SQLALCHEMY_BINDS'] = {'tenant': tenant_url}
        
        # ─────────────────────────────────────────────────────────────
        # CONFIGURE SENTRY
        # ─────────────────────────────────────────────────────────────
        if app.config.get('SENTRY_DSN'):
            try:
                import sentry_sdk
                from sentry_sdk.integrations.flask import FlaskIntegration
                sentry_sdk.init(
                    dsn=app.config['SENTRY_DSN'],
                    integrations=[FlaskIntegration()],
                    traces_sample_rate=0.1,
                    environment='production',
                )
            except ImportError:
                app.logger.warning('sentry-sdk not installed; skipping Sentry')
        
        # ─────────────────────────────────────────────────────────────
        # PRODUCTION LOGGING
        # ─────────────────────────────────────────────────────────────
        handler = logging.StreamHandler()
        handler.setLevel(logging.WARNING)
        formatter = logging.Formatter(BaseConfig.LOG_FORMAT)
        handler.setFormatter(formatter)
        app.logger.addHandler(handler)


class TestingConfig(BaseConfig):
    """Testing environment configuration."""
    
    TESTING = True
    DEBUG = False
    
    # In-memory SQLite for tests
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_BINDS = {'tenant': 'sqlite:///:memory:'}
    SQLALCHEMY_ENGINE_OPTIONS = {}
    
    # Disable CSRF and rate limiting for tests
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SESSION_COOKIE_SECURE = False
    
    # Null cache for tests
    CACHE_TYPE = 'NullCache'
    
    # Use test secrets
    SECRET_KEY = 'test-secret-key-not-for-production'
    FERNET_KEY = b'test-fernet-key-not-for-production-_-1234567890ab'


# ─────────────────────────────────────────────────────────────────────────
# CONFIGURATION REGISTRY
# ─────────────────────────────────────────────────────────────────────────
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}


def get_config(env=None):
    """Get configuration object by environment name."""
    if env is None:
        env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
