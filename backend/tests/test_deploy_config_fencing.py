"""Regression tests for the production deployment configuration (finding #4).

The defect
----------
`docker-compose.prod.yml` assembled the connection URL by string
concatenation:

    DATABASE_URL: postgresql+psycopg://${DB_USER:-radar}:${DB_PASSWORD}@db:5432/${DB_NAME:-radar}

and the application then used that URL as-is. A password containing
`@ : / % + #` or a space breaks it: the '@' terminates the userinfo early,
the '/' starts the path, the '#' starts a fragment, and the space breaks the
whole thing. The migrate, api and worker services could therefore end up
pointing at *different* databases (or at none), and a migration could succeed
against a URL that the API cannot use.

The fix
-------
Two layers, both required:

1. The DISCRETE parts (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD) are
   passed through to every service and are the authoritative source.
   `app/config.py::encode_db_url()` builds the effective URL from them and
   percent-encodes the password exactly once — no guessing, no
   double-encoding, no wrong decoding.
2. `DATABASE_URL` is still exported (tools read it directly, and the
   deployment contract expects it), but it is *shadowed* by the discrete
   parts inside the application. It references the same variables, so it
   never contains a hard-coded secret.

Both layers are asserted below: the compose wiring (this file) and the
application behaviour (test_db_credentials.py).
"""
import os
import re

import pytest

import yaml  # type: ignore

# The compose file and the scripts live in the REPOSITORY ROOT, while pytest
# runs with cwd=backend/. Resolve them from the test file location so the
# tests do not depend on the working directory.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMPOSE_PATH = os.path.join(REPO_ROOT, "docker-compose.prod.yml")
DEPLOY_SH = os.path.join(REPO_ROOT, "scripts", "deploy.sh")
ENV_EXAMPLE = os.path.join(REPO_ROOT, ".env.example")


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def compose_text():
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# 1. No secret is hardcoded; the exported URL references the variables
# --------------------------------------------------------------------------
class TestNoHardcodedSecret:
    def test_exported_url_references_the_password_variable(self, compose):
        """The exported DATABASE_URL must interpolate ${DB_PASSWORD} — never a
        literal secret, and never a value that is not the configured one."""
        for svc in ("migrate", "api", "worker"):
            url = compose["services"][svc]["environment"].get("DATABASE_URL", "")
            assert url, f"{svc} must export DATABASE_URL"
            assert "${DB_PASSWORD}" in url, (
                f"{svc} 的 DATABASE_URL 必须引用 ${{DB_PASSWORD}}，不得硬编码")
            assert "${DB_USER" in url and "${DB_NAME" in url

    def test_no_literal_password_sits_next_to_the_url(self, compose_text):
        """A password literal in the compose file would be a committed secret.

        Only `${...}` variable references are allowed on a DATABASE_URL line.
        """
        for line in compose_text.splitlines():
            if "DATABASE_URL:" not in line:
                continue
            value = line.split("DATABASE_URL:", 1)[1]
            # strip the variable references, what remains must be empty
            residue = re.sub(r"\$\{[^}]*\}", "", value)
            residue = residue.replace(
                "postgresql+psycopg://", "").replace("@db:5432/", "")
            residue = re.sub(r"^[\s:'\"]+", "", residue)
            assert residue in (":", ""), (
                f"a literal value is spliced into the DATABASE_URL: {line.strip()!r}")

    def test_the_db_parts_are_the_authoritative_source(self, compose):
        """Every DB-consuming service must receive the discrete parts, so the
        application can build a correctly encoded URL regardless of what the
        interpolated URL looks like."""
        for svc in ("migrate", "api", "worker"):
            env = compose["services"][svc]["environment"]
            for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
                assert key in env, f"{svc} 缺少 {key}"

    def test_the_url_is_shadowed_by_the_parts(self):
        """The application must ignore the interpolated URL when the discrete
        parts are present — that is what makes the special-character password
        safe even though compose splices it into a URI."""
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DATABASE_URL="postgresql+psycopg://radar:P@ss:w/rd@db:5432/radar",
            DB_USER="radar", DB_PASSWORD="P@ss:w/rd",
            DB_HOST="db", DB_PORT=5432, DB_NAME="radar",
        )
        url = encode_db_url(s)
        assert "P%40ss%3Aw%2Frd" in url, (
            f"the effective URL must be built from the parts, got {url}")
        assert "@db:5432" in url


# --------------------------------------------------------------------------
# 2. All three DB-consuming services get identical credentials
# --------------------------------------------------------------------------
class TestConsistentCredentials:
    @pytest.mark.parametrize("service", ["migrate", "api", "worker"])
    def test_service_receives_the_db_parts(self, compose, service):
        env = compose["services"][service]["environment"]
        assert env.get("DB_HOST") == "db"
        assert env.get("DB_PORT") == "5432"
        assert "DB_NAME" in env
        assert "DB_USER" in env
        assert "DB_PASSWORD" in env

    def test_migrate_api_and_worker_use_the_same_values(self, compose):
        envs = [compose["services"][s]["environment"]
                for s in ("migrate", "api", "worker")]
        keys = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
        for k in keys:
            values = {e.get(k) for e in envs}
            assert len(values) == 1, (
                f"{k} differs across services: {values} — migrate, api and "
                f"worker must connect to the SAME database")

    def test_db_password_is_required(self, compose_text):
        """An unset DB_PASSWORD must fail loudly, not silently default."""
        # Anchor on the YAML key form — a bare search would also match the
        # "${DB_PASSWORD:?required}" reference *inside* a value.
        m = re.search(r"^\s*DB_PASSWORD:\s*(\S+)", compose_text, re.M)
        assert m, "DB_PASSWORD must be passed to the services"
        assert ":?" in m.group(1), (
            "DB_PASSWORD must use the `:?required` form so compose refuses "
            "to start without it")


