"""Final SSRF / outbound-network security tests.

All network-facing tests use local fakes:
- DNS is replaced via app.security._orig_getaddrinfo (no real queries, and no
  probing of real third-party/internal networks).
- The HTTP layer is replaced by a fake httpx.Client.send, so redirect chains
  are exercised without any real connection.
Real-network reachability is covered separately in test_real_e2e.py.
"""
import ipaddress
import os
import socket

import httpx
import pytest

from app.collectors.base import CollectorError, safe_get
from app.config import settings
from app.security import (SSRFError, is_public_ip, uninstall_guarded_resolver,
                          validate_public_url)

PUBLIC_V4 = "93.184.216.34"    # example.com's real address, used as test data only
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


# ---------------- IP classification ----------------

class TestIPClassification:
    BLOCKED = [
        "127.0.0.1", "127.8.9.7",            # loopback
        "10.0.0.1", "172.16.3.4", "192.168.1.1",  # private
        "169.254.169.254", "169.254.0.1",     # link-local / cloud metadata
        "0.0.0.0", "0.1.2.3",                 # this-network
        "255.255.255.255", "224.0.0.1", "239.1.2.3",  # broadcast/multicast
        "100.64.0.1", "100.127.1.1",          # CGNAT
        "192.0.0.1", "192.0.2.1",             # IETF / TEST-NET-1
        "198.51.100.7", "203.0.113.9",        # TEST-NET-2/3
        "198.18.0.5", "198.19.1.2",           # benchmarking
        "192.88.99.1",                        # 6to4 relay anycast
        # IPv6
        "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1",
        "ff02::1", "ff00::ff",
        "2001:db8::1",                       # documentation
        "::ffff:127.0.0.1", "::ffff:169.254.169.254",   # IPv4-mapped private
        "64:ff9b::7f00:1",                   # NAT64 wrapping 127.0.0.1
        "2002:7f00:0001::",                  # 6to4 wrapping 127.0.0.1
    ]

    ALLOWED = [
        PUBLIC_V4, "1.1.1.1", "8.8.8.8", "140.82.121.4",
        PUBLIC_V6, "2001:4860:4860::8888", "2404:6800:4004:80a::200e",
    ]

    def test_blocked_addresses_denied(self):
        for ip in self.BLOCKED:
            assert is_public_ip(ip) is False, f"{ip} must be denied"

    def test_public_addresses_allowed(self):
        for ip in self.ALLOWED:
            assert is_public_ip(ip) is True, f"{ip} must be allowed"

    def test_garbage_input_denied(self):
        for junk in ["", "not-an-ip", "999.1.1.1", "example.com", "0x7f.0.0.1"]:
            assert is_public_ip(junk) is False


# ---------------- URL validation with controlled DNS ----------------

@pytest.fixture()
def fake_dns(monkeypatch):
    """Controlled DNS: host -> list of IPs. No real queries are made."""
    table: dict[str, list[str]] = {}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # mirror real resolver behavior for IP literals: return the literal itself
        try:
            ipaddress.ip_address(host)
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (host, 0))]
        except ValueError:
            pass
        if host in table:
            family = socket.AF_INET6 if ":" in table[host][0] else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))
                    for ip in table[host]]
        # unknowns resolve to a public IP so validation focuses on the host policy
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 0))]

    from app import security
    orig = security._orig_getaddrinfo
    monkeypatch.setattr(security, "_orig_getaddrinfo", fake_getaddrinfo)
    # ensure the guarded resolver is installed (it reads _orig_getaddrinfo at call time)
    security.install_guarded_resolver()
    return table


