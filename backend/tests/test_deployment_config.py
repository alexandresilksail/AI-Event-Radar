"""Production deployment-config consistency audit (release audit stage 2).

This is a STATIC cross-file consistency check — it does not start Docker and
does not talk to a database. What it DOES verify, concretely:

1. Every `Settings` field that matters in production is documented in
   `.env.example` (an undocumented variable is an operator trap: the
   deployment silently uses the code default).
2. `.env.example` contains no leftover placeholder that would pass the
   production validator (e.g. a default JWT secret).
3. `docker-compose.prod.yml` wires the startup order correctly:
   db -> migrate -> api/worker -> web, with real health conditions.
4. The database and the API are NOT published to the host; only the web
   (nginx) container exposes a port.
5. `DATABASE_URL` in compose is built from DB_USER/DB_PASSWORD/DB_NAME and
   does not hard-code a password.
6. The Dockerfiles do not run migrations at container start.
7. `ALLOWED_ORIGINS` is required in production and the wildcard is rejected
   (exercised through the real validator, not by reading the source).

Everything Docker- or TLS-related that cannot be executed here is reported
as NOT VERIFIED by the audit document, not silently assumed.
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

# Settings that MUST be documented in .env.example for production use.
# (A variable that is documented but optional may be left blank.)
REQUIRED_IN_ENV_EXAMPLE = [
    "ENV", "ROLE", "DATABASE_URL", "AUTO_MIGRATE", "JWT_SECRET",
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "ALLOWED_ORIGINS", "SCHEDULER_ENABLED",
    "DEFAULT_FREQUENCY_MINUTES", "CRAWL_TIMEOUT_SECONDS", "MAX_CONCURRENT_CRAWLS",
    "MAX_RETRY", "SCHEDULER_LOCK_TTL", "WORKER_HEALTH_PORT", "MAX_REDIRECTS",
    "MAX_RESPONSE_BYTES", "ALLOWED_PORTS", "USER_AGENT",
    "DB_USER", "DB_PASSWORD", "DB_NAME",
]


@pytest.fixture(scope="module")
def env_example():
    return (REPO / ".env.example").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose():
    return (REPO / "docker-compose.prod.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerfile_api():
    return (REPO / "Dockerfile.api").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nginx_prod():
    return (REPO / "nginx.prod.conf").read_text(encoding="utf-8")


class TestEnvExampleCompleteness:
    def test_every_production_variable_is_documented(self, env_example):
        missing = [k for k in REQUIRED_IN_ENV_EXAMPLE
                   if not re.search(rf"^\s*{re.escape(k)}\s*=", env_example, re.M)]
        assert not missing, f"未在 .env.example 中说明的部署变量: {missing}"

    def test_new_trusted_internal_hosts_is_documented(self, env_example):
        """The SSRF trusted-host escape hatch must be discoverable."""
        assert re.search(r"^\s*TRUSTED_INTERNAL_HOSTS\s*=", env_example, re.M), (
            "TRUSTED_INTERNAL_HOSTS 未在 .env.example 中说明")

    def test_no_default_secrets_shipped(self, env_example):
        """Placeholders must not be values the production validator accepts."""
        from app.config import DEFAULT_JWT_SECRET, DEFAULT_ADMIN_PASSWORD
        assert DEFAULT_JWT_SECRET not in env_example
        assert DEFAULT_ADMIN_PASSWORD not in env_example

    def test_example_declares_production_env(self, env_example):
        assert re.search(r"^\s*ENV\s*=\s*production\s*$", env_example, re.M)

    def test_example_requires_explicit_origins(self, env_example):
        m = re.search(r"^\s*ALLOWED_ORIGINS\s*=\s*(.*)$", env_example, re.M)
        assert m, "ALLOWED_ORIGINS 未说明"
        value = m.group(1).strip()
        assert value and "*" not in value, \
            "ALLOWED_ORIGINS 示例不得为空或使用通配符"

    def test_example_documents_auto_migrate_off(self, env_example):
        assert re.search(r"^\s*AUTO_MIGRATE\s*=\s*false\s*$", env_example, re.M), (
            "生产示例必须保持 AUTO_MIGRATE=false（迁移由独立 migrate 服务负责）")


class TestComposeStartupOrder:
    def test_migrate_service_exists(self, compose):
        assert re.search(r"^\s{2}migrate:", compose, re.M)

    def test_migrate_is_one_shot(self, compose):
        block = _service_block(compose, "migrate")
        assert re.search(r'restart:\s*"?no"?', block), \
            "migrate 服务必须是一次性的 (restart: no)"

    def test_api_waits_for_migrate_completion(self, compose):
        block = _service_block(compose, "api")
        assert re.search(r"migrate:\s*\{\s*condition:\s*service_completed_successfully", block), \
            "api 必须等待 migrate 成功完成"

    def test_worker_waits_for_migrate_completion(self, compose):
        block = _service_block(compose, "worker")
        assert re.search(r"migrate:\s*\{\s*condition:\s*service_completed_successfully", block), \
            "worker 必须等待 migrate 成功完成"

    def test_api_and_worker_disable_auto_migrate(self, compose):
        for svc in ("api", "worker"):
            block = _service_block(compose, svc)
            assert re.search(r'AUTO_MIGRATE:\s*"false"', block), \
                f"{svc} 必须显式设置 AUTO_MIGRATE=false"

    def test_db_is_not_published_to_host(self, compose):
        block = _service_block(compose, "db")
        assert "ports:" not in block, "数据库端口不得发布到宿主机"

    def test_api_is_not_published_to_host(self, compose):
        block = _service_block(compose, "api")
        assert "ports:" not in block, "API 端口不得发布到宿主机（只经由 nginx）"

    def test_worker_is_not_published_to_host(self, compose):
        block = _service_block(compose, "worker")
        assert "ports:" not in block, "worker 端口不得发布到宿主机"

    def test_only_web_publishes_a_port(self, compose):
        published = re.findall(r"^\s{4}ports:\s*$", compose, re.M)
        assert len(published) == 1, "只允许 web（nginx）发布端口"
        web = _service_block(compose, "web")
        assert re.search(r"\$\{HTTP_PORT:-80\}:80", web)

    def test_web_waits_for_api_health(self, compose):
        block = _service_block(compose, "web")
        assert re.search(r"api:\s*\{\s*condition:\s*service_healthy", block)

    def test_db_url_is_composed_from_separate_vars(self, compose):
        """No hard-coded password in the compose file."""
        for svc in ("migrate", "api", "worker"):
            block = _service_block(compose, svc)
            m = re.search(r"DATABASE_URL:\s*(\S+)", block)
            assert m, f"{svc} 缺少 DATABASE_URL"
            url = m.group(1)
            assert "${DB_PASSWORD}" in url, \
                f"{svc} 的 DATABASE_URL 必须引用 ${{DB_PASSWORD}}，不得硬编码"
            assert "${DB_USER" in url and "${DB_NAME" in url

    def test_services_use_env_file(self, compose):
        for svc in ("migrate", "api", "worker"):
            block = _service_block(compose, svc)
            assert "env_file: .env" in block, f"{svc} 必须加载 .env"

    def test_db_password_is_required(self, compose):
        block = _service_block(compose, "db")
        assert ":?required" in block, "DB_PASSWORD 缺失时 compose 必须直接失败"

    def test_role_is_split_between_api_and_worker(self, compose):
        assert "ROLE: api" in _service_block(compose, "api")
        assert "ROLE: worker" in _service_block(compose, "worker")


class TestDockerfiles:
    def test_api_image_does_not_run_migrations_at_start(self, dockerfile_api):
        for line in dockerfile_api.splitlines():
            low = line.strip().lower()
            if low.startswith(("cmd ", "entrypoint ")):
                assert "alembic" not in low and "app.migrate" not in low, \
                    f"API 镜像启动命令不得执行迁移: {line.strip()}"

    def test_api_image_runs_as_non_root(self, dockerfile_api):
        assert re.search(r"^\s*USER\s+(?!root)", dockerfile_api, re.M), \
            "API 镜像必须以非 root 用户运行"

    def test_web_image_uses_nginx(self):
        web = (REPO / "Dockerfile.web").read_text(encoding="utf-8")
        assert "nginx" in web.lower()


class TestNginxRouting:
    def test_api_requests_are_proxied_to_the_api_service(self, nginx_prod):
        assert re.search(r"proxy_pass\s+http://api:8000", nginx_prod)

    def test_api_location_sets_forwarded_headers(self, nginx_prod):
        for header in ("X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
            assert header in nginx_prod, f"nginx 缺少 {header} 转发头"

    def test_client_body_and_timeouts_are_bounded(self, nginx_prod):
        assert "client_max_body_size" in nginx_prod
        assert "proxy_read_timeout" in nginx_prod

    def test_static_assets_are_cached(self, nginx_prod):
        assert re.search(r"expires\s+\S+", nginx_prod)

    def test_health_endpoint_is_not_spa_fallback(self, nginx_prod):
        """The health location must answer directly, not fall back to index.html."""
        m = re.search(r"location\s+=\s*/nginx-health[\s\S]{0,400}?\}", nginx_prod)
        assert m, "nginx 缺少 /nginx-health 专用 location"
        block = m.group(0)
        assert "index.html" not in block, \
            "/nginx-health 不得回落到 SPA index.html（会把 200 当成健康）"
        assert "return 200" in block, "/nginx-health 必须直接返回 200"


class TestProductionValidatorBehaviour:
    """Exercise the real validator, not a re-implementation of it."""

    def _settings(self, **overrides):
        from app.config import Settings
        base = dict(
            ENV="production",
            ROLE="api",
            DATABASE_URL="postgresql+psycopg://radar:Sup3rSecretPassw0rd@db:5432/radar",
            JWT_SECRET="x" * 48,
            ADMIN_PASSWORD="Sup3rSecretAdminPassw0rd",
            ALLOWED_ORIGINS="https://radar.example.com",
        )
        base.update(overrides)
        return Settings(**base)

    def test_valid_production_config_passes(self):
        from app.config import validate_production_config
        validate_production_config(self._settings())  # must not raise

    def test_default_jwt_secret_is_rejected(self):
        from app.config import DEFAULT_JWT_SECRET, validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(JWT_SECRET=DEFAULT_JWT_SECRET))
        assert "JWT_SECRET" in str(e.value)

    def test_short_jwt_secret_is_rejected(self):
        from app.config import validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(JWT_SECRET="short"))
        assert "JWT_SECRET" in str(e.value)

    def test_sqlite_in_production_is_rejected(self):
        from app.config import validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(DATABASE_URL="sqlite:///./data/x.db"))
        assert "PostgreSQL" in str(e.value)

    def test_wildcard_origins_are_rejected(self):
        from app.config import validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(ALLOWED_ORIGINS="*"))
        assert "ALLOWED_ORIGINS" in str(e.value)

    def test_missing_origins_are_rejected(self):
        from app.config import validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(ALLOWED_ORIGINS=""))
        assert "ALLOWED_ORIGINS" in str(e.value)

    def test_short_db_password_is_rejected(self):
        from app.config import validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(
                DATABASE_URL="postgresql+psycopg://radar:short@db:5432/radar"))
        assert "密码" in str(e.value)

    def test_invalid_role_is_rejected(self):
        from app.config import validate_production_config
        with pytest.raises(SystemExit) as e:
            validate_production_config(self._settings(ROLE="frontend"))
        assert "ROLE" in str(e.value)

    def test_dev_config_is_not_validated(self):
        from app.config import validate_production_config
        validate_production_config(self._settings(
            ENV="dev", JWT_SECRET="x", ADMIN_PASSWORD="y",
            DATABASE_URL="sqlite:///./data/x.db", ALLOWED_ORIGINS=""))


class TestDatabaseUrlEncoding:
    def test_raw_special_chars_are_encoded_exactly_once(self):
        from app.config import Settings, encode_db_url
        s = Settings(DATABASE_URL="postgresql+psycopg://u:p@ss:w/rd@db:5432/n")
        url = encode_db_url(s)
        assert "p%40ss%3Aw%2Frd" in url
        assert "%2540" not in url, "不得双重编码"

    def test_idempotent_on_own_output(self):
        from app.config import Settings, encode_db_url
        s = Settings(DATABASE_URL="postgresql+psycopg://u:p@ss@db:5432/n")
        once = encode_db_url(s)
        twice = encode_db_url(Settings(DATABASE_URL=once))
        assert once == twice

    def test_driver_is_normalised(self):
        from app.config import Settings, encode_db_url
        for raw in ("postgres://u:p@db:5432/n", "postgresql://u:p@db:5432/n"):
            assert encode_db_url(Settings(DATABASE_URL=raw)).startswith(
                "postgresql+psycopg://")

    def test_sqlite_passes_through(self):
        from app.config import Settings, encode_db_url
        raw = "sqlite:///./data/radar.db"
        assert encode_db_url(Settings(DATABASE_URL=raw)) == raw


def _service_block(compose_text: str, name: str) -> str:
    """Extract one service block from the compose YAML (indentation based)."""
    lines = compose_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(name)}:\s*$", line):
            start = i
            break
    assert start is not None, f"compose 中找不到服务 {name}"
    out = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith("    "):
            break
        out.append(line)
    return "\n".join(out)
