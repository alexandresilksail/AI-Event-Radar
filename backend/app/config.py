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

# The characters `urllib.parse.quote(value, safe="")` never escapes.
# Anything else MUST be percent-encoded inside a URL userinfo component.
_ALWAYS_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "abcdefghijklmnopqrstuvwxyz" "0123456789" "_.-~"
)
_PCT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")


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


def _credential_is_encoded(value: str) -> bool:
    """Decide whether a credential already carries percent-escapes.

    The rule
    --------
    `quote(value, safe="")` escapes everything except ``A-Za-z0-9_.-~``.
    So a *correctly encoded* credential necessarily contains at least one
    ``%XX`` escape whose decoded character is outside that always-safe set
    (``%40`` -> '@', ``%3A`` -> ':', ``%25`` -> '%', ...).

    A raw password that merely *looks* encoded (``pa%41ss``, where ``%41``
    decodes to the always-safe 'A') carries no such escape and is therefore
    correctly treated as raw.

    This is what separates the two cases a naive "%XX present?" test cannot:
    ``pa%41ss`` (raw) must become ``pa%2541ss`` while ``pa%40ss`` (already
    encoded, i.e. ``pa@ss``) must be left untouched.

    Residual ambiguity
    ------------------
    A raw password that literally contains ``%40``-style text whose decoded
    character needs escaping (``pa%40ss``) is indistinguishable from the
    encoded form. No string inspection can resolve it. Operators with such a
    password must use the explicit ``DB_PASSWORD`` setting, where the encoding
    state is stated instead of guessed.
    """
    if not value:
        return False
    for m in _PCT_ESCAPE.finditer(value):
        if chr(int(m.group(1), 16)) not in _ALWAYS_SAFE:
            return True
    return False


def _encode_credential(value: str, already_encoded: bool = False) -> str:
    """Percent-encode a credential exactly once."""
    if already_encoded:
        return value
    return quote(value, safe="")


def _decode_credential(raw: str | None) -> str:
    """Recover the literal value of a credential for *validation* only.

    Never used to build a connection string — see `encode_db_url`.
    """
    if not raw:
        return ""
    return unquote(raw) if _credential_is_encoded(raw) else raw


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "AI Event Radar"
    ENV: str = "dev"

    # Process role: "api" (web only, no scheduler) | "worker" (scheduler) | "all" (dev single process)
    ROLE: str = "all"

    # Database: SQLite for dev, PostgreSQL via DATABASE_URL for production
    DATABASE_URL: str = "sqlite:///./data/radar.db"

    # Explicit PostgreSQL connection parts. When DB_USER + DB_HOST + DB_NAME
    # are all set, encode_db_url() builds the URL from these instead of
    # DATABASE_URL, and DB_PASSWORD is treated as the LITERAL password and
    # percent-encoded exactly once. This is the unambiguous path: no guessing
    # about whether a '%' in the password is an escape or a literal.
    #
    # DB_PASSWORD_ENCODED=true means "DB_PASSWORD is already percent-encoded"
    # — only for operators who deliberately store an encoded value.
    DB_HOST: str = ""
    DB_PORT: int = 5432
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_PASSWORD_ENCODED: bool = False

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

    # Operator-configured endpoints allowed to resolve to private networks.
    # Comma-separated hostnames. SMTP_HOST and the AI_BASE_URL host are added
    # automatically. These come from deployment config (never user input), so
    # they are exempt from the global DNS guard; user-supplied crawl targets
    # are still validated per-request by validate_public_url().
    TRUSTED_INTERNAL_HOSTS: str = ""


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


