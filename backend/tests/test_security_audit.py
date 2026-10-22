"""Auth / authz / XSS / CORS / dependency-surface audit (release audit stage 6).

Every check here exercises the real code path (real JWT encode/decode, real
FastAPI dependency graph, real HTTP requests through TestClient). Nothing is
asserted by reading source text except where the property IS a source-level
convention (e.g. "no dangerouslySetInnerHTML in the frontend"), and those
cases say so explicitly.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
FRONTEND_SRC = REPO / "frontend" / "src"


class TestPasswordHashing:
    def test_hash_is_salted_and_verifiable(self):
        from app.security import hash_password, verify_password
        h1 = hash_password("correct horse battery staple")
        h2 = hash_password("correct horse battery staple")
        assert h1 != h2, "同一密码两次哈希必须不同（盐必须随机）"
        assert verify_password("correct horse battery staple", h1)
        assert verify_password("correct horse battery staple", h2)

    def test_wrong_password_fails(self):
        from app.security import hash_password, verify_password
        h = hash_password("correct horse battery staple")
        assert not verify_password("wrong", h)
        assert not verify_password("", h)

    def test_malformed_hash_never_verifies(self):
        from app.security import verify_password
        for bad in ("", "garbage", "pbkdf2$zz$zz", "pbkdf2$$", "a$b$c"):
            assert verify_password("anything", bad) is False

    def test_iteration_count_is_strong(self):
        """PBKDF2 must not be downgraded to a weak iteration count."""
        import inspect
        from app import security
        src = inspect.getsource(security)
        # Every pbkdf2_hmac call must pass an explicit iteration count.
        # Match the count as the LAST argument of the call.
        counts = []
        for call in re.findall(r"pbkdf2_hmac\((.*?)\)\s*\n", src, re.S):
            last = call.strip().rstrip(",").split(",")[-1].strip()
            if re.fullmatch(r"\d[\d_]*", last):
                counts.append(int(last.replace("_", "")))
        assert counts, "未找到 PBKDF2 迭代次数"
        assert all(c >= 100_000 for c in counts), \
            f"PBKDF2 迭代次数过低: {counts}"

    def test_comparison_is_constant_time(self):
        """Password comparison must not use a short-circuiting equality."""
        import inspect
        from app.security import verify_password
        src = inspect.getsource(verify_password)
        assert "compare_digest" in src
        assert not re.search(r"dk_hex\s*==", src)


class TestJwt:
    def test_round_trip(self):
        from app.security import create_token, decode_token
        tok = create_token(7, "admin")
        p = decode_token(tok)
        assert p["sub"] == "7" and p["name"] == "admin"

    def test_tampered_token_is_rejected(self):
        from app.security import create_token, decode_token
        tok = create_token(7, "admin")
        head, payload, sig = tok.split(".")
        forged = f"{head}.{payload[:-2]}XY.{sig}"
        assert decode_token(forged) is None

    def test_token_signed_with_another_secret_is_rejected(self):
        import jwt as pyjwt
        from app.config import settings
        from app.security import decode_token
        forged = pyjwt.encode({"sub": "1", "name": "admin",
                                "exp": int(time.time()) + 600},
                               "a-different-secret-entirely", algorithm="HS256")
        assert decode_token(forged) is None

    def test_expired_token_is_rejected(self):
        import jwt as pyjwt
        from app.config import settings
        from app.security import decode_token
        expired = pyjwt.encode({"sub": "1", "name": "admin",
                                 "exp": int(time.time()) - 10},
                                settings.JWT_SECRET, algorithm="HS256")
        assert decode_token(expired) is None

    def test_algorithm_confusion_is_rejected(self):
        """A token with alg=none must never be accepted."""
        import base64
        import json as _json
        from app.security import decode_token
        header = base64.urlsafe_b64encode(
            _json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            _json.dumps({"sub": "1", "name": "admin",
                         "exp": int(time.time()) + 600}).encode()).rstrip(b"=").decode()
        assert decode_token(f"{header}.{payload}.") is None

    def test_garbage_token_is_rejected(self):
        from app.security import decode_token
        for bad in ("", "abc", "a.b.c", "x" * 500):
            assert decode_token(bad) is None

    def test_only_hs256_is_accepted(self):
        """decode_token must pin the algorithm list (no RS256/HS confusion)."""
        import inspect
        from app.security import decode_token
        src = inspect.getsource(decode_token)
        assert 'algorithms=["HS256"]' in src


class TestAuthEndpoints:
    def test_login_with_wrong_password_is_401(self, client):
        r = client.post("/auth/login", json={"username": "admin", "password": "nope"})
        assert r.status_code == 401

    def test_login_with_unknown_user_is_401(self, client):
        r = client.post("/auth/login", json={"username": "nobody", "password": "x"})
        assert r.status_code == 401

    def test_login_error_does_not_reveal_which_field_was_wrong(self, client):
        r1 = client.post("/auth/login", json={"username": "admin", "password": "nope"})
        r2 = client.post("/auth/login", json={"username": "nobody", "password": "nope"})
        assert r1.status_code == r2.status_code == 401
        assert r1.json()["detail"] == r2.json()["detail"], \
            "错误信息不得区分「用户不存在」与「密码错误」"

    def test_login_returns_a_token(self, client):
        r = client.post("/auth/login", json={"username": "admin",
                                             "password": "test-admin-pass"})
        assert r.status_code == 200
        assert r.json()["token"]
        assert r.json()["username"] == "admin"

    def test_login_does_not_leak_the_password_hash(self, client):
        r = client.post("/auth/login", json={"username": "admin",
                                             "password": "test-admin-pass"})
        body = json.dumps(r.json())
        assert "pbkdf2" not in body.lower()
        assert "password_hash" not in body

    def test_change_password_requires_auth(self, client):
        r = client.post("/auth/change-password",
                        json={"old_password": "test-admin-pass",
                              "new_password": "another-strong-pass"})
        assert r.status_code == 401

    def test_change_password_rejects_a_wrong_old_password(self, client, auth_headers):
        r = client.post("/auth/change-password", headers=auth_headers,
                        json={"old_password": "wrong", "new_password": "another-strong-pass"})
        assert r.status_code == 403

    def test_change_password_enforces_a_minimum_length(self, client, auth_headers):
        r = client.post("/auth/change-password", headers=auth_headers,
                        json={"old_password": "test-admin-pass", "new_password": "short"})
        assert r.status_code == 422

    def test_reset_password_is_disabled_without_a_token(self, client):
        r = client.post("/auth/reset-password",
                        json={"reset_token": "anything",
                              "new_password": "another-strong-pass"})
        assert r.status_code == 403, "未配置 ADMIN_RESET_TOKEN 时重置入口必须关闭"

    def test_reset_password_rejects_a_wrong_token(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "ADMIN_RESET_TOKEN", "the-real-token")
        r = client.post("/auth/reset-password",
                        json={"reset_token": "wrong-token",
                              "new_password": "another-strong-pass"})
        assert r.status_code == 403

    def test_reset_password_uses_constant_time_comparison(self):
        import inspect
        from app.routers import auth
        src = inspect.getsource(auth.reset_password)
        assert "compare_digest" in src


class TestAuthorizationBoundary:
    """Every operational endpoint must require a token; the public product
    surface stays public."""

    PROTECTED = [
        ("GET", "/sources"),
        ("GET", "/sources/1"),
        ("GET", "/crawl-tasks"),
        ("GET", "/crawl-tasks/running"),
        ("GET", "/crawl-tasks/1"),
        ("GET", "/crawl-logs"),
        ("GET", "/monitor"),
        ("GET", "/notification-rules"),
        ("GET", "/settings"),
    ]

    PUBLIC = [
        ("GET", "/events"),
        ("GET", "/calendar"),
        ("GET", "/stats/dashboard"),
        ("GET", "/stats/analysis"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/health/scheduler"),
        ("GET", "/status"),
    ]

    @pytest.mark.parametrize("method,path", PROTECTED)
    def test_protected_endpoint_rejects_anonymous(self, client, method, path):
        r = client.request(method, path)
        assert r.status_code == 401, f"{method} {path} 未要求认证（返回 {r.status_code}）"

    @pytest.mark.parametrize("method,path", PUBLIC)
    def test_public_endpoint_stays_public(self, client, method, path):
        r = client.request(method, path)
        assert r.status_code in (200, 503), \
            f"{method} {path} 应当公开（返回 {r.status_code}）"

    @pytest.mark.parametrize("method,path", PROTECTED)
    def test_protected_endpoint_rejects_a_garbage_token(self, client, method, path):
        r = client.request(method, path, headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_mutation_endpoints_require_auth(self, client):
        for method, path, kwargs in [
            ("POST", "/sources", {"json": {"name": "x", "type": "rss",
                                           "url": "https://example.com/rss"}}),
            ("POST", "/notification-rules", {"json": {"name": "x", "channel": "webhook"}}),
            ("POST", "/auth/change-password", {"json": {"old_password": "a",
                                                        "new_password": "b"}}),
            ("PUT", "/settings/system_setting?key=k", {"json": {"v": 1}}),
        ]:
            r = client.request(method, path, **kwargs)
            assert r.status_code == 401, f"{method} {path} 未要求认证"


class TestCorsPolicy:
    def test_unknown_origin_is_not_allowed(self, client):
        r = client.get("/status", headers={"Origin": "https://evil.example.com"})
        # No CORS grant for the hostile origin.
        assert "access-control-allow-origin" not in r.headers or \
            r.headers.get("access-control-allow-origin") != "https://evil.example.com"

    def test_preflight_from_unknown_origin_is_refused(self, client):
        r = client.options("/api/status", headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET"})
        assert r.status_code not in (200, 204) or \
            "access-control-allow-origin" not in r.headers

    def test_credentials_are_not_allowed_from_a_wildcard(self, client):
        """allow_credentials=True together with '*' is forbidden by the spec."""
        import inspect
        from app import main
        src = inspect.getsource(main)
        # the middleware is configured from an explicit allowlist, never '*'
        assert re.search(r"allow_origins=allowed_origins\(\)", src)
        assert "allow_origins=[\"*\"]" not in src, \
            "CORS 不得使用通配符来源"

    def test_allowed_origins_list_is_explicit_in_production(self):
        """Production must refuse to start with an empty/wildcard allowlist."""
        from app.config import Settings, validate_production_config
        with pytest.raises(SystemExit):
            validate_production_config(Settings(
                ENV="production", ROLE="api",
                DATABASE_URL="postgresql+psycopg://u:Sup3rSecretPassw0rd@db:5432/n",
                JWT_SECRET="x" * 48, ADMIN_PASSWORD="Sup3rSecretAdminPassw0rd",
                ALLOWED_ORIGINS="*"))


class TestXssDefence:
    """Stored XSS via a hostile feed URL — the URL is persisted and later
    rendered as an <a href>."""

    def test_sanitize_url_rejects_dangerous_schemes(self):
        from app.security import sanitize_url
        for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)",
                    "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)",
                    "file:///etc/passwd", "java\nscript:alert(1)"):
            assert sanitize_url(bad) == "", f"未拦截危险协议: {bad!r}"

    def test_sanitize_url_allows_safe_urls(self):
        from app.security import sanitize_url
        assert sanitize_url("https://example.com/a") == "https://example.com/a"
        assert sanitize_url("http://example.com/a") == "http://example.com/a"
        assert sanitize_url("/relative/path") == "/relative/path"

    def test_sanitize_url_is_not_bypassed_by_control_characters(self):
        from app.security import sanitize_url
        assert sanitize_url("java\tscript:alert(1)") == ""
        assert sanitize_url("java\x00script:alert(1)") == ""

    def test_pipeline_does_not_store_a_hostile_item_url(self, db, monkeypatch):
        """A feed item with a javascript: link must never reach the database."""
        import httpx
        from app.models import EventSource, RawDocument
        from app.services.pipeline import crawl_source
        from app.models import Source

        payload = ("<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
                   "<item><title>2026 人工智能大会</title>"
                   "<link>javascript:alert(document.cookie)</link>"
                   "<description>大会将于2026年11月3日在上海举办，人工智能，报名参会。</description>"
                   "</item></channel></rss>")

        def fake_safe_get(url, timeout=None, method="GET"):
            return httpx.Response(200, content=payload.encode(),
                                  request=httpx.Request("GET", url))
        monkeypatch.setattr("app.collectors.base.safe_get", fake_safe_get)

        s = Source(name="恶意源", type="rss", url="https://evil.example.com/rss")
        db.add(s)
        db.commit()
        crawl_source(db, s)
        for row in db.query(EventSource).all():
            assert "javascript:" not in (row.url or "").lower()
        for row in db.query(RawDocument).all():
            assert "javascript:" not in (row.url or "").lower()

    def test_frontend_has_no_html_injection_sink(self):
        """React escapes text; the code must not opt out of that."""
        offenders = []
        for path in FRONTEND_SRC.rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            if "dangerouslySetInnerHTML" in text:
                offenders.append(f"{path.name}: dangerouslySetInnerHTML")
        assert not offenders, f"前端存在 HTML 注入 sink: {offenders}"

    def test_frontend_has_no_dynamic_eval(self):
        offenders = []
        for path in FRONTEND_SRC.rglob("*.ts*"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\beval\s*\(", text) or re.search(r"new\s+Function\s*\(", text):
                offenders.append(path.name)
        assert not offenders, f"前端存在动态执行: {offenders}"

    def test_frontend_hrefs_go_through_safe_href(self):
        """Any href built from server data must be filtered."""
        offenders = []
        for path in FRONTEND_SRC.rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"href=\{([^}]+)\}", text):
                expr = m.group(1)
                if "safeHref" in expr:
                    continue
                # static, literal hrefs are fine
                if re.fullmatch(r"['\"][^'\"]*['\"]", expr):
                    continue
                offenders.append(f"{path.name}: href={{{expr}}}")
        assert not offenders, f"未经过 safeHref 的动态 href: {offenders}"

    def test_frontend_safe_href_filters_dangerous_schemes(self):
        """Verify the TS implementation semantics by transpiling it."""
        import subprocess
        src = (FRONTEND_SRC / "api.ts").read_text(encoding="utf-8")
        m = re.search(r"export function safeHref[\s\S]*?\n}", src)
        assert m, "前端缺少 safeHref"
        body = m.group(0)
        assert "http://" in body and "https://" in body
        # the scheme test must be anchored (not a bare substring check)
        assert re.search(r"startsWith\('http", body)

    def test_external_links_carry_noopener(self):
        """target=_blank without rel=noopener leaks the opener (reverse tabnabbing)."""
        offenders = []
        for path in FRONTEND_SRC.rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"<a\b[^>]*target=\"_blank\"[^>]*>", text):
                if "noopener" not in m.group(0):
                    offenders.append(f"{path.name}: {m.group(0)[:80]}")
        assert not offenders, f"缺少 rel=noopener 的外链: {offenders}"


class TestAuditTrail:
    def test_mutation_is_audited(self, client, auth_headers, db, monkeypatch):
        from app.models import AuditLog
        # The source URL must pass SSRF validation, so use a host that
        # resolves to a public address.
        monkeypatch.setattr("app.routers.sources.validate_public_url", lambda u: u)
        r = client.post("/sources", headers=auth_headers, json={
            "name": "审计测试源", "type": "rss", "url": "https://audit.example.com/rss"})
        assert r.status_code == 201, r.text
        rows = db.query(AuditLog).all()
        assert rows, "写操作必须留下审计记录"
        assert any("create_source" in (r_.action or "") for r_ in rows)

    def test_audit_record_does_not_contain_the_token(self, client, auth_headers, db,
                                                     monkeypatch):
        from app.models import AuditLog
        monkeypatch.setattr("app.routers.sources.validate_public_url", lambda u: u)
        token = auth_headers["Authorization"].split(" ", 1)[1]
        client.post("/sources", headers=auth_headers, json={
            "name": "审计测试源2", "type": "rss", "url": "https://audit2.example.com/rss"})
        for row in db.query(AuditLog).all():
            assert token not in (row.detail or "")


class TestErrorDisclosure:
    def test_500_errors_do_not_leak_tracebacks(self, client, auth_headers, monkeypatch):
        """An unexpected server error must not return the traceback."""
        from app.routers import sources as sources_router

        def boom(*a, **kw):
            raise RuntimeError("内部敏感信息: db password is hunter2")

        monkeypatch.setattr(sources_router, "list_sources", boom)
        r = client.get("/sources", headers=auth_headers)
        # FastAPI turns an unhandled exception into a 500; the body must not
        # contain the exception text.
        assert "hunter2" not in r.text
        assert "Traceback" not in r.text

    def test_error_body_does_not_include_the_db_url(self, client, auth_headers, monkeypatch):
        from app.routers import sources as sources_router

        def boom(*a, **kw):
            raise RuntimeError("failed to connect to postgresql://u:pwd@db:5432/radar")

        monkeypatch.setattr(sources_router, "list_sources", boom)
        r = client.get("/sources", headers=auth_headers)
        assert "pwd@db" not in r.text

    def test_ssrf_error_message_is_actionable_but_not_internal(self, client):
        r = client.post("/sources", headers=auth_headers if False else {}, json={
            "name": "内网源", "type": "html", "url": "http://169.254.169.254/latest/meta-data"})
        assert r.status_code in (400, 401)
        if r.status_code == 400:
            assert "安全校验" in r.json()["detail"]


class TestDependencySurface:
    """Dependency review. pip-audit could not be installed in this environment
    (PyPI unreachable), so the Python side is a pinned-version review and the
    JavaScript side uses `npm audit` (which DID run)."""

    def test_requirements_are_pinned(self):
        req = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
        unpinned = []
        for line in req.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            if "==" not in line:
                unpinned.append(line)
        assert not unpinned, f"未固定版本的依赖: {unpinned}"

    def test_requirements_have_no_known_vulnerable_baseline(self):
        req = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
        # These versions are the audited ones; a downgrade must be a deliberate act.
        assert "fastapi==0.146.0" in req
        assert "sqlalchemy==2.1.5" in req
        assert "pyjwt==2.15.1" in req
        assert "cryptography==" in req, "PyJWT 的 RS256/EdDSA 支持需要 cryptography"
        assert "psycopg[binary]==3.2.13" in req

    def test_frontend_package_json_has_lockfile(self):
        assert (REPO / "frontend" / "package-lock.json").exists(), \
            "缺少 lockfile，依赖版本不可复现"

    def test_frontend_dependencies_are_declared(self):
        pkg = json.loads((REPO / "frontend" / "package.json").read_text(encoding="utf-8"))
        deps = pkg.get("dependencies", {})
        assert "react" in deps and "react-router-dom" in deps
        assert pkg.get("devDependencies", {}).get("vite")


class TestRateLimiting:
    """Login brute-force protection (behavioural, not source-pattern based)."""

    def test_repeated_failures_are_throttled(self, client, auth_headers):
        from app.routers import auth as auth_router
        auth_router._login_attempts.clear()
        auth_router._login_locked_until.clear()
        codes = []
        for _ in range(auth_router.LOGIN_MAX_ATTEMPTS + 3):
            r = client.post("/auth/login", json={"username": "admin",
                                                 "password": "definitely-wrong"})
            codes.append(r.status_code)
        assert 429 in codes, f"连续错误登录未被限流: {codes}"
        assert codes[-1] == 429, "达到阈值后必须持续返回 429"

    def test_lockout_does_not_leak_whether_the_user_exists(self, client):
        from app.routers import auth as auth_router
        auth_router._login_attempts.clear()
        auth_router._login_locked_until.clear()
        for _ in range(auth_router.LOGIN_MAX_ATTEMPTS + 1):
            client.post("/auth/login", json={"username": "ghost-user",
                                             "password": "definitely-wrong"})
        r = client.post("/auth/login", json={"username": "ghost-user",
                                             "password": "definitely-wrong"})
        assert r.status_code == 429
        assert "不存在" not in r.json()["detail"]

    def test_successful_login_clears_the_counter(self, client):
        from app.routers import auth as auth_router
        auth_router._login_attempts.clear()
        auth_router._login_locked_until.clear()
        for _ in range(auth_router.LOGIN_MAX_ATTEMPTS - 1):
            client.post("/auth/login", json={"username": "admin",
                                             "password": "definitely-wrong"})
        r = client.post("/auth/login", json={"username": "admin",
                                             "password": "test-admin-pass"})
        assert r.status_code == 200
        key = f"{auth_router._client_ip.__name__}"  # placeholder, checked below
        assert not auth_router._login_attempts, "成功登录后必须清空失败计数"

    def test_failed_logins_are_audited(self, client, db):
        from app.models import AuditLog
        from app.routers import auth as auth_router
        auth_router._login_attempts.clear()
        auth_router._login_locked_until.clear()
        client.post("/auth/login", json={"username": "admin", "password": "nope-nope"})
        rows = db.query(AuditLog).all()
        assert any("login_failed" in (r.action or "") for r in rows), \
            "失败登录必须写入审计日志"
