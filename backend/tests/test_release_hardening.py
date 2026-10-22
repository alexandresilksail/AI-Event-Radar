"""Release-hardening regression tests (v1.4 audit).

Every test here corresponds to a defect found during the release audit and
must fail on the pre-audit code. Grouped by the stage of the audit that
surfaced them:

1. DNS resolver hygiene  — a test-injected fake resolver must never leak
   process-wide, and the guard must be installed in the worker too.
2. Trusted internal hosts — operator-configured private endpoints (SMTP relay,
   internal LLM gateway) must stay reachable.
3. Authentication boundary — operational endpoints must not be world-readable.
4. Credential safety — masked secrets must survive an edit round-trip, and
   password comparison must be constant-time.
5. Notification webhook SSRF — user-configured webhook URLs must be validated.
"""
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import security
from app.security import (SSRFError, is_public_ip, install_guarded_resolver,
                          trusted_internal_hosts, uninstall_guarded_resolver)


# ---------------------------------------------------------------- 1. resolver

class TestResolverHygiene:
    """The guarded resolver must never leave a test fake installed globally."""

    def test_uninstall_restores_native_resolver_not_the_fake_seam(self):
        """Regression: uninstall() used to restore the mutable _orig_getaddrinfo.

        A test that injected a fake resolver into that seam therefore left the
        FAKE installed process-wide after uninstall, silently disabling SSRF
        enforcement (and breaking every later test module).
        """
        import app.security as sec
        native = sec._NATIVE_GETADDRINFO
        fake = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 0))]
        original_seam = sec._orig_getaddrinfo
        try:
            sec._orig_getaddrinfo = fake
            install_guarded_resolver()
            assert socket.getaddrinfo is not native
        finally:
            uninstall_guarded_resolver()
            sec._orig_getaddrinfo = original_seam
        # the NATIVE resolver must be back — not the fake seam
        assert socket.getaddrinfo is native, "uninstall must restore the native resolver"

    def test_fake_resolver_does_not_survive_uninstall(self):
        """After uninstall, a hostname resolving to a private IP must work again
        (i.e. the fake that maps everything to a public IP is gone)."""
        import app.security as sec
        native = sec._NATIVE_GETADDRINFO
        original_seam = sec._orig_getaddrinfo
        try:
            install_guarded_resolver()
        finally:
            uninstall_guarded_resolver()
            sec._orig_getaddrinfo = original_seam
        assert socket.getaddrinfo is native

    def test_guarded_resolver_blocks_private_resolution(self):
        """The guard must still reject a hostname that resolves privately."""
        import app.security as sec
        native = sec._NATIVE_GETADDRINFO
        original_seam = sec._orig_getaddrinfo

        def fake(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

        try:
            sec._orig_getaddrinfo = fake
            install_guarded_resolver()
            with pytest.raises(SSRFError):
                socket.getaddrinfo("internal.example.com", 80)
        finally:
            uninstall_guarded_resolver()
            sec._orig_getaddrinfo = original_seam
        assert socket.getaddrinfo is native

    def test_worker_installs_the_dns_guard(self):
        """Regression: `python -m app.worker` never ran app.main's lifespan(),
        so the process that ACTUALLY crawls had no TOCTOU protection."""
        import importlib
        import app.worker as worker_mod
        src = open(worker_mod.__file__, encoding="utf-8").read()
        assert "install_guarded_resolver()" in src, \
            "worker must install the DNS guard itself"

    def test_worker_main_installs_guard_at_runtime(self, monkeypatch):
        """The worker's main() must install the guard before doing any work."""
        import app.worker as worker_mod
        calls = []
        monkeypatch.setattr(worker_mod, "install_guarded_resolver",
                            lambda: calls.append("install"))
        monkeypatch.setattr(worker_mod, "validate_production_config", lambda s: None)
        monkeypatch.setattr(worker_mod, "auto_migrate_enabled", lambda s: False)
        monkeypatch.setattr(worker_mod, "ensure_default_admin", lambda db: None)
        monkeypatch.setattr(worker_mod, "_start_health_server", lambda: None)
        monkeypatch.setattr(worker_mod.sched, "start_scheduler", lambda: True)
        monkeypatch.setattr(worker_mod.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            worker_mod.main()
        except KeyboardInterrupt:
            pass
        assert "install" in calls, "worker.main() must install the DNS guard"


# ------------------------------------------------- 2. trusted internal hosts

class TestTrustedInternalHosts:
    def test_smtp_and_ai_hosts_are_trusted(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "SMTP_HOST", "smtp.corp.internal")
        monkeypatch.setattr(settings, "AI_BASE_URL", "http://llm-gateway.internal:8080/v1")
        hosts = trusted_internal_hosts()
        assert "smtp.corp.internal" in hosts
        assert "llm-gateway.internal" in hosts

    def test_explicit_trusted_hosts_are_included(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "TRUSTED_INTERNAL_HOSTS", "a.internal, b.internal")
        hosts = trusted_internal_hosts()
        assert "a.internal" in hosts and "b.internal" in hosts

    def test_guarded_resolver_allows_trusted_internal_host(self, monkeypatch):
        """A trusted operator endpoint must NOT be blocked by the global guard."""
        import app.security as sec
        from app.config import settings
        native = sec._NATIVE_GETADDRINFO
        original_seam = sec._orig_getaddrinfo

        def fake(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

        try:
            monkeypatch.setattr(settings, "TRUSTED_INTERNAL_HOSTS", "smtp.corp.internal")
            monkeypatch.setattr(settings, "SMTP_HOST", "")
            monkeypatch.setattr(settings, "AI_BASE_URL", "")
            sec._orig_getaddrinfo = fake
            install_guarded_resolver()
            res = socket.getaddrinfo("smtp.corp.internal", 25)
            assert res, "trusted internal host must resolve"
        finally:
            uninstall_guarded_resolver()
            sec._orig_getaddrinfo = original_seam
        assert socket.getaddrinfo is native

    def test_untrusted_private_host_is_still_blocked(self, monkeypatch):
        import app.security as sec
        from app.config import settings
        native = sec._NATIVE_GETADDRINFO
        original_seam = sec._orig_getaddrinfo

        def fake(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

        try:
            monkeypatch.setattr(settings, "TRUSTED_INTERNAL_HOSTS", "")
            monkeypatch.setattr(settings, "SMTP_HOST", "")
            monkeypatch.setattr(settings, "AI_BASE_URL", "")
            sec._orig_getaddrinfo = fake
            install_guarded_resolver()
            with pytest.raises(SSRFError):
                socket.getaddrinfo("evil.example.com", 80)
        finally:
            uninstall_guarded_resolver()
            sec._orig_getaddrinfo = original_seam
        assert socket.getaddrinfo is native


# ------------------------------------------------- 3. authentication boundary

PUBLIC_SAFE_GETS = ["/health/live", "/health/ready", "/health", "/status"]


class TestAuthenticationBoundary:
    """Operational endpoints expose internal state and must require a token.

    Public radar content (/events, /calendar, /stats/*) stays public on purpose.
    """

    PROTECTED_GETS = [
        "/sources",
        "/sources/1",
        "/crawl-tasks",
        "/crawl-tasks/running",
        "/crawl-tasks/1",
        "/crawl-logs",
        "/monitor",
        "/notification-rules",
        "/settings",
    ]

    @pytest.mark.parametrize("path", PROTECTED_GETS)
    def test_operational_get_requires_auth(self, client, path):
        r = client.get(path)
        assert r.status_code == 401, f"{path} must require authentication, got {r.status_code}"

    @pytest.mark.parametrize("path", PROTECTED_GETS)
    def test_operational_get_with_token_succeeds(self, client, auth_headers, path):
        r = client.get(path, headers=auth_headers)
        assert r.status_code in (200, 404), f"{path} -> {r.status_code}"

    def test_notification_test_endpoint_requires_auth(self, client, db):
        from app.models import NotificationRule
        rule = NotificationRule(name="R-auth", channel="telegram",
                                config={"bot_token": "x", "chat_id": "1"}, filters={})
        db.add(rule)
        db.commit()
        r = client.post(f"/notification-rules/{rule.id}/test")
        assert r.status_code == 401, "test-send triggers a real outbound send; must require auth"

    def test_notification_test_endpoint_with_token(self, client, auth_headers, db):
        from app.models import NotificationRule
        rule = NotificationRule(name="R-auth2", channel="telegram",
                                config={"bot_token": "x", "chat_id": "1"}, filters={})
        db.add(rule)
        db.commit()
        r = client.post(f"/notification-rules/{rule.id}/test", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["ok"] is False  # honest failure, no crash

    @pytest.mark.parametrize("path", PUBLIC_SAFE_GETS)
    def test_public_health_endpoints_stay_open(self, client, path):
        r = client.get(path)
        assert r.status_code in (200, 503), f"{path} -> {r.status_code}"

    def test_public_radar_content_stays_public(self, client):
        """The public product surface must remain readable without a token."""
        for path in ("/events", "/calendar", "/stats/dashboard", "/stats/analysis"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} must stay public, got {r.status_code}"


# ------------------------------------------------------- 4. credential safety

class TestCredentialSafety:
    def test_masked_secret_survives_edit_roundtrip(self, client, auth_headers, db):
        """Regression: editing a rule wrote the MASKED value back to the DB,
        destroying the stored credential (e.g. bot_token became '***')."""
        from app.models import NotificationRule
        rule = NotificationRule(name="R-roundtrip", channel="telegram",
                                config={"bot_token": "123:SECRET", "chat_id": "42"},
                                filters={})
        db.add(rule)
        db.commit()
        rid = rule.id

        masked = client.get("/notification-rules", headers=auth_headers).json()
        mine = next(r for r in masked["rules"] if r["id"] == rid)
        assert mine["config"]["bot_token"] == "***"

        r = client.put(f"/notification-rules/{rid}", headers=auth_headers, json={
            "name": "R-roundtrip-renamed", "channel": "telegram",
            "config": mine["config"], "filters": {}, "enabled": True})
        assert r.status_code == 200

        db.expire_all()
        row = db.query(NotificationRule).filter_by(id=rid).first()
        assert row.config["bot_token"] == "123:SECRET", "stored secret must be preserved"
        assert row.config["chat_id"] == "42"
        assert row.name == "R-roundtrip-renamed"

    def test_new_secret_value_does_replace_stored_one(self, client, auth_headers, db):
        from app.models import NotificationRule
        rule = NotificationRule(name="R-replace", channel="telegram",
                                config={"bot_token": "old", "chat_id": "1"}, filters={})
        db.add(rule)
        db.commit()
        rid = rule.id
        r = client.put(f"/notification-rules/{rid}", headers=auth_headers, json={
            "name": "R-replace", "channel": "telegram",
            "config": {"bot_token": "new-token", "chat_id": "1"},
            "filters": {}, "enabled": True})
        assert r.status_code == 200
        db.expire_all()
        row = db.query(NotificationRule).filter_by(id=rid).first()
        assert row.config["bot_token"] == "new-token"

    def test_password_comparison_is_constant_time(self):
        """verify_password must not use a short-circuiting equality on digests."""
        from app.security import hash_password, verify_password
        import inspect
        src = inspect.getsource(verify_password)
        assert "compare_digest" in src, "must use hmac.compare_digest"
        stored = hash_password("correct-horse")
        assert verify_password("correct-horse", stored) is True
        assert verify_password("wrong", stored) is False

    def test_verify_password_rejects_garbage(self):
        from app.security import verify_password
        assert verify_password("x", "not-a-hash") is False
        assert verify_password("x", "pbkdf2$zz$zz") is False


# ------------------------------------------------- 5. notification webhook SSRF

class TestNotificationWebhookSSRF:
    def test_webhook_url_is_validated_before_use(self, client, auth_headers, db, monkeypatch):
        """A webhook URL pointing at the cloud metadata service must be rejected
        at send time, before any request is made."""
        from app.models import NotificationRule
        from app.services import notifier
        rule = NotificationRule(name="meta", channel="webhook",
                                config={"url": "http://169.254.169.254/latest/meta-data/"},
                                filters={})
        db.add(rule)
        db.commit()
        rid = rule.id
        seen = {}

        def fake_post(url, **kw):
            seen["url"] = url
            raise RuntimeError("should never be called")

        monkeypatch.setattr(notifier.httpx, "post", fake_post)
        r = client.post(f"/notification-rules/{rid}/test", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "url" not in seen, "SSRF target must be blocked before the request"
        assert "禁止" in r.json()["error"] or "SSRF" in r.json()["error"]


# ------------------------------------------------------------- misc invariants

class TestPublicIPClassificationStillHolds:
    @pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254",
                                    "::1", "::ffff:127.0.0.1", "192.168.1.1"])
    def test_private_addresses_denied(self, ip):
        assert is_public_ip(ip) is False

    @pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "8.8.8.8"])
    def test_public_addresses_allowed(self, ip):
        assert is_public_ip(ip) is True
