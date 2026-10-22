"""Regression tests for strict public-address classification (audit finding #2).

The defect
----------
`is_public_ip()` relied on `ipaddress.is_global` plus a partial blocklist.
Python's `is_global` reports a number of IANA special-purpose ranges as
globally routable, so those addresses were ALLOWED by the SSRF guard:

    2001:1::1        IETF protocol assignments (PCP anycast)
    2001:20::1       ORCHIDv2 (RFC 7343)
    5f00::1          SRv6 SIDs (RFC 9602)
    fec0::1          site-local (RFC 3879, deprecated)
    2620:fe::fe      6bone anycast (RFC 3701, deprecated)

There was also an ORDERING defect: the explicit IPv6 blocklist was consulted
AFTER the 6to4/Teredo embedding logic, which made the `2002::/16` (6to4) and
`2001::/32` (Teredo) entries dead code — a 6to4 address embedding a *public*
IPv4 (2002:0808:0808::) was allowed through.
"""
import socket

import pytest

from app import security
from app.security import SSRFError, is_public_ip, validate_public_url


# --------------------------------------------------------------------------
# 1. Every non-public address must be denied
# --------------------------------------------------------------------------
MUST_DENY = [
    # --- IPv4 loopback / private / link-local / special ---
    "127.0.0.1", "127.255.255.254", "0.0.0.0", "0.1.2.3",
    "10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.255.254",
    "192.168.0.1", "192.168.255.254",
    "169.254.0.1", "169.254.169.254",          # link-local / cloud metadata
    "100.64.0.1", "100.127.255.254",           # CGNAT
    "192.0.0.1", "192.0.0.9", "192.0.0.10",    # IETF protocol assignments
    "192.0.2.1", "198.51.100.1", "203.0.113.1",  # TEST-NETs
    "192.31.196.1", "192.52.193.1", "192.175.48.1",  # AS112 / AMT
    "192.88.99.1",                            # 6to4 relay anycast
    "198.18.0.1", "198.19.255.254",           # benchmarking
    "224.0.0.1", "239.0.0.1", "233.252.0.1",   # multicast / MCAST test
    "240.0.0.1", "255.255.255.255",           # reserved / broadcast
    # --- IPv6 loopback / unspecified / unique-local / link-local ---
    "::1", "::", "fc00::1", "fd00::1", "fe80::1", "ff02::1",
    "100::1",                                  # discard-only
    "2001:db8::1",                             # documentation
    "3fff::1",                                 # documentation (RFC 9637)
    # --- IPv4-mapped IPv6 ---
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
    "::ffff:192.168.1.1", "::ffff:0.0.0.0",
    # --- 6to4 wrapping a private IPv4 ---
    "2002:7f00:0001::", "2002:0a00:0001::", "2002:a9fe:a9fe::",
    # --- 6to4 wrapping a PUBLIC IPv4 (the ordering defect) ---
    "2002:0808:0808::",
    # --- Teredo ---
    "2001:0000:4136:e378:8000:63bf:3fff:fdd2",
    # --- NAT64 wrapping private IPv4 ---
    "64:ff9b::7f00:1", "64:ff9b::a00:1", "64:ff9b:1::1",
    # --- IANA special-purpose that Python still calls "global" ---
    "2001:1::1", "2001:2::1", "2001:3::1", "2001:4:112::1",
    "2001:10::1", "2001:20::1", "2001:30::1",
    "5f00::1", "5f00::ffff:ffff:ffff:ffff:ffff:ffff:ffff",
    "fec0::1", "feff::1",
    "2620:fe::fe", "2620:4f:8000::1",
]


@pytest.mark.parametrize("ip", MUST_DENY)
def test_non_public_addresses_are_denied(ip):
    assert is_public_ip(ip) is False, f"{ip} must not be treated as public"


# --------------------------------------------------------------------------
# 2. Genuinely public addresses must still be allowed (no false positives)
# --------------------------------------------------------------------------
MUST_ALLOW = [
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "142.250.185.78",
    "2606:4700:4700::1111", "2001:4860:4860::8888",
    "2a00:1450:4001:81b::200e",
    # Windows returns IPv4-mapped IPv6 for ordinary IPv4 hosts — these MUST
    # be allowed, otherwise all real IPv4 crawling would break.
    "::ffff:8.8.8.8", "::ffff:93.184.216.34",
]


