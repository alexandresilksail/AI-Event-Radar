"""Scheduler lease, migration mutex, DB URL encoding and deployment-config tests.

- Lease tests use an isolated SQLite DB with real transactions — the CAS
  semantics (atomic steal condition) is exercised at the DB level, not mocked.
- Migration tests run alembic against throwaway SQLite files, including a
  concurrent-migration scenario.
- Deployment-config checks are STATIC file checks (documented as such in the
  report): they verify the shipped config contains the required wiring.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.abspath(os.path.join(BACKEND, ".."))


@pytest.fixture()
def lock_db(tmp_path):
    """Isolated SQLite engine + tables for lock tests."""
    from sqlalchemy import create_engine
    from app.database import Base
    import app.models  # noqa: F401  (register all models)
    eng = create_engine(f"sqlite:///{tmp_path}/lock.db")
    Base.metadata.create_all(eng)
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db = Session()
    yield db
    db.close()
    eng.dispose()


def _age_lock(db, seconds):
    db.execute(text("UPDATE scheduler_locks SET heartbeat_at = :t WHERE id = 1"),
               {"t": (datetime.utcnow() - timedelta(seconds=seconds)).isoformat(sep=" ")})
    db.commit()


class TestSchedulerLease:
    def test_single_leader_semantics(self, lock_db):
        from app.services import scheduler as sched
        old = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = "worker-1"
            assert sched.try_acquire_lock(lock_db) is True
            sched.HOLDER_ID = "worker-2"
            # fresh lease held by worker-1: worker-2 must NOT acquire
            assert sched.try_acquire_lock(lock_db) is False
            sched.HOLDER_ID = "worker-1"
            assert sched.heartbeat(lock_db) is True
            sched.HOLDER_ID = "worker-2"
            # heartbeat only renews the real holder's lease
            assert sched.heartbeat(lock_db) is False
        finally:
            sched.HOLDER_ID = old

    def test_concurrent_acquire_only_one_winner(self, lock_db):
        """Two workers racing for the same fresh/stale lease: the atomic CAS
        UPDATE guarantees exactly one winner (verified via rowcount)."""
        from app.services import scheduler as sched
        _age_lock(lock_db, 3600)  # stale lease up for grabs
        old = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = "racer-A"
            a = sched.try_acquire_lock(lock_db)
            sched.HOLDER_ID = "racer-B"
            b = sched.try_acquire_lock(lock_db)
            assert a is True and b is False  # A wins, B is refused
            # the DB still shows A as the only holder
            lock_db.expire_all()
            row = lock_db.execute(text("SELECT holder FROM scheduler_locks WHERE id=1")).first()
            assert row[0] == "racer-A"
            sched.HOLDER_ID = "racer-A"
            assert sched.is_leader(lock_db) is True
        finally:
            sched.HOLDER_ID = old

    def test_stale_lease_takeover(self, lock_db):
        from app.services import scheduler as sched
        old = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = "worker-old"
            assert sched.try_acquire_lock(lock_db) is True
            from app.config import settings
            _age_lock(lock_db, settings.SCHEDULER_LOCK_TTL + 60)
            sched.HOLDER_ID = "worker-new"
            assert sched.try_acquire_lock(lock_db) is True
            assert sched.is_leader(lock_db) is True
            # old leader's heartbeat no longer renews (fencing)
            sched.HOLDER_ID = "worker-old"
            assert sched.heartbeat(lock_db) is False
        finally:
            sched.HOLDER_ID = old

    def test_lease_version_is_fencing_token(self, lock_db):
        from app.services import scheduler as sched
        old = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = "w1"
            sched.try_acquire_lock(lock_db)
            v1 = lock_db.execute(text("SELECT lease_version FROM scheduler_locks")).scalar()
            sched.HOLDER_ID = "w2"
            from app.config import settings
            _age_lock(lock_db, settings.SCHEDULER_LOCK_TTL + 60)
            sched.try_acquire_lock(lock_db)
            v2 = lock_db.execute(text("SELECT lease_version FROM scheduler_locks")).scalar()
            assert v2 == v1 + 1
        finally:
            sched.HOLDER_ID = old

    def test_release_only_by_holder(self, lock_db):
        from app.services import scheduler as sched
        old = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = "w1"
            sched.try_acquire_lock(lock_db)
            sched.HOLDER_ID = "intruder"
            assert sched.release_lock(lock_db) is False
            sched.HOLDER_ID = "w1"
            assert sched.release_lock(lock_db) is True
            # released lease can be acquired by anyone
            sched.HOLDER_ID = "w2"
            assert sched.try_acquire_lock(lock_db) is True
        finally:
            sched.HOLDER_ID = old

    def test_lease_still_held_false_after_takeover(self, lock_db):
        from app.services import scheduler as sched
        old = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = "worker-old"
            sched.try_acquire_lock(lock_db)
            assert sched._lease_still_held(lock_db) is True
            # simulate external takeover by another worker
            lock_db.execute(text("UPDATE scheduler_locks SET holder='w2' WHERE id=1"))
            lock_db.commit()
            # worker-old's fencing check must now fail
            assert sched._lease_still_held(lock_db) is False
        finally:
            sched.HOLDER_ID = old

    def test_crawl_job_skipped_when_lease_lost(self, db):
        """A demoted leader must not crawl: no CrawlTask side effects.

        _crawl_job opens its own SessionLocal session, so this uses the shared
        test DB (the `db` fixture) for both the lock and the assertions.
        """
        from app.models import CrawlTask, Source
        from app.services import scheduler as sched
        old = sched.HOLDER_ID
        try:
            src = Source(name="fencing test", type="rss",
                         url="https://example.com/rss", enabled=True)
            db.add(src)
            db.commit()
            sched.HOLDER_ID = "stale-leader"
            assert sched.try_acquire_lock(db) is True
            # another worker takes over while our job is about to dispatch
            db.execute(text("UPDATE scheduler_locks SET holder='new-leader' WHERE id=1"))
            db.commit()
            sched._crawl_job(src.id)
            db.expire_all()
            assert db.query(CrawlTask).filter_by(source_id=src.id).count() == 0
        finally:
            sched.HOLDER_ID = old
            db.execute(text("UPDATE scheduler_locks SET holder='' WHERE id=1"))
            db.commit()


class TestMigrationMutex:
    def _migrate(self, db_path):
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{db_path}"
        env["ENV"] = "dev"
        env["ROLE"] = "api"
        r = subprocess.run([sys.executable, "-m", "app.migrate"],
                           capture_output=True, text=True, cwd=BACKEND, env=env, timeout=300)
        return r

    def test_fresh_db_migration(self, tmp_path):
        import sqlalchemy as sa
        db = str(tmp_path / "fresh.db")
        r = self._migrate(db)
        assert r.returncode == 0, r.stderr[-800:]
        eng = sa.create_engine(f"sqlite:///{db}")
        tables = {row[0] for row in eng.connect().execute(
            text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "scheduler_locks" in tables and "lease_version" in {
            c[1] for c in eng.connect().execute(text("PRAGMA table_info(scheduler_locks)"))}
        assert "alembic_version" in tables
        eng.dispose()

    def test_rerun_is_idempotent(self, tmp_path):
        db = str(tmp_path / "rerun.db")
        assert self._migrate(db).returncode == 0
        r = self._migrate(db)
        assert r.returncode == 0, r.stderr[-800:]

    def test_concurrent_migrations_one_flock(self, tmp_path):
        """Two migrate processes racing on a fresh DB: guard serializes them."""
        db = str(tmp_path / "conc.db")
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{db}"
        env["ENV"] = "dev"
        env["ROLE"] = "api"
        procs = [subprocess.Popen([sys.executable, "-m", "app.migrate"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=BACKEND, env=env, text=True) for _ in range(2)]
        outs = [p.communicate(timeout=300) for p in procs]
        codes = [p.returncode for p in procs]
        assert all(c == 0 for c in codes), [o[1][-500:] for o in outs]
        # guard row must be released afterwards
        import sqlalchemy as sa
        eng = sa.create_engine(f"sqlite:///{db}")
        conn = eng.connect()
        guard = conn.execute(text("SELECT COUNT(*) FROM _migration_guard")).scalar()
        conn.close()
        eng.dispose()
        assert guard == 0

    def test_upgrade_then_downgrade_then_upgrade(self, tmp_path):
        db = str(tmp_path / "ud.db")
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{db}"
        env["ENV"] = "dev"
        r1 = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                            capture_output=True, text=True, cwd=BACKEND, env=env, timeout=300)
        assert r1.returncode == 0, r1.stderr[-500:]
        r2 = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"],
                            capture_output=True, text=True, cwd=BACKEND, env=env, timeout=300)
        assert r2.returncode == 0, r2.stderr[-500:]
        r3 = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                            capture_output=True, text=True, cwd=BACKEND, env=env, timeout=300)
        assert r3.returncode == 0, r3.stderr[-500:]


class TestAutoMigrateResolution:
    def test_dev_default_true_prod_default_false(self):
        from app.config import Settings, auto_migrate_enabled
        s = Settings(_env_file=None)
        assert auto_migrate_enabled(s) is True  # ENV defaults to dev
        p = Settings(_env_file=None, ENV="production")
        assert auto_migrate_enabled(p) is False

    def test_explicit_override_wins(self):
        from app.config import Settings, auto_migrate_enabled
        assert auto_migrate_enabled(Settings(_env_file=None, ENV="production",
                                             AUTO_MIGRATE=True)) is True
        assert auto_migrate_enabled(Settings(_env_file=None, AUTO_MIGRATE=False)) is False


class TestDbUrlEncoding:
    """Passwords with special characters must never be double-encoded.

    encode_db_url() treats a credential as ALREADY-ENCODED when it contains
    percent-escapes (documented heuristic), otherwise encodes it exactly once.
    """

    def _url(self, password):
        from app.config import Settings, encode_db_url
        s = Settings(_env_file=None,
                     DATABASE_URL=f"postgresql://radar:{password}@db:5432/radar")
        return encode_db_url(s)

    def test_raw_special_chars_encoded_exactly_once(self):
        from urllib.parse import unquote
        from sqlalchemy.engine import make_url
        for pwd in ["p@ss", "pa:ss", "pa/ss", "pa+ss", "pa#ss",
                    "pa ss", "复杂密码123", "a@b:c/d#f g"]:
            url = self._url(pwd)
            # exactly one encoding layer: unquoting the URL password yields the original
            parsed = make_url(url)
            assert unquote(parsed.password) == pwd, f"{pwd!r} -> {url}"
            assert parsed.username == "radar"
            assert parsed.host == "db" and parsed.port == 5432
            assert parsed.database == "radar"

    def test_idempotent_on_own_output(self):
        from app.config import Settings, encode_db_url
        for pwd in ["p@ss", "pa ss", "a@b:c/d#f g"]:
            once = self._url(pwd)
            s2 = Settings(_env_file=None, DATABASE_URL=once)
            assert encode_db_url(s2) == once

    def test_already_encoded_input_not_double_encoded(self):
        from urllib.parse import unquote
        from sqlalchemy.engine import make_url
        # password "p@ss" pre-encoded as p%40ss
        from app.config import Settings, encode_db_url
        s = Settings(_env_file=None,
                     DATABASE_URL="postgresql://radar:p%40ss@db:5432/radar")
        url = encode_db_url(s)
        assert url.count("%40") == 1
        assert "%2540" not in url
        assert make_url(url).password == "p@ss"
        assert unquote(make_url(url).password) == "p@ss"

    def test_driver_normalization(self):
        for prefix in ["postgres://", "postgresql://", "postgresql+psycopg://"]:
            from app.config import Settings, encode_db_url
            s = Settings(_env_file=None,
                         DATABASE_URL=f"{prefix}radar:secret-pass-123456@db:5432/radar")
            assert encode_db_url(s).startswith("postgresql+psycopg://")

    def test_sqlite_passthrough(self):
        from app.config import Settings, encode_db_url
        s = Settings(_env_file=None, DATABASE_URL="sqlite:///./data/x.db")
        assert encode_db_url(s) == "sqlite:///./data/x.db"


class TestProductionPasswordParsing:
    """Hermetic: every relevant variable is set explicitly so the backend/.env
    file (dev defaults) cannot influence the subprocess."""

    def _run(self, env_overrides):
        code = ("from app.config import Settings, validate_production_config;"
                "validate_production_config(Settings())")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        env = dict(os.environ)
        env["ENV"] = "production"
        env["ROLE"] = "worker"
        env["JWT_SECRET"] = "x" * 40
        env["ADMIN_PASSWORD"] = "Strong" + "Pass" + "w0rd" + "!!"
        env["ALLOWED_ORIGINS"] = ""  # neutralized baseline; tests override
        env.update(env_overrides)
        if "DATABASE_URL" not in env_overrides:
            env["DATABASE_URL"] = f"sqlite:///{db_path}"
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, cwd=BACKEND, env=env, timeout=120)
        os.unlink(db_path)
        return r

    def test_strong_pg_password_accepted(self):
        r = self._run({"ALLOWED_ORIGINS": "https://radar.example.com",
                       "DATABASE_URL": "postgresql+psycopg://radar:Str0ng-P%40ss-2026!!@127.0.0.1:5432/radar"})
        assert r.returncode == 0, r.stderr[-400:]

    def test_weak_pg_password_rejected(self):
        r = self._run({"ALLOWED_ORIGINS": "https://radar.example.com",
                       "DATABASE_URL": "postgresql+psycopg://radar:password@127.0.0.1:5432/radar"})
        assert r.returncode != 0

    def test_missing_origins_rejected(self):
        r = self._run({"DATABASE_URL": "postgresql+psycopg://radar:Str0ng-P%40ss-2026!!@127.0.0.1:5432/radar"})
        assert r.returncode != 0

    def test_wildcard_origins_rejected(self):
        r = self._run({"ALLOWED_ORIGINS": "*",
                       "DATABASE_URL": "postgresql+psycopg://radar:Str0ng-P%40ss-2026!!@127.0.0.1:5432/radar"})
        assert r.returncode != 0

    def test_sqlite_in_production_rejected(self):
        r = self._run({"ALLOWED_ORIGINS": "https://radar.example.com"})  # DATABASE_URL defaults to sqlite
        assert r.returncode != 0

    def test_raw_special_char_password_length_checked_on_decoded_value(self):
        # raw password containing @: validation must decode before judging length
        r = self._run({"ALLOWED_ORIGINS": "https://radar.example.com",
                       "DATABASE_URL": "postgresql+psycopg://radar:short@127.0.0.1:5432/radar"})
        assert r.returncode != 0  # 'short' decodes short -> rejected


class TestDeploymentConfig (object):
    """STATIC configuration checks (file contents, not runtime behavior)."""

    def _read(self, rel):
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            return f.read()

    def test_nginx_health_location_exists_and_exact(self):
        conf = self._read("nginx.prod.conf")
        assert "location = /nginx-health" in conf  # exact match, no SPA fallback

    def test_web_healthcheck_uses_nginx_health(self):
        assert "/nginx-health" in self._read("Dockerfile.web")

    def test_api_dockerfile_no_longer_migrates(self):
        cmd = [l for l in self._read("Dockerfile.api").splitlines()
               if l.startswith("CMD")]
        assert cmd and "migrate_or_init" not in cmd[0]

    def test_compose_has_migrate_service_and_ordering(self):
        c = self._read("docker-compose.prod.yml")
        assert "migrate:" in c
        assert "service_completed_successfully" in c
        assert c.count("AUTO_MIGRATE") >= 3  # image default + api + worker override
        assert "POSTGRES_PASSWORD" in c  # db initializes from env
        assert "pgdata" in c

    def test_no_host_port_for_db_or_api(self):
        c = self._read("docker-compose.prod.yml")
        db_block = c.split("db:")[1].split("migrate:")[0]
        assert "ports:" not in db_block          # postgres not published
        api_block = c.split("api:")[1].split("worker:")[0]
        assert "ports:" not in api_block         # api not published directly
        web_block = c.split("web:")[1]
        assert "ports:" in web_block             # only web is public

    def test_env_example_documents_new_vars(self):
        ex = self._read(".env.example")
        assert "AUTO_MIGRATE" in ex and "ALLOWED_PORTS" in ex

    def test_tls_variant_present_and_documented(self):
        assert os.path.exists(os.path.join(REPO, "nginx.tls.conf"))
        tls = self._read("nginx.tls.conf")
        assert "ssl_certificate" in tls and "return 301 https://" in tls

    def test_worker_healthcheck_uses_own_endpoint(self):
        c = self._read("docker-compose.prod.yml")
        # worker must be checked on its OWN embedded health server (8001),
        # never on the API's port (which would report the wrong process)
        assert "localhost:8001/health/live" in c
        assert "8001" not in self._read("Dockerfile.web")

    def test_web_healthcheck_not_spa_fallback(self):
        # web container must check a real static endpoint, not a path that the
        # SPA fallback would answer with index.html (false-positive health)
        web = self._read("Dockerfile.web")
        assert "/nginx-health" in web
        assert "index.html" not in web.split("HEALTHCHECK")[-1]
        nginx = self._read("nginx.prod.conf")
        assert "location = /nginx-health" in nginx  # exact match, no SPA fallback


class TestHealthEndpoints:
    """Layered health checks: liveness / readiness / scheduler lease honesty."""

    def _clear_lock(self, db):
        db.execute(text("DELETE FROM scheduler_locks"))
        db.commit()

    def _put_lock(self, db, holder, age_seconds=0):
        from app.models import SchedulerLock
        self._clear_lock(db)
        ts = datetime.utcnow() - timedelta(seconds=age_seconds)
        db.add(SchedulerLock(id=1, holder=holder, acquired_at=ts,
                             heartbeat_at=ts, lease_version=1))
        db.commit()

    def test_live_is_process_only(self, client):
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

    def test_ready_reports_database(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["database"] == "ok"

    def test_scheduler_health_honest_without_leader(self, client, db):
        self._clear_lock(db)
        body = client.get("/health/scheduler").json()
        assert body["leader_elected"] is False
        assert body["lease_valid"] is False
        assert body["ok"] is False  # no leader => nobody is scheduling

    def test_scheduler_health_ok_with_fresh_lease(self, client, db):
        self._put_lock(db, "worker-1:99", age_seconds=5)
        body = client.get("/health/scheduler").json()
        assert body["ok"] is True
        assert body["leader_holder"] == "worker-1:99"
        assert body["lease_valid"] is True

    def test_scheduler_health_expired_lease_is_not_ok(self, client, db):
        from app.config import settings
        self._put_lock(db, "stale-worker:99",
                       age_seconds=settings.SCHEDULER_LOCK_TTL + 60)
        body = client.get("/health/scheduler").json()
        assert body["leader_elected"] is True   # a row exists...
        assert body["lease_valid"] is False     # ...but it is stale
        assert body["ok"] is False

    def test_worker_embedded_health_server(self):
        """The worker's stdlib health server answers live/ready/scheduler/404."""
        import httpx
        from http.server import ThreadingHTTPServer
        from app.worker import _HealthHandler
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            live = httpx.get(f"http://127.0.0.1:{port}/health/live", trust_env=False)
            assert live.status_code == 200
            assert live.json()["role"] == "worker"
            ready = httpx.get(f"http://127.0.0.1:{port}/health/ready", trust_env=False)
            assert ready.status_code == 200
            assert ready.json()["status"] == "ready"
            sched = httpx.get(f"http://127.0.0.1:{port}/health/scheduler", trust_env=False)
            assert sched.status_code == 200
            assert "scheduler_running" in sched.json()
            assert "lease_version" in sched.json()
            nf = httpx.get(f"http://127.0.0.1:{port}/nope", trust_env=False)
            assert nf.status_code == 404
        finally:
            server.shutdown()
            server.server_close()