def effective_db_password(s: "Settings") -> str:
    """The literal database password, whatever the configuration source.

    Used for strength validation. When the password comes from DB_PASSWORD it
    is returned verbatim (that is the literal value). When it is embedded in
    DATABASE_URL it is already encoded, so it is decoded once for the length
    / default-value checks.
    """
    if s.DB_PASSWORD:
        return unquote(s.DB_PASSWORD) if s.DB_PASSWORD_ENCODED else s.DB_PASSWORD
    url = s.DATABASE_URL
    if url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        _, pwd_raw, _ = _split_db_credentials(url)
        return _decode_credential(pwd_raw)
    return ""


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

    explicit_parts = bool(s.DB_USER and s.DB_HOST and s.DB_NAME)
    if explicit_parts:
        # The URL is assembled from DB_* parts by encode_db_url().
        password = effective_db_password(s)
        if not password:
            problems.append("缺少数据库密码（DB_PASSWORD 未设置）")
        elif len(password) < 16 or password.lower() in DEFAULT_PG_PASSWORDS or password.isdigit():
            problems.append("DB_PASSWORD 过短或为常见默认值（至少 16 位且非纯数字）")
    else:
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
    """Build the SQLAlchemy connection URL, percent-encoding credentials exactly once.

    Two configuration sources, in priority order
    ---------------------------------------------
    1. **Explicit parts** (`DB_USER` + `DB_PASSWORD` + `DB_HOST` + `DB_NAME`)
       — the preferred path, used by `docker-compose.prod.yml`. The password
       is given as its LITERAL value and is percent-encoded exactly once
       here. `DB_PASSWORD_ENCODED=true` means "this value is already
       percent-encoded" — only for operators who deliberately store one.

    2. **`DATABASE_URL`** — the credentials embedded in the URL are encoded
       exactly once *unless* they already carry percent-escapes, in which
       case they are passed through byte-for-byte.

    Why "unless they already carry percent-escapes" is decided by *decoding*
    ------------------------------------------------------------------------
    A raw password and a percent-encoded password are indistinguishable as
    strings whenever the raw one happens to contain '%' followed by two hex
    digits. `pa%41ss` is the raw password "pa%41ss" OR the encoded form of
    "paAss" — no string inspection can tell them apart, and guessing wrong
    silently corrupts the credential (the server then rejects the login with
    a confusing "password authentication failed").

    The tie is broken by *what the escapes decode to*: `quote(value, safe="")`
    escapes everything outside ``A-Za-z0-9_.-~``, so a genuinely encoded
    credential must contain at least one escape that decodes to a character
    outside that set. `pa%41ss` decodes to 'A' (always-safe) -> raw -> it is
    encoded once and the server receives the literal "pa%41ss". `pa%40ss`
    decodes to '@' (must be escaped) -> already encoded -> left untouched.

    The single irreducible case is a raw password that literally contains
    `%40`-style text. Use the explicit `DB_PASSWORD` setting for it — there
    the encoding state is *stated*, not guessed.

    Guarantees:
      - no double encoding   (an encoded input is never quoted again)
      - no wrong decoding    (a raw input is never unquoted)
      - special characters (@ : / % + # space) survive a round trip
    """
    # ---- source 1: explicit DB_USER / DB_PASSWORD (preferred) ----
    db_user = (s.DB_USER or "").strip()
    db_password = s.DB_PASSWORD or ""
    db_host = (s.DB_HOST or "").strip()
    db_port = s.DB_PORT or 5432
    db_name = (s.DB_NAME or "").strip()

    if db_user and db_host and db_name:
        user_out = _encode_credential(db_user, _credential_is_encoded(db_user))
        pwd_out = _encode_credential(db_password, s.DB_PASSWORD_ENCODED)
        return (f"postgresql+psycopg://{user_out}:{pwd_out}"
                f"@{db_host}:{db_port}/{db_name}")

    # ---- source 2: DATABASE_URL ----
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
    user_out = _encode_credential(user, _credential_is_encoded(user))
    pwd_out = _encode_credential(pwd or "", _credential_is_encoded(pwd or ""))
    return f"{scheme}://{user_out}:{pwd_out}@{hostpart}"


settings = Settings()