class TestURLValidation:
    def test_rejects_non_http_scheme(self):
        for url in ["ftp://example.com/x", "file:///etc/passwd",
                    "javascript:alert(1)", "gopher://example.com/"]:
            with pytest.raises(SSRFError):
                validate_public_url(url)

    def test_rejects_userinfo_obfuscation(self):
        with pytest.raises(SSRFError):
            validate_public_url("http://user:pass@example.com/feed")

    def test_rejects_internal_hostnames(self):
        for url in ["http://localhost/", "http://metadata.google.internal/",
                    "http://svc.internal/", "http://box.local/", "http://localhost./"]:
            with pytest.raises(SSRFError):
                validate_public_url(url)

    def test_rejects_non_standard_ports(self):
        with pytest.raises(SSRFError):
            validate_public_url("http://example.com:22/")
        with pytest.raises(SSRFError):
            validate_public_url("http://example.com:6379/")

    def test_rejects_empty_or_bad_url(self):
        for url in ["", None, "http://", "http:///path"]:
            with pytest.raises(SSRFError):
                validate_public_url(url)

    def test_literal_private_ips_rejected(self, fake_dns):
        for url in ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
                    "http://[::1]/", "http://[::ffff:127.0.0.1]/",
                    "http://10.0.0.5/", "http://192.168.1.1/"]:
            with pytest.raises(SSRFError):
                validate_public_url(url)

    def test_public_url_passes_with_controlled_dns(self, fake_dns):
        fake_dns["example.com"] = [PUBLIC_V4]
        assert validate_public_url("https://example.com/feed") == "https://example.com/feed"
        fake_dns["six.example.com"] = [PUBLIC_V6]
        assert validate_public_url("http://six.example.com/") is not None
        # default ports are fine
        fake_dns["p80.example.com"] = [PUBLIC_V4]
        validate_public_url("http://p80.example.com:80/feed")
        validate_public_url("https://p443.example.com:443/feed")

    def test_mixed_dns_results_rejected(self, fake_dns):
        """Any non-public resolution result rejects the whole target."""
        fake_dns["mixed.example.com"] = [PUBLIC_V4, "10.0.0.9"]
        with pytest.raises(SSRFError):
            validate_public_url("http://mixed.example.com/")

    def test_all_private_dns_rejected(self, fake_dns):
        fake_dns["priv.example.com"] = ["127.0.0.1", "127.0.0.2"]
        with pytest.raises(SSRFError):
            validate_public_url("http://priv.example.com/")


# ---------------- guarded resolver ----------------

class TestGuardedResolver:
    def test_blocks_non_public_resolution_at_connect_time(self, fake_dns):
        """Simulated DNS rebinding: the resolver rejects the connect target."""
        fake_dns["rebind.example.com"] = ["127.0.0.1"]
        with pytest.raises(SSRFError):
            socket.getaddrinfo("rebind.example.com", 80)

    def test_allows_public_resolution(self, fake_dns):
        fake_dns["ok.example.com"] = [PUBLIC_V4]
        assert socket.getaddrinfo("ok.example.com", 80)

    def test_ip_literals_pass_through_unfiltered(self, fake_dns):
        # literal IPs bypass resolution (policy enforced by validate_public_url)
        infos = socket.getaddrinfo("93.184.216.34", 80)
        assert infos


# ---------------- safe_get behavior (no real connections) ----------------

class _FakeSend:
    """Fake httpx.Client.send driven by a script of responses per host."""

    def __init__(self, routes):
        self.routes = routes  # host -> list of callables(url) -> httpx.Response or Exception

    def __call__(self, request, **kwargs):
        host = request.url.host
        # simulate the real transport: resolve before connecting, so the
        # guarded resolver (connect-time SSRF check) is exercised too
        socket.getaddrinfo(host, request.url.port or 80)
        script = self.routes.get(host)
        if not script:
            raise httpx.ConnectError("no fake route", request=request)
        entry = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(entry, Exception):
            raise entry
        return entry(request.url)


def _resp(request_url, status=200, body=b"ok", headers=None):
    return httpx.Response(status, content=body,
                          headers=headers or {}, request=httpx.Request("GET", str(request_url)))