@pytest.mark.parametrize("ip", MUST_ALLOW)
def test_public_addresses_are_allowed(ip):
    assert is_public_ip(ip) is True, f"{ip} must be treated as public"


# --------------------------------------------------------------------------
# 3. Garbage input
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "", "not-an-ip", "999.999.999.999", "1.2.3", "0x7f000001",
    "::ffff:999.999.999.999", "12345::", "  ", "1.2.3.4.5",
])
def test_malformed_input_is_denied(bad):
    assert is_public_ip(bad) is False


# --------------------------------------------------------------------------
# 4. The blocklist must not be bypassable by the embedding logic
# --------------------------------------------------------------------------
class TestBlocklistPrecedence:
    def test_6to4_of_public_ipv4_is_still_denied(self):
        """2002::/16 is entirely non-routable; the embedding check must not
        let a public-IPv4-carrying 6to4 address through."""
        assert is_public_ip("2002:0808:0808::") is False

    def test_teredo_block_is_denied(self):
        assert is_public_ip("2001:0000:4136:e378:8000:63bf:3fff:fdd2") is False

    def test_ipv4_mapped_public_is_allowed_but_private_is_denied(self):
        """The ::ffff:0:0/96 range must not be blanket-blocked."""
        assert is_public_ip("::ffff:8.8.8.8") is True
        assert is_public_ip("::ffff:127.0.0.1") is False

    def test_every_blocked_v6_network_actually_blocks(self):
        """Each entry in BLOCKED_V6 must contain at least one denied address."""
        import ipaddress
        for net in security.BLOCKED_V6:
            n = ipaddress.ip_network(net)
            probe = n.network_address
            assert is_public_ip(str(probe)) is False, \
                f"blocklist entry {net} does not block its own network address"

    def test_every_blocked_v4_network_actually_blocks(self):
        import ipaddress
        for net in security.BLOCKED_V4:
            n = ipaddress.ip_network(net)
            probe = n.network_address
            assert is_public_ip(str(probe)) is False, \
                f"blocklist entry {net} does not block its own network address"


# --------------------------------------------------------------------------
# 5. Mixed DNS results: ANY non-public address rejects the whole target
# --------------------------------------------------------------------------
class TestMixedDnsResults:
    """A hostname resolving to both a public and a private address must be
    rejected outright — otherwise the attacker controls which address the
    client happens to pick."""

    def _install_fake_resolver(self, monkeypatch, results):
        """Patch socket.getaddrinfo itself.

        `validate_public_url()` calls `socket.getaddrinfo()` directly, so
        patching the module-level `_orig_getaddrinfo` seam would NOT affect
        it — the test would then pass for the wrong reason (a real DNS
        failure raising SSRFError) instead of exercising the mixed-result
        logic. Patching the stdlib function is what actually controls the
        addresses the guard sees.
        """
        def fake(host, port, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                    for ip in results]
        monkeypatch.setattr(socket, "getaddrinfo", fake)

    def test_validate_public_url_rejects_mixed_results(self, monkeypatch):
        self._install_fake_resolver(monkeypatch, ["93.184.216.34", "127.0.0.1"])
        with pytest.raises(SSRFError):
            validate_public_url("https://mixed.example.com/path")

    def test_validate_public_url_rejects_mixed_with_metadata(self, monkeypatch):
        self._install_fake_resolver(monkeypatch, ["93.184.216.34", "169.254.169.254"])
        with pytest.raises(SSRFError):
            validate_public_url("https://mixed.example.com/path")

    def test_validate_public_url_rejects_private_only(self, monkeypatch):
        self._install_fake_resolver(monkeypatch, ["10.0.0.5"])
        with pytest.raises(SSRFError):
            validate_public_url("https://private.example.com/path")

    def test_validate_public_url_accepts_all_public(self, monkeypatch):
        self._install_fake_resolver(monkeypatch, ["93.184.216.34", "1.1.1.1"])
        assert validate_public_url("https://good.example.com/path") == \
            "https://good.example.com/path"

    def test_guarded_resolver_rejects_mixed_results(self, monkeypatch):
        """The installed guard must reject a mixed public/private DNS answer.

        The guard's inner resolver is the module-level `_orig_getaddrinfo`
        seam (captured at install time), so that is what must be faked here —
        `socket.getaddrinfo` itself is replaced BY the guard.
        """
        results = ["93.184.216.34", "127.0.0.1"]

        def fake(host, port, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
                    for ip in results]

        monkeypatch.setattr(security, "_orig_getaddrinfo", fake)
        security.install_guarded_resolver()
        try:
            with pytest.raises(SSRFError):
                socket.getaddrinfo("mixed.example.com", 443)
        finally:
            security.uninstall_guarded_resolver()


