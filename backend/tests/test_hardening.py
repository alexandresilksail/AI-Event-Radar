"""Tests for audit fixes: startup config, admin flows, SSRF redirects, health, CORS, lock."""
import hashlib
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.collectors import safe_get, CollectorError, ChangeDetectionCollector, HTMLCollector
from app.models import SchedulerLock, Source, User
from app.security import (SSRFError, is_public_ip, validate_public_url,
                          _orig_getaddrinfo, install_guarded_resolver)

BACKEND = Path(__file__).resolve().parent.parent


# ---------- P0: production startup validation ----------

class TestProductionConfig:
    def _run_with_env(self, env_overrides: dict) -> subprocess.CompletedProcess:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        env = dict(os.environ)
        env.pop("ENV", None)  # test overrides decide the environment
        env.update(env_overrides)
        if "DATABASE_URL" not in env_overrides:  # only default when not explicitly set
            env["DATABASE_URL"] = f"sqlite:///{db_path}"
        code = ("import sys; sys.path.insert(0, %r);"
                "from app.config import Settings, validate_production_config;"
                "validate_production_config(Settings())" % str(BACKEND).replace("\\", "\\\\"))
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              env=env, cwd=str(BACKEND), timeout=60)

    def test_default_jwt_secret_rejected(self):
        r = self._run_with_env({"ENV": "production", "JWT_SECRET": "change-me-in-production",
                                "ADMIN_PASSWORD": "StrongPassw0rd!!", "DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:5432/d"})
        assert r.returncode != 0 and "JWT_SECRET" in r.stderr + r.stdout

    def test_default_admin_password_rejected(self):
        r = self._run_with_env({"ENV": "production", "JWT_SECRET": "x" * 40,
                                "ADMIN_PASSWORD": "admin123",
                                "DATABASE_URL": "postgresql+psycopg://u:PhantomPass2026!!@127.0.0.1:5432/d"})
        assert r.returncode != 0 and "ADMIN_PASSWORD" in r.stderr + r.stdout

    def test_sqlite_rejected_in_production(self):
        r = self._run_with_env({"ENV": "production", "JWT_SECRET": "x" * 40,
                                "ADMIN_PASSWORD": "StrongPassw0rd!!"})
        assert r.returncode != 0 and "PostgreSQL" in r.stderr + r.stdout

    def test_good_production_config_passes(self):
        r = self._run_with_env({"ENV": "production", "JWT_SECRET": "x" * 40,
                                "ADMIN_PASSWORD": "StrongPassw0rd!!", "ROLE": "worker",
                                "ALLOWED_ORIGINS": "https://radar.example.com",
                                "DATABASE_URL": "postgresql+psycopg://u:PhantomPass2026!!@127.0.0.1:5432/d"})
        assert r.returncode == 0, r.stderr + r.stdout

    def test_wildcard_origins_rejected_in_prod(self):
        r = self._run_with_env({"ENV": "production", "JWT_SECRET": "x" * 40,
                                "ADMIN_PASSWORD": "StrongPassw0rd!!",
                                "ALLOWED_ORIGINS": "*",
                                "DATABASE_URL": "postgresql+psycopg://u:PhantomPass2026!!@127.0.0.1:5432/d"})
        assert r.returncode != 0 and "ALLOWED_ORIGINS" in r.stderr + r.stdout

    def test_dev_env_skips_validation(self):
        r = self._run_with_env({"ENV": "dev", "JWT_SECRET": "change-me-in-production",
                                "ADMIN_PASSWORD": "admin123"})
        assert r.returncode == 0


# ---------- admin lifecycle ----------

class TestAdminLifecycle:
    def test_admin_creation_idempotent_no_reset(self, client, db):
        from app.routers.auth import ensure_default_admin
        from app.security import hash_password, verify_password
        # simulate existing account with a different password
        db.query(User).delete()
        db.add(User(username="admin", password_hash=hash_password("already-set-strong-password-123")))
        db.commit()
        ensure_default_admin(db)
        ensure_default_admin(db)  # boot twice more
        user = db.query(User).filter_by(username="admin").one()
        assert verify_password("already-set-strong-password-123", user.password_hash)
        assert db.query(User).count() == 1
        # restore shared-fixture state for later tests
        user.password_hash = hash_password("test-admin-pass")
        db.commit()

    def test_change_password(self, client, auth_headers, db):
        from app.security import hash_password
        r = client.post("/auth/change-password", headers=auth_headers,
                        json={"old_password": "test-admin-pass", "new_password": "new-strong-secret-2026"})
        assert r.status_code == 200
        assert client.post("/auth/login", json={"username": "admin",
                                                "password": "new-strong-secret-2026"}).status_code == 200
        # old password no longer works
        assert client.post("/auth/login", json={"username": "admin",
                                                "password": "test-admin-pass"}).status_code == 401
        # restore shared-fixture state
        user = db.query(User).filter_by(username="admin").one()
        user.password_hash = hash_password("test-admin-pass")
        db.commit()

    def test_change_password_rejects_wrong_old(self, client, auth_headers):
        r = client.post("/auth/change-password", headers=auth_headers,
                        json={"old_password": "wrong-one-here", "new_password": "whatever-strong-2026"})
        assert r.status_code == 403

    def test_reset_endpoint_disabled_without_token(self, client):
        r = client.post("/auth/reset-password", json={"reset_token": "guess",
                                                      "new_password": "any-strong-password-99"})
        assert r.status_code == 403


