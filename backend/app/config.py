"""Application configuration via environment variables (.env supported).

P0 hardening:
- Production (ENV=production) refuses to start with default/weak secrets.
- No secret ever has a "safe production fallback" default.
- ALLOWED_ORIGINS must be explicit in production (no wildcard + credentials).
"""
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"
DEFAULT_ADMIN_PASSWORD = "admin123"
DEFAULT_PG_PASSWORDS = {"密码", "password", "change-me", "radar", "postgres"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "AI Event Radar"
    ENV: str = "dev"

    # Process role: "api" (web only, no scheduler) | "worker" (scheduler) | "all" (dev single process)
    ROLE: str = "all"

    # Database: SQLite for dev, PostgreSQL via DATABASE_URL for production
    DATABASE_URL: str = "sqlite:///./data/radar.db"

    # Auth
    JWT_SECRET: str = DEFAULT_JWT_SECRET
    JWT_EXPIRE_MINUTES: int = 60 * 12
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = DEFAULT_ADMIN_PASSWORD
    # Optional: enables a one-step secure password reset for the admin account
    ADMIN_RESET_TOKEN: str = ""

    # Scheduler (only meaningful when ROLE in (worker, all))
    SCHEDULER_ENABLED: bool = True
    DEFAULT_FREQUENCY_MINUTES: int = 60
    CRAWL_TIMEOUT_SECONDS: int = 30
    MAX_CONCURRENT_CRAWLS: int = 4
    MAX_RETRY: int = 3
    # heartbeat TTL of the DB scheduler lock (seconds); stale lock can be taken over
    SCHEDULER_LOCK_TTL: int = 120
    USER_AGENT: str = "AIEventRadar/1.0 (+https://your-site.example.com/bot; respects robots.txt)"

    # CORS: comma-separated allowlist. Empty in dev => common local origins.
    ALLOWED_ORIGINS: str = ""

    # Crawl safety limits
    MAX_REDIRECTS: int = 3
    MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024  # 5 MB per response

    # AI extraction (optional; OpenAI-compatible API)
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = ""

    # Search engine discovery (optional)
    SEARCH_PROVIDER: str = ""  # serper | tavily | bing | ""
    SEARCH_API_KEY: str = ""

    # Notification defaults (never logged)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""


def is_production(env: str) -> bool:
    return (env or "").lower() in ("production", "prod")


def validate_production_config(s: "Settings") -> None:
    """Refuse to boot production with unsafe defaults. Raises SystemExit with clear message."""
    if not is_production(s.ENV):
        return
    problems: list[str] = []
    if len(s.JWT_SECRET) < 32 or s.JWT_SECRET == DEFAULT_JWT_SECRET:
        problems.append(
            "JWT_SECRET 仍为默认值或长度不足 32 位。请生成随机密钥，例如：\n"
            "  python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
    if s.ADMIN_PASSWORD == DEFAULT_ADMIN_PASSWORD or len(s.ADMIN_PASSWORD) < 12:
        problems.append("ADMIN_PASSWORD 为默认值或长度不足 12 位，生产环境必须使用强密码")
    if s.ROLE not in ("api", "worker", "all"):
        problems.append("ROLE 必须是 api / worker / all")
    url = s.DATABASE_URL
    if url.startswith("sqlite"):
        problems.append("生产环境必须使用 PostgreSQL（DATABASE_URL=postgresql+psycopg://…）")
    else:
        if ":" in url.split("//", 1)[-1] and "@" in url:
            raw = url.split("//", 1)[-1].split(":", 1)[1].rsplit("@", 1)[0]
            from urllib.parse import unquote
            decoded = unquote(raw)
            if len(raw) < 16 or decoded.lower() in DEFAULT_PG_PASSWORDS or decoded.isdigit():
                problems.append("DATABASE_URL 中的数据库密码过短或为常见默认值")
    if s.ALLOWED_ORIGINS.strip() and "*" in s.ALLOWED_ORIGINS:
        problems.append("生产环境 ALLOWED_ORIGINS 不允许使用通配符 *")
    if problems:
        raise SystemExit(
            "⛔ 生产环境配置校验失败，拒绝启动：\n- " + "\n- ".join(problems))


def encode_db_url(s: "Settings") -> str:
    """Normalize driver and percent-encode credentials so special chars don't break the URL."""
    url = s.DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+psycopg://"):
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            if ":" in creds:
                user, pwd = creds.split(":", 1)
                url = f"{scheme}://{user}:{quote_plus(pwd)}@{host}"
    return url


settings = Settings()