# --------------------------------------------------------------------------
# 3. Startup ordering
# --------------------------------------------------------------------------
class TestStartupOrder:
    def test_api_waits_for_a_successful_migration(self, compose):
        dep = compose["services"]["api"]["depends_on"]
        assert dep["migrate"]["condition"] == "service_completed_successfully", (
            "the API must only start after the migration EXITED SUCCESSFULLY")

    def test_worker_waits_for_a_successful_migration(self, compose):
        dep = compose["services"]["worker"]["depends_on"]
        assert dep["migrate"]["condition"] == "service_completed_successfully", (
            "the worker must only start after the migration EXITED SUCCESSFULLY")

    def test_api_and_worker_wait_for_the_database(self, compose):
        for s in ("api", "worker", "migrate"):
            dep = compose["services"][s]["depends_on"]
            assert dep["db"]["condition"] == "service_healthy", (
                f"{s} must wait for a healthy database")

    def test_migration_is_a_one_shot_service(self, compose):
        mig = compose["services"]["migrate"]
        assert mig.get("restart") == "no", "migrate must not be restarted"
        assert mig["command"] == ["python", "-m", "app.migrate"]

    def test_api_and_worker_do_not_auto_migrate(self, compose):
        """AUTO_MIGRATE=true in a replica would race the migration service."""
        for s in ("api", "worker"):
            env = compose["services"][s]["environment"]
            assert str(env.get("AUTO_MIGRATE")).lower() == "false", (
                f"{s} must not run migrations itself")

    def test_web_waits_for_a_healthy_api(self, compose):
        dep = compose["services"]["web"]["depends_on"]
        assert dep["api"]["condition"] == "service_healthy"

    def test_the_comment_documents_the_real_order(self, compose_text):
        """The header comment must describe the order compose actually
        enforces — an outdated comment misleads operators during incidents."""
        head = compose_text.split("services:")[0]
        assert "service_completed_successfully" in head or "exit 0" in head, (
            "the compose header must state that a failed migration blocks "
            "api/worker")
        assert "migrate" in head and "worker" in head


# --------------------------------------------------------------------------
# 4. Worker health is actually checked
# --------------------------------------------------------------------------
class TestWorkerHealth:
    def test_worker_has_a_healthcheck(self, compose):
        hc = compose["services"]["worker"].get("healthcheck")
        assert hc, "the worker needs a healthcheck so a failed start is visible"
        assert "8001" in " ".join(hc["test"]), (
            "the worker healthcheck must probe the worker's own health port")

    def test_api_healthcheck_probes_the_api(self, compose):
        hc = compose["services"]["api"]["healthcheck"]
        assert "8000" in " ".join(hc["test"])

    def test_deploy_script_requires_the_worker_to_be_healthy(self):
        """`docker compose ps worker | grep -q Up` is NOT enough: a container
        can be Up while its healthcheck is failing. The deploy script must
        wait for `healthy` and must fail on `unhealthy`/`exited`."""
        with open(DEPLOY_SH, encoding="utf-8") as f:
            script = f.read()
        assert "healthy" in script, (
            "deploy.sh must check the worker's HEALTH, not just that it is Up")
        assert "unhealthy" in script, (
            "deploy.sh must detect an unhealthy worker and fail the deploy")
        assert "exited" in script, (
            "deploy.sh must detect an exited worker and fail the deploy")

    def test_deploy_script_checks_the_database_password_strength(self):
        with open(DEPLOY_SH, encoding="utf-8") as f:
            script = f.read()
        assert "DB_PASSWORD" in script
        assert "16" in script, "deploy.sh must enforce a minimum DB_PASSWORD length"


# --------------------------------------------------------------------------
# 5. The .env.example must point operators at the discrete parts
# --------------------------------------------------------------------------
class TestEnvExample:
    def test_env_example_defines_the_discrete_parts(self):
        with open(ENV_EXAMPLE, encoding="utf-8") as f:
            text = f.read()
        for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
            assert re.search(rf"^{key}=", text, re.M), \
                f".env.example must define {key}"

    def test_env_example_documents_the_password_encoding_flag(self):
        """DB_PASSWORD_ENCODED decides whether the value is literal or
        already percent-encoded — it must be discoverable."""
        with open(ENV_EXAMPLE, encoding="utf-8") as f:
            text = f.read()
        assert re.search(r"^DB_PASSWORD_ENCODED=", text, re.M), (
            ".env.example must define DB_PASSWORD_ENCODED")

    def test_env_example_password_placeholder_is_not_chinese_prose(self):
        """A Chinese placeholder like 请替换为强密码 would be treated as a
        literal password and encoded into the URL."""
        with open(ENV_EXAMPLE, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^DB_PASSWORD=(.*)$", text, re.M)
        assert m, ".env.example must define DB_PASSWORD"
        value = m.group(1).strip()
        assert value and not re.search(r"[\u4e00-\u9fff]", value), (
            f"the DB_PASSWORD placeholder must be ASCII, got {value!r}")
        assert value.upper().startswith("CHANGE_ME"), (
            "the placeholder must be obviously invalid so it cannot be used "
            "by accident")