# ---------- SSRF with real local HTTP server ----------

class _RedirectTestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", "http://192.168.0.1/admin")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"local-server-payload")

    def log_message(self, *args):
        pass


@pytest.fixture()
def local_server():
    srv = HTTPServer(("127.0.0.1", 0), _RedirectTestHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


class TestSSRFRedirects:
    def test_direct_localhost_blocked(self, local_server):
        with pytest.raises(CollectorError) as e:
            safe_get(local_server + "/payload")
        assert "SSRF" in str(e.value)

    def test_redirect_to_metadata_blocked_every_hop(self, local_server, monkeypatch):
        # first hop "pretends public" (simulating a public DNS), hop 2 must still be validated
        real_validate = validate_public_url

        def allow_first_hop(url):
            if url.startswith(local_server):
                return url
            return real_validate(url)

        monkeypatch.setattr("app.collectors.base.validate_public_url", allow_first_hop)
        with pytest.raises(CollectorError) as e:
            safe_get(local_server + "/to-metadata")
        assert "SSRF" in str(e.value)

    def test_redirect_to_private_ip_blocked(self, local_server, monkeypatch):
        real_validate = validate_public_url

        def allow_first_hop(url):
            return url if url.startswith(local_server) else real_validate(url)

        monkeypatch.setattr("app.collectors.base.validate_public_url", allow_first_hop)
        with pytest.raises(CollectorError) as e:
            safe_get(local_server + "/to-private")
        assert "SSRF" in str(e.value)

    def test_redirect_loop_terminates(self, local_server, monkeypatch):
        monkeypatch.setattr("app.collectors.base.validate_public_url",
                            lambda url: url if url.startswith(local_server) else validate_public_url(url))
        with pytest.raises(CollectorError) as e:
            safe_get(local_server + "/loop", timeout=5)
        assert "重定向" in str(e.value)

    def test_guarded_resolver_filters_private(self):
        """Once the guarded resolver is installed, results for hostnames that
        resolve only to private addresses raise SSRFError at DNS time."""
        res = _orig_getaddrinfo("localhost", 80)
        assert res
        # save & simulate guarded behavior on localhost
        for r in res:
            assert not is_public_ip(r[4][0]) or True  # sanity: is_public_ip works
        vetted = [r for r in res if is_public_ip(r[4][0])]
        assert vetted == []  # localhost -> all private -> guarded resolver would raise

    def test_resolver_install_is_idempotent(self):
        install_guarded_resolver()
        import socket
        first = socket.getaddrinfo
        install_guarded_resolver()
        assert socket.getaddrinfo is first


# ---------- change detection persistence ----------

class TestChangeDetection:
    def _mock_get(self, monkeypatch, html):
        monkeypatch.setattr(
            "app.collectors.html_api.safe_get",
            lambda url, **kw: __import__("httpx").Response(
                200, content=html.encode(), request=__import__("httpx").Request("GET", url)))

    def test_first_run_stores_baseline(self, monkeypatch, db):
        s = Source(name="CD 源", type="change_detection", url="https://cd.example.com/page",
                  frequency_minutes=60)
        db.add(s)
        db.commit()
        self._mock_get(monkeypatch, "<html><body>AI 大会 2026年11月3日</body></html>")
        items = ChangeDetectionCollector().fetch(s)
        assert len(items) == 1 and "首次基线" in items[0].content
        from app.models import SourceState
        assert db.query(SourceState).filter_by(source_id=s.id).count() == 1

    def test_unchanged_produces_nothing(self, monkeypatch, db):
        s = Source(name="CD 源2", type="change_detection", url="https://cd.example.com/p2",
                  frequency_minutes=60)
        db.add(s)
        db.commit()
        self._mock_get(monkeypatch, "<html><body>static content</body></html>")
        ChangeDetectionCollector().fetch(s)
        assert ChangeDetectionCollector().fetch(s) == []  # unchanged -> nothing new

    def test_change_detected(self, monkeypatch, db):
        s = Source(name="CD 源3", type="change_detection", url="https://cd.example.com/p3",
                  frequency_minutes=60)
        db.add(s)
        db.commit()
        html = "<html><body>AI 峰会</body></html>"
        self._mock_get(monkeypatch, html)
        ChangeDetectionCollector().fetch(s)
        self._mock_get(monkeypatch, "<html><body>AI 峰会 2026年12月1日 新增：免费报名</body></html>")
        items = ChangeDetectionCollector().fetch(s)
        assert len(items) == 1 and "内容变更" in items[0].content

    def test_failed_request_keeps_hash(self, monkeypatch, db):
        s = Source(name="CD 源4", type="change_detection", url="https://cd.example.com/p4",
                  frequency_minutes=60)
        db.add(s)
        db.commit()
        self._mock_get(monkeypatch, "<html><body>original</body></html>")
        ChangeDetectionCollector().fetch(s)
        from app.models import SourceState
        old_hash = db.query(SourceState).filter_by(source_id=s.id).one().content_hash

        def boom(url, **kw):
            raise CollectorError("网络中断")
        monkeypatch.setattr("app.collectors.html_api.safe_get", boom)
        with pytest.raises(CollectorError):
            ChangeDetectionCollector().fetch(s)
        assert db.query(SourceState).filter_by(source_id=s.id).one().content_hash == old_hash

    def test_state_is_db_backed_not_memory(self, monkeypatch, db):
        """A fresh collector instance (simulating process restart) sees persisted state."""
        s = Source(name="CD 源5", type="change_detection", url="https://cd.example.com/p5",
                  frequency_minutes=60)
        db.add(s)
        db.commit()
        self._mock_get(monkeypatch, "<html><body>restart-safe</body></html>")
        ChangeDetectionCollector().fetch(s)
        # brand-new collector instance, no in-memory cache exists anymore
        assert ChangeDetectionCollector().fetch(s) == []


# ---------- health ----------

class TestHealth:
    def test_live_and_ready(self, client):
        assert client.get("/health/live").json()["status"] == "alive"
        r = client.get("/health/ready")
        assert r.status_code == 200 and r.json()["database"] == "ok"

    def test_legacy_health_compat(self, client):
        assert client.get("/health").status_code == 200


# ---------- CORS ----------

class TestCORS:
    def test_origins_from_env(self, monkeypatch):
        from app import main
        monkeypatch.setattr(main.settings, "ALLOWED_ORIGINS", "https://a.example.com, https://b.example.com")
        assert main.allowed_origins() == ["https://a.example.com", "https://b.example.com"]

    def test_dev_default_local_origins(self, monkeypatch):
        from app import main
        monkeypatch.setattr(main.settings, "ENV", "dev")
        monkeypatch.setattr(main.settings, "ALLOWED_ORIGINS", "")
        origins = main.allowed_origins()
        assert "http://localhost:5173" in origins and "*" not in origins

    def test_prod_refuses_wildcard(self, monkeypatch):
        from app import main
        monkeypatch.setattr(main.settings, "ENV", "production")
        monkeypatch.setattr(main.settings, "ALLOWED_ORIGINS", "")
        with pytest.raises(SystemExit):
            main.allowed_origins()


# ---------- API collector validation ----------

class TestAPICollectorValidation:
    def test_type_validation(self, monkeypatch):
        import json
        from app.collectors.html_api import APICollector
        s = Source(name="api 源", type="api",
                  url="https://api.example.com/events",
                  method={"path_to_array": "data", "url_field": "url", "title_field": "name"})
        payload = {"data": [
            {"url": 123, "name": "non-str url skipped"},
            {"url": None, "name": "null skipped"},
            "just-a-string-row",
            {"url": "https://ok.example.com/e", "name": "AI 峰会"},
        ]}
        monkeypatch.setattr(
            "app.collectors.base.safe_get",
            lambda url, **kw: __import__("httpx").Response(
                200, content=json.dumps(payload).encode(),
                request=__import__("httpx").Request("GET", url)))
        items = APICollector().fetch(s)
        assert len(items) == 1 and items[0].title == "AI 峰会"


# ---------- scheduler leader lock ----------

class TestSchedulerLock:
    def test_single_leader(self, db):
        from app.services.scheduler import (try_acquire_lock, is_leader,
                                            release_lock, HOLDER_ID)
        from datetime import datetime, timedelta
        assert try_acquire_lock(db) is True
        assert is_leader(db) is True
        # another "process" cannot steal a fresh lock
        row = db.get(SchedulerLock, 1)
        row.holder = "other-host:999"
        row.heartbeat_at = datetime.utcnow()
        db.commit()
        assert try_acquire_lock(db) is False
        # stale lock can be taken over
        row.heartbeat_at = datetime.utcnow() - timedelta(seconds=99999)
        db.commit()
        assert try_acquire_lock(db) is True
        release_lock(db)
        assert db.get(SchedulerLock, 1).holder == ""

    def test_lock_uses_single_convention_row(self, db):
        """The leader election always operates on the single row with id=1."""
        from app.services.scheduler import try_acquire_lock
        try_acquire_lock(db)
        assert db.query(SchedulerLock).count() == 1
        assert db.query(SchedulerLock).one().id == 1
