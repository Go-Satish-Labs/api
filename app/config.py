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
    # PostgreSQL belongs in .env. SQLite remains available only for local tests.
    DATABASE_URL: str = "sqlite:///./analytics_app.db"
    STORAGE_DIR: str = "./storage_data"
    CORS_ORIGINS: str = "http://localhost:5173"

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
    ALLOWED_EXTENSIONS: tuple = (".csv", ".xlsx", ".xls")
    MAX_UPLOAD_MB: int = 25


settings = Settings()
