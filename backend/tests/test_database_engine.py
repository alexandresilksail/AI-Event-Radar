"""Regression test for the production engine-construction defect (P0).

The defect
----------
`app/database.py` merged the PostgreSQL engine options into `create_engine()`
with `**` while ALSO passing `connect_args` and `pool_pre_ping` as explicit
keyword arguments:

    engine = create_engine(DB_URL, connect_args=_connect_args,
                           pool_pre_ping=True, **_pool_kwargs)

On PostgreSQL, `_pool_kwargs` contains `pool_pre_ping` and `connect_args`
too, so the call raised

    TypeError: create_engine() got multiple values for keyword argument
    'pool_pre_ping'

at IMPORT time. The API and the worker therefore could not start in
production at all. Every test in the suite ran on SQLite, where the
PostgreSQL branch is not taken, so the defect was invisible.

These checks run in a SUBPROCESS on purpose: re-importing `app.database`
in-process would replace `app.config.settings` for the rest of the session
and leak into unrelated tests. A subprocess also exercises the real import
path exactly as the container does it.
"""
import json
import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r'''
import json
from app.database import DB_URL, engine

pool = engine.pool
info = {
    "db_url_scheme": DB_URL.split(":", 1)[0],
    "pool_class": type(pool).__name__,
    "maxsize": getattr(getattr(pool, "_pool", None), "maxsize", None),
    "max_overflow": getattr(pool, "_max_overflow", None),
    "recycle": getattr(pool, "_recycle", None),
    "pre_ping": getattr(pool, "_pre_ping", None),
}
print("ENGINE_PROBE " + json.dumps(info))
'''


def _probe(url: str, extra_env=None, script: str = PROBE):
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ENV"] = "dev"
    env["ROLE"] = "api"
    env["AUTO_MIGRATE"] = "false"
    env["JWT_SECRET"] = "x" * 48
    env["ADMIN_PASSWORD"] = "a-strong-admin-password"
    env["ALLOWED_ORIGINS"] = "http://localhost"
    if extra_env:
        env.update(extra_env)
    p = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=180)
    info = None
    for line in p.stdout.splitlines():
        if line.startswith("ENGINE_PROBE "):
            info = json.loads(line[len("ENGINE_PROBE "):])
    return p.returncode, info, p.stdout, p.stderr


class TestEngineConstruction:
    def test_postgres_engine_is_created_without_duplicate_kwargs(self):
        """The PostgreSQL branch must not collide with the shared kwargs."""
        rc, info, out, err = _probe(
            "postgresql+psycopg://radar:Str0ng%21Pass@db:5432/radar")
        assert rc == 0, (
            f"the engine could not be created for a PostgreSQL URL:\n"
            f"stdout: {out}\nstderr: {err[-2000:]}")
        assert info is not None
        assert info["db_url_scheme"] == "postgresql+psycopg"

    def test_postgres_engine_has_the_expected_pool_options(self):
        rc, info, out, err = _probe(
            "postgresql+psycopg://radar:Str0ng%21Pass@db:5432/radar")
        assert rc == 0, err[-2000:]
        assert info["pool_class"] == "QueuePool", (
            "PostgreSQL must use a real connection pool, not NullPool")
        assert info["maxsize"] == 10, "pool_size must be applied on PostgreSQL"
        assert info["max_overflow"] == 20, "max_overflow must be applied"
        assert info["recycle"] == 1800, "pool_recycle must be applied"
        assert info["pre_ping"] is True, "pool_pre_ping must be enabled"

    def test_sqlite_engine_is_created(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="dbengine-")
        rc, info, out, err = _probe(f"sqlite:///{tmp}/t.db")
        assert rc == 0, err[-2000:]
        assert info["db_url_scheme"] == "sqlite"

    def test_the_module_source_has_no_duplicate_keyword(self):
        """Static guard: the old call shape must not come back."""
        path = os.path.join(BACKEND, "app", "database.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert "pool_pre_ping=True" not in src, (
            "create_engine() must not receive pool_pre_ping both explicitly "
            "and through **kwargs")
        assert "connect_args=_connect_args" not in src, (
            "create_engine() must not receive connect_args both explicitly "
            "and through **kwargs")


class TestApplicationStartsWithPostgresUrl:
    """The application must import cleanly with a PostgreSQL URL."""

    def test_importing_app_main_with_postgres_url_succeeds(self):
        script = ("import app.main; print('IMPORT_OK')")
        rc, info, out, err = _probe(
            "postgresql+psycopg://radar:Str0ng%21Pass@db:5432/radar",
            script=script)
        assert rc == 0, (
            f"importing the app with a PostgreSQL URL failed:\n"
            f"stdout: {out}\nstderr: {err[-2000:]}")
        assert "IMPORT_OK" in out

    def test_the_api_module_imports_with_a_special_character_password(self):
        script = ("import app.main; print('IMPORT_OK')")
        rc, info, out, err = _probe(
            "postgresql+psycopg://radar:P%40ss%3Aw%2Frd%2B%23@db:5432/radar",
            script=script)
        assert rc == 0, (
            f"a special-character password broke the import:\n{err[-2000:]}")
        assert "IMPORT_OK" in out