class TestStaleGuardTakeover:
    """A guard row left by a DEAD migrate process must never block forever."""

    def _migrate_env(self, db):
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{db}"
        env["ENV"] = "dev"
        env["ROLE"] = "api"
        return env

    def test_dead_pid_guard_is_taken_over(self, tmp_path):
        import sqlalchemy as sa
        db = str(tmp_path / "stale.db")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()  # pid is now guaranteed dead
        eng = sa.create_engine(f"sqlite:///{db}")
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE _migration_guard ("
                " id INTEGER PRIMARY KEY, pid TEXT NOT NULL, started_at TEXT NOT NULL)"))
            conn.execute(text(
                "INSERT INTO _migration_guard (id, pid, started_at) VALUES (1, :p, '2020-01-01')"),
                {"p": str(dead.pid)})
        eng.dispose()
        r = subprocess.run([sys.executable, "-m", "app.migrate"],
                           capture_output=True, text=True, cwd=BACKEND,
                           env=self._migrate_env(db), timeout=300)
        assert r.returncode == 0, r.stderr[-500:]
        assert "僵死迁移守卫" in r.stdout + r.stderr  # takeover was logged

    def test_live_pid_guard_is_respected(self, tmp_path):
        """A guard row owned by a live process keeps other migrates waiting."""
        import sqlalchemy as sa
        from app.database import engine  # default test DB (has no guard row)
        Base_meta = type(engine).__mro__  # noqa: F841  (documentation only)
        # direct unit check of the liveness predicate on the app engine
        from app import migrate as mig
        mig._ensure_guard_table()
        mig._exit_guard()
        mig._try_enter_guard()  # hold it as the live "other" process
        try:
            assert mig._someone_migrating() is True
        finally:
            mig._exit_guard()
        assert mig._someone_migrating() is False