# --------------------------------------------------------------------------
# 6. Per-hop revalidation in the collector path
# --------------------------------------------------------------------------
class TestPerHopRevalidation:
    """A redirect must be re-validated at every hop, not only before the
    first request. Otherwise `https://public.example/redirect` can bounce the
    client to http://169.254.169.254/ (cloud metadata)."""

    def _install_fake_client(self, monkeypatch, hops):
        """Drive safe_get with a fake transport returning the given hops.

        hops: list of (status_code, location_header_or_None)
        Returns the list of URLs the client actually requested.
        """
        import httpx
        from app.collectors import base as collector_base

        requested = []

        def fake_send(req, **kw):
            requested.append(str(req.url))
            idx = min(len(requested) - 1, len(hops) - 1)
            status, location = hops[idx]
            headers = {"location": location} if location else {}
            return httpx.Response(status, headers=headers, request=req)

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def build_request(self, method, url, headers=None):
                return httpx.Request(method, url, headers=headers or {})

            def send(self, req, **kw):
                return fake_send(req, **kw)

        monkeypatch.setattr(collector_base.httpx, "Client", FakeClient)
        monkeypatch.setattr(collector_base, "_throttle", lambda url: None)

        def fake_validate(url):
            if "169.254.169.254" in url or "10.0.0.5" in url:
                raise SSRFError("禁止访问内网/保留地址")
            return url

        monkeypatch.setattr(collector_base, "validate_public_url", fake_validate)
        return requested

    def test_redirect_to_metadata_is_blocked(self, monkeypatch):
        from app.collectors import base as collector_base
        requested = self._install_fake_client(
            monkeypatch, [(302, "http://169.254.169.254/latest/meta-data/")])
        with pytest.raises(collector_base.CollectorError) as e:
            collector_base.safe_get("https://public.example/redirect")
        assert "SSRF" in str(e.value)
        # the internal target must never have been requested
        assert all("169.254.169.254" not in u for u in requested)

    def test_redirect_to_private_address_is_blocked(self, monkeypatch):
        from app.collectors import base as collector_base
        self._install_fake_client(monkeypatch, [(302, "http://10.0.0.5/admin")])
        with pytest.raises(collector_base.CollectorError) as e:
            collector_base.safe_get("https://public.example/redirect")
        assert "SSRF" in str(e.value)

    def test_safe_get_validates_every_hop(self):
        """The validation call must sit INSIDE the redirect loop.

        A single textual occurrence is correct here: the call is inside a
        `for hop in range(...)` loop, so it runs once per hop. The assertion
        checks the loop structure instead of counting occurrences.
        """
        import inspect
        from app.collectors import base as collector_base
        src = inspect.getsource(collector_base.safe_get)
        loop_at = src.index("for hop in range")
        call_at = src.index("validate_public_url(current)")
        assert call_at > loop_at, \
            "validate_public_url must be called inside the redirect loop"


# --------------------------------------------------------------------------
# 7. Hostname policy
# --------------------------------------------------------------------------
class TestHostnamePolicy:
    @pytest.mark.parametrize("url", [
        "http://localhost/", "http://localhost:8080/",
        "http://metadata.google.internal/",
        "http://metadata/",
        "http://foo.internal/", "http://foo.local/",
        "http://foo.localhost/", "http://LOCALHOST/",
    ])
    def test_internal_hostnames_are_rejected(self, url):
        with pytest.raises(SSRFError):
            validate_public_url(url)

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/",
        "data:text/html,<script>", "javascript:alert(1)",
        "http://user:pass@example.com/",
    ])
    def test_bad_schemes_and_userinfo_are_rejected(self, url):
        with pytest.raises(SSRFError):
            validate_public_url(url)

    @pytest.mark.parametrize("url", ["", None, 123])
    def test_empty_input_is_rejected(self, url):
        with pytest.raises(SSRFError):
            validate_public_url(url)
