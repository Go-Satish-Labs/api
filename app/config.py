"""
Central configuration.

Everything that the blueprint says must be "configurable from the backend
rather than hard-coded in the client" lives here, sourced from environment
variables (.env). Nothing here is duplicated in the frontend.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    SECRET_KEY: str = "dev-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    # PostgreSQL belongs in .env (never hard-code credentials here - this file is
    # committed). SQLite stays as the zero-config default so a fresh clone works.
    DATABASE_URL: str = "sqlite:///./analytics_app.db"
    # Postgres resilience: fail fast (seconds, not minutes) when the DB is away.
    DB_CONNECT_TIMEOUT_SECONDS: int = 8
    DB_POOL_TIMEOUT_SECONDS: int = 8
    DB_POOL_RECYCLE_SECONDS: int = 300
    # Local-dev safety net: if DATABASE_URL is unreachable, serve from a local
    # SQLite file instead of answering every request with 503. Off by default -
    # opting in from production would silently write rows to the wrong database,
    # so Brain/.env turns it on while the Supabase DSN is being repaired.
    DB_SQLITE_FALLBACK: bool = False
    DB_SQLITE_FALLBACK_URL: str = "sqlite:///./analytics_app.db"
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    # Server-side storage writes must bypass Storage RLS, so they use the
    # service_role key (never the anon key). Never expose this to the client.
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    STORAGE_DIR: str = "datasets"
    CORS_ORIGINS: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "https://analytrix-mocha.vercel.app"
    )

    # Firebase ID tokens are verified by the API before workspace-scoped queries run.
    # AUTH_MODE=local exists only for tests and offline development.
    AUTH_MODE: str = "firebase"
    FIREBASE_PROJECT_ID: str = ""

    # Free plan limits
    FREE_MAX_DATASETS: int = 3
    FREE_MAX_STORAGE_MB: int = 50
    FREE_MAX_DASHBOARDS: int = 3
    FREE_MAX_AI_QUESTIONS_PER_MONTH: int = 20

    # Premium plan limits
    PREMIUM_MAX_DATASETS: int = 100
    PREMIUM_MAX_STORAGE_MB: int = 2000
    PREMIUM_MAX_DASHBOARDS: int = 50
    PREMIUM_MAX_AI_QUESTIONS_PER_MONTH: int = 1000
    PREMIUM_PRICE_USD: int = 10

    # Payments
    PAYMENT_PROVIDER: str = "mock"  # "mock" (free, no account) or "razorpay" (real)
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # AI
    LLM_PROVIDER: str = "none"  # "none" | "openai" | "anthropic"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Upload validation
    ALLOWED_EXTENSIONS: tuple = (".csv", ".xlsx", ".xls", ".txt", ".json", ".html", ".htm", ".xml")
    MAX_UPLOAD_MB: int = 25

    # How long a shareable dashboard link stays live. Shared reports hold
    # aggregates only, so they can outlive the source file - but not forever.
    SHARE_LINK_EXPIRY_HOURS: int = 168


settings = Settings()
