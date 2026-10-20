"""Application configuration via environment variables (.env supported).

P0 hardening:
- Production (ENV=production) refuses to start with default/weak secrets.
- No secret ever has a "safe production fallback" default.
- ALLOWED_ORIGINS must be explicit in production (no wildcard + credentials).
- Production never runs migrations automatically: a dedicated one-shot
  migrate service owns schema changes (AUTO_MIGRATE=false by default in prod).
- DATABASE_URL credentials are normalized to exactly one layer of
  percent-encoding, so passwords containing @ : / % # etc. work.
"""
import re
from urllib.parse import quote, unquote

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"
DEFAULT_ADMIN_PASSWORD = "admin123"
DEFAULT_PG_PASSWORDS = {"密码", "password", "change-me", "radar", "postgres"}

_HEX_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def _looks_encoded(value: str) -> bool:
    """Heuristic: credential already carries percent-escapes -> treat as encoded.

    Documented ambiguity: a raw password that literally contains '%' + two hex
    digits is mistaken for encoded input. Prefer relying on the docker-compose
    default, which passes raw values and lets encode_db_url encode exactly once.
    """
    return bool(_HEX_ESCAPE.search(value))


def _split_db_credentials(url: str) -> tuple[str | None, str | None, str]:
    """Split a postgres URL into (user, password, remainder) WITHOUT letting
    special characters in the password confuse the split.

    Uses the LAST '@' as the userinfo/host separator (raw '@' in passwords is
    unambiguous this way, unlike a naive split('@', 1)).
    """
    rest = url.split("://", 1)[1] if "://" in url else url
    at = rest.rfind("@")
    if at == -1:
        return None, None, rest
    userinfo, hostpart = rest[:at], rest[at + 1:]
    if ":" in userinfo:
        user, pwd = userinfo.split(":", 1)
    else:
        user, pwd = userinfo, None
    return user, pwd, hostpart


def _decode_credential(raw: str | None) -> str:
    """Best-effort decode of a credential that may be raw or encoded."""
    if not raw:
        return ""
    return unquote(raw) if _looks_encoded(raw) else raw


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "AI Event Radar"
    ENV: str = "dev"

    # Process role: "api" (web only, no scheduler) | "worker" (scheduler) | "all" (dev single process)
    ROLE: str = "all"

    # Database: SQLite for dev, PostgreSQL via DATABASE_URL for production
    DATABASE_URL: str = "sqlite:///./data/radar.db"

    # Auto-run alembic migrations at process start. Dev default: True.
    # Production: keep False — the dedicated one-shot `migrate` service owns it,
    # so API/Worker replicas never race on the same migration.
    AUTO_MIGRATE: bool | None = None

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
    # worker embedded health server port (internal only; not published in prod)
    WORKER_HEALTH_PORT: int = 8001
    USER_AGENT: str = "AIEventRadar/1.0 (+https://your-site.example.com/bot; respects robots.txt)"

    # CORS: comma-separated allowlist. Empty in dev => common local origins.
    ALLOWED_ORIGINS: str = ""

    # Crawl safety limits
    MAX_REDIRECTS: int = 3
    MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024  # 5 MB per response
    # SSRF: only these TCP ports may be crawled (comma-separated). Default 80/443.
    ALLOWED_PORTS: str = "80,443"

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


def auto_migrate_enabled(s: "Settings") -> bool:
    """Resolve the AUTO_MIGRATE tri-state.

    - unset (None): dev -> True (convenience), production -> False (single
      one-shot migrate service owns schema changes).
    - explicit True/False always wins.
    """
    if s.AUTO_MIGRATE is None:
        return not is_production(s.ENV)
    return bool(s.AUTO_MIGRATE)


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
    elif url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        _, pwd_raw, hostpart = _split_db_credentials(url)
        host = (hostpart.split("/", 1)[0].split("?", 1)[0] if hostpart else "")
        host_name = host.rsplit(":", 1)[0] if ":" in host and not host.startswith("[") else host
        if not host_name:
            problems.append("DATABASE_URL 缺少数据库主机名")
        if not pwd_raw:
            problems.append("DATABASE_URL 缺少数据库密码")
        else:
            raw = _decode_credential(pwd_raw)
            if len(raw) < 16 or raw.lower() in DEFAULT_PG_PASSWORDS or raw.isdigit():
                problems.append("DATABASE_URL 中的数据库密码过短或为常见默认值")
    else:
        problems.append("DATABASE_URL 必须是 postgresql(+psycopg):// 或 sqlite:/// 格式")
    if s.ALLOWED_ORIGINS.strip() and "*" in s.ALLOWED_ORIGINS:
        problems.append("生产环境 ALLOWED_ORIGINS 不允许使用通配符 *")
    if not s.ALLOWED_ORIGINS.strip():
        problems.append("生产环境必须显式配置 ALLOWED_ORIGINS（逗号分隔的可信来源）")
    if problems:
        raise SystemExit(
            "⛔ 生产环境配置校验失败，拒绝启动：\n- " + "\n- ".join(problems))


def encode_db_url(s: "Settings") -> str:
    """Normalize driver and percent-encode credentials exactly once.

    Idempotent: a credential that already carries percent-escapes is treated
    as encoded and left untouched (never double-encoded); a raw credential is
    encoded exactly once. Never re-quotes an already-encoded URL string
    (that caused %40 -> %2540). Non-PostgreSQL URLs pass through untouched.
    """
    url = s.DATABASE_URL
    if url.startswith("postgres://"):
        scheme, rest = "postgresql+psycopg", url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        scheme, rest = "postgresql+psycopg", url[len("postgresql://"):]
    elif url.startswith("postgresql+psycopg://"):
        scheme, rest = "postgresql+psycopg", url[len("postgresql+psycopg://"):]
    else:
        return url  # sqlite and others pass through untouched
    user, pwd, hostpart = _split_db_credentials(url)
    if user is None:
        return url
    user_out = user if _looks_encoded(user) else quote(user, safe="")
    pwd_out = pwd if (pwd is None or _looks_encoded(pwd)) else quote(pwd, safe="")
    return f"{scheme}://{user_out}:{pwd_out}@{hostpart}"


settings = Settings()