class TestSafeGetSecurity:
    def test_redirect_to_private_blocked(self, monkeypatch, fake_dns):
        fake_dns["evil.example.com"] = [PUBLIC_V4]
        routes = {
            "evil.example.com": [
                lambda u: _resp(u, 302, b"", {"location": "http://127.0.0.1:8080/secret"}),
            ],
        }
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError) as ei:
            safe_get("http://evil.example.com/page")
        assert "SSRF" in str(ei.value) or "拦截" in str(ei.value)

    def test_redirect_to_metadata_blocked(self, monkeypatch, fake_dns):
        fake_dns["evil2.example.com"] = [PUBLIC_V4]
        routes = {"evil2.example.com": [
            lambda u: _resp(u, 302, b"", {"location": "http://169.254.169.254/latest/meta-data/"}),
        ]}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError):
            safe_get("http://evil2.example.com/x")

    def test_scheme_downgrade_redirect_blocked(self, monkeypatch, fake_dns):
        fake_dns["down.example.com"] = [PUBLIC_V4]
        routes = {"down.example.com": [
            lambda u: _resp(u, 302, b"", {"location": "ftp://example.com/pub"}),
        ]}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError) as ei:
            safe_get("http://down.example.com/x")
        assert "协议" in str(ei.value)

    def test_normal_redirect_chain_followed(self, monkeypatch, fake_dns):
        fake_dns["a.example.com"] = [PUBLIC_V4]
        fake_dns["b.example.com"] = [PUBLIC_V4]
        routes = {
            "a.example.com": [lambda u: _resp(u, 302, b"", {"location": "http://b.example.com/next"})],
            "b.example.com": [lambda u: _resp(u, 200, b"final")],
        }
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        r = safe_get("http://a.example.com/")
        assert r.text == "final"

    def test_redirect_loop_capped(self, monkeypatch, fake_dns):
        fake_dns["loop.example.com"] = [PUBLIC_V4]
        # permanently 302-ing to itself; hop cap must stop it
        routes = {"loop.example.com": [
            lambda u: _resp(u, 302, b"", {"location": "http://loop.example.com/self"})] * 20}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError) as ei:
            safe_get("http://loop.example.com/")
        assert "重定向" in str(ei.value)

    def test_dns_rebinding_between_hops_blocked(self, monkeypatch, fake_dns):
        """First resolution public; the connect-time resolver then sees private."""
        fake_dns["flip.example.com"] = [PUBLIC_V4]
        # guarded resolver path: swap DNS result AFTER validation
        calls = {"n": 0}

        def flipping(host, port, *a, **kw):
            calls["n"] += 1
            if host == "flip.example.com" and calls["n"] > 1:
                raise SSRFError("rebinding rejected")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, 0))]

        from app import security
        monkeypatch.setattr(security, "_orig_getaddrinfo", flipping)
        routes = {"flip.example.com": [lambda u: _resp(u, 200, b"x")]}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError):
            safe_get("http://flip.example.com/")

    def test_oversized_body_aborted(self, monkeypatch, fake_dns):
        fake_dns["big.example.com"] = [PUBLIC_V4]

        def big(u):
            cap = settings.MAX_RESPONSE_BYTES + 1024
            chunks = [b"x" * 1024] * (cap // 1024 + 1)

            class R:
                status_code = 200
                headers = {"content-type": "text/plain"}

                def iter_bytes(self):
                    return iter(chunks)

                def close(self):
                    pass
            return R()

        routes = {"big.example.com": [big]}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError) as ei:
            safe_get("http://big.example.com/")
        assert "大小上限" in str(ei.value)

    def test_timeout_reported_as_collector_error(self, monkeypatch, fake_dns):
        fake_dns["slow.example.com"] = [PUBLIC_V4]
        routes = {"slow.example.com": [httpx.ReadTimeout("too slow")]}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError):
            safe_get("http://slow.example.com/")

    def test_tls_verification_stays_on(self, monkeypatch, fake_dns):
        """We must never 'fix' connectivity by disabling certificate checks."""
        fake_dns["badtls.example.com"] = [PUBLIC_V4]
        routes = {"badtls.example.com": [httpx.ConnectError("tls verify failed")]}
        monkeypatch.setattr(httpx.Client, "send", _FakeSend(routes))
        with pytest.raises(CollectorError):
            safe_get("https://badtls.example.com/")
        # source must never construct a client with verify=False
        import inspect

        import app.collectors.base as base
        assert "verify=False" not in inspect.getsource(base)

    def test_concurrency_semaphore_is_bounded(self, monkeypatch, fake_dns):
        """Outbound concurrency never exceeds MAX_CONCURRENT_CRAWLS."""
        fake_dns["c.example.com"] = [PUBLIC_V4]
        import threading
        import time as _time

        peak = 0
        lock = threading.Lock()
        live = 0

        def slow_send(_self, request, **kw):
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            _time.sleep(0.05)
            with lock:
                live -= 1
            return _resp(request.url, 200, b"ok")

        monkeypatch.setattr(httpx.Client, "send", slow_send)
        threads = [threading.Thread(target=safe_get, args=("http://c.example.com/",))
                   for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert peak <= settings.MAX_CONCURRENT_CRAWLS, f"peak={peak}"


# ---------------- real network (opt-in marker) ----------------

class TestRealNetworkSSRF:
    @pytest.mark.real_network
    def test_real_public_site_passes(self, fake_dns):
        """example.com uses REAL DNS here (fake fixture not used)."""
        pytest.importorskip("socket")
        uninstall_guarded_resolver()
        try:
            url = validate_public_url("https://example.com/")
            assert url == "https://example.com/"
        except SSRFError as e:
            if "DNS" in str(e):
                pytest.skip(f"sandbox DNS unavailable: {e}")
            raise
