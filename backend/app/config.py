"""Application configuration via environment variables (.env supported)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "AI Event Radar"
    ENV: str = "dev"

    # Database: SQLite for dev, PostgreSQL via DATABASE_URL for production
    DATABASE_URL: str = "sqlite:///./data/radar.db"

    # Auth
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRE_MINUTES: int = 60 * 12
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"  # MUST be overridden via .env in production

    # Scheduler
    SCHEDULER_ENABLED: bool = True
    DEFAULT_FREQUENCY_MINUTES: int = 60
    CRAWL_TIMEOUT_SECONDS: int = 30
    MAX_CONCURRENT_CRAWLS: int = 4
    MAX_RETRY: int = 3
    USER_AGENT: str = "AIEventRadar/1.0 (+https://example.com/bot; respects robots.txt)"

    # AI extraction (optional; OpenAI-compatible API)
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = ""

    # Search engine discovery (optional)
    SEARCH_PROVIDER: str = ""  # serper | tavily | bing | ""
    SEARCH_API_KEY: str = ""

    # Notification defaults
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""


settings = Settings()
