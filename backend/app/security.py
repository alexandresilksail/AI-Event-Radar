"""Security utilities: password hashing (PBKDF2), JWT, SSRF guard.

SSRF defense-in-depth (P0 final hardening):
1. `validate_public_url()` — full URL policy check before any request:
   scheme allowlist, userinfo rejection, hostname policy, port allowlist,
   DNS resolution with ALL results required to be public.
2. `install_guarded_resolver()` — wraps socket.getaddrinfo so that the IPs
   actually used by connections are public. If DNS returns ANY non-public
   result (mixed A/AAAA), the target is rejected outright. This closes the
   TOCTOU window between validation and connect (DNS rebinding).
3. Per-request + per-hop re-validation lives in collectors.base.safe_get.
"""
import hashlib
import hmac
import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import jwt

from .config import settings


class SSRFError(Exception):
    pass


# ---------- comprehensive non-routable / special-purpose ranges ----------
# Explicit lists (auditable) + `ip.is_global` backstop. An address is public
# only if it is NOT in any blocklist AND is globally routable.
BLOCKED_V4 = [
    "0.0.0.0/8",          # "this" network
    "10.0.0.0/8",         # private
    "100.64.0.0/10",      # CGNAT
    "127.0.0.0/8",        # loopback
    "169.254.0.0/16",     # link-local (incl. 169.254.169.254 cloud metadata)
    "172.16.0.0/12",      # private
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # TEST-NET-1
    "192.88.99.0/24",     # 6to4 relay anycast (deprecated)
    "192.168.0.0/16",     # private
    "198.18.0.0/15",      # benchmarking
    "198.51.100.0/24",    # TEST-NET-2
    "203.0.113.0/24",     # TEST-NET-3
    "224.0.0.0/4",        # multicast
    "240.0.0.0/4",        # reserved (incl. broadcast 255.255.255.255)
]
BLOCKED_V6 = [
    "::/128",             # unspecified
    "::1/128",            # loopback
    "::ffff:0:0/96",      # IPv4-mapped (also handled via .ipv4_mapped)
    "64:ff9b::/96",       # NAT64 (can embed private v4)
    "100::/64",           # discard-only
    "2001:db8::/32",      # documentation
    "fc00::/7",           # unique local
    "fe80::/10",          # link-local
    "ff00::/8",           # multicast
    "2002::/16",          # 6to4 (can embed private v4)
    "2001::/32",          # Teredo (can embed private v4)
]
_NETS_V4 = [ipaddress.ip_network(n) for n in BLOCKED_V4]
_NETS_V6 = [ipaddress.ip_network(n) for n in BLOCKED_V6]

# hosts that must never be fetched even if DNS misbehaves
BLOCKED_HOSTNAMES = {
    "localhost", "metadata", "metadata.google.internal",
    "metadata.goog", "instance-data",
}

_NATIVE_GETADDRINFO = socket.getaddrinfo  # pristine native resolver (never reassigned)
_orig_getaddrinfo = socket.getaddrinfo     # swappable seam: tests inject a fake resolver here
_RESOLVER_INSTALLED = False


def trusted_internal_hosts() -> set[str]:
    """Operator-configured endpoints that are allowed to live on a private network.

    The global resolver guard rejects ANY hostname that resolves to a non-public
    address. That is correct for user-supplied crawl targets, but it would also
    break operator-configured integrations that legitimately live inside a
    private network (corporate SMTP relay, a self-hosted LLM gateway, ...).

    Those endpoints are trusted because they come from the deployment
    configuration (env vars), never from user input. They are NOT crawl
    targets: every user-supplied URL is still validated by
    `validate_public_url()`, which performs its own DNS + public-IP check
    independently of this global guard.
    """
    hosts: set[str] = set()
    for part in (settings.TRUSTED_INTERNAL_HOSTS or "").split(","):
        p = part.strip().lower()
        if p:
            hosts.add(p)
    smtp = (settings.SMTP_HOST or "").strip()
    if smtp:
        hosts.add(smtp.split(":")[0].strip().lower())
    if settings.AI_BASE_URL:
        try:
            h = urlparse(settings.AI_BASE_URL).hostname
            if h:
                hosts.add(h.lower())
        except Exception:
            pass
    hosts.discard("")
    return hosts


def is_public_ip(ip_str: str) -> bool:
    """True only for globally-routable unicast addresses.

    Handles IPv4-mapped IPv6, 6to4/Teredo/NAT64 wrappers, TEST-NETs,
    multicast/broadcast, and any address Python's ipaddress considers
    non-global. Anything ambiguous is denied.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version == 6:
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return is_public_ip(str(mapped))          # ::ffff:127.0.0.1 -> v4 check
        sixtofour = getattr(ip, "sixtofour", None)
        if sixtofour is not None:
            return is_public_ip(str(sixtofour))
        teredo = getattr(ip, "teredo", None)
        if teredo is not None:                          # (server, client) tuple
            return all(is_public_ip(str(t)) for t in teredo if t)
        if any(ip in n for n in _NETS_V6):
            return False
        return bool(ip.is_global)
    if any(ip in n for n in _NETS_V4):
        return False
    # is_global excludes 0.0.0.0, broadcast, RFC1918, documentation, etc.
    return bool(ip.is_global)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def install_guarded_resolver() -> None:
    """Wrap socket.getaddrinfo so that DNS results actually used by connections
    are filtered to public IPs. Closes the TOCTOU window between validation and
    connect (DNS rebinding / per-hop re-resolution races).

    Policy: if a hostname resolves to ANY non-public address, the target is
    rejected entirely (a mixed A/AAAA set is treated as untrusted).
    Operator-configured private endpoints (see `trusted_internal_hosts`) are
    exempt — they come from env config, never from user input, and user-facing
    crawl targets are validated separately by `validate_public_url()`.

    Idempotent; called once at app/worker startup (and by tests)."""
    global _RESOLVER_INSTALLED
    if _RESOLVER_INSTALLED:
        return
    _RESOLVER_INSTALLED = True

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        results = _orig_getaddrinfo(host, port, *args, **kwargs)
        if _is_ip_literal(host):
            return results  # literal IPs are validated per-request in validate_public_url
        if str(host).lower() in trusted_internal_hosts():
            return results  # operator-trusted private endpoint
        bad = [r for r in results if not is_public_ip(r[4][0])]
        if bad:
            raise SSRFError(
                f"主机 {host} 的解析结果包含内网/保留地址（{[r[4][0] for r in bad]}），已拒绝连接")
        return results

    socket.getaddrinfo = guarded_getaddrinfo


def uninstall_guarded_resolver() -> None:
    """Restore the native resolver. Production keeps it installed for the process
    lifetime; tests use this to avoid polluting process-wide state.

    Restores `_NATIVE_GETADDRINFO` — the pristine function captured at import
    time — NOT the mutable `_orig_getaddrinfo` seam. Restoring the seam would
    install a test-injected fake resolver process-wide and silently disable
    SSRF enforcement for every later test module.
    """
    global _RESOLVER_INSTALLED
    if not _RESOLVER_INSTALLED:
        return
    socket.getaddrinfo = _NATIVE_GETADDRINFO
    _RESOLVER_INSTALLED = False


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time password verification.

    Uses hmac.compare_digest instead of `==` so that a wrong guess cannot be
    distinguished from a correct one by response timing (byte-by-byte early
    exit). Any malformed stored value simply fails verification.
    """
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ---------- JWT ----------
def create_token(user_id: int, username: str) -> str:
    payload = {"sub": str(user_id), "name": username, "exp": int(time.time()) + settings.JWT_EXPIRE_MINUTES * 60}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---------- SSRF guard ----------

def allowed_ports() -> set[int]:
    raw = (settings.ALLOWED_PORTS or "").strip()
    if not raw:
        return {80, 443}
    ports: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            p = int(part)
        except ValueError:
            raise SSRFError(f"ALLOWED_PORTS 含非法端口: {part}")
        if not (0 < p < 65536):
            raise SSRFError(f"ALLOWED_PORTS 端口越界: {p}")
        ports.add(p)
    return ports or {80, 443}


def validate_public_url(url: str) -> str:
    """Validate that a URL is a safe public HTTP(S) target. Raises SSRFError otherwise.

    Checks: scheme allowlist, no userinfo, non-empty hostname, blocked hostnames,
    port allowlist, and DNS resolution where EVERY returned address must be public.
    """
    if not url or not isinstance(url, str):
        raise SSRFError("URL 为空或类型非法")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFError(f"不允许的协议: {p.scheme or '(空)'}")
    if "@" in (p.netloc or ""):
        raise SSRFError("URL 不允许包含用户信息字段（user:pass@）")
    host = p.hostname or ""
    if not host:
        raise SSRFError("URL 缺少主机名")
    if host.lower().rstrip(".") in BLOCKED_HOSTNAMES or \
            host.lower().endswith((".internal", ".local", ".localhost")):
        raise SSRFError(f"禁止访问内部主机: {host}")
    try:
        port = p.port
    except ValueError as e:
        raise SSRFError(f"URL 端口非法: {e}")
    if port is not None and port not in allowed_ports():
        raise SSRFError(f"不允许的端口: {port}（仅允许 {sorted(allowed_ports())}）")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise SSRFError(f"DNS 解析失败: {host} ({e})")
    if not infos:
        raise SSRFError(f"DNS 解析无结果: {host}")
    for info in infos:
        ip = info[4][0]
        if not is_public_ip(ip):
            raise SSRFError(f"禁止访问内网/保留地址: {ip}")
    return url


def sanitize_text(text: str, max_len: int = 20000) -> str:
    """Strip control chars, cap length; treat scraped content as untrusted data only."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return cleaned[:max_len]


# Schemes that are safe to put in an href/src. Everything else (javascript:,
# data:, vbscript:, file:, ...) is dropped. Scraped feeds are untrusted input and
# a `javascript:` URL rendered into an <a href> would execute in our origin.
SAFE_URL_SCHEMES = ("http://", "https://")


def sanitize_url(url: str, max_len: int = 2000) -> str:
    """Return the URL only if its scheme is safe to render; otherwise "".

    Guards the stored-XSS path: a hostile feed can put
    `<link>javascript:alert(1)</link>` in an item. The value is persisted and
    later rendered by the frontend as an anchor href. React escapes text but
    does NOT filter the href scheme, so the scheme must be rejected here, at
    the point where untrusted data enters the database.

    Scheme detection is deliberately conservative: the URL must start with
    http:// or https:// after stripping leading whitespace and control
    characters. A scheme-less relative URL is allowed (it cannot carry a
    scheme) but is length-capped.
    """
    if not url or not isinstance(url, str):
        return ""
    # strip control characters/whitespace that could hide the scheme
    cleaned = "".join(c for c in url if c not in "\r\n\t\x00" and ord(c) >= 32).strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if not lowered.startswith(SAFE_URL_SCHEMES):
        # allow scheme-less relative paths, but reject anything carrying a scheme
        head = cleaned.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if ":" in head:
            return ""
    return cleaned[:max_len]
