"""Security utilities: password hashing (PBKDF2), JWT, SSRF guard."""
import hashlib
import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import jwt

from .config import settings


class SSRFError(Exception):
    pass


PRIVATE_V4 = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
)]
PRIVATE_V6 = [ipaddress.ip_network(n) for n in ("::1/128", "fc00::/7", "fe80::/10", "ff00::/8")]

_orig_getaddrinfo = socket.getaddrinfo
_RESOLVER_INSTALLED = False


def is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    nets = PRIVATE_V4 if ip.version == 4 else PRIVATE_V6
    return not any(ip in n for n in nets)


def install_guarded_resolver() -> None:
    """Wrap socket.getaddrinfo so that DNS results actually used by connections
    are filtered to public IPs. Closes the TOCTOU window between validation and
    connect (DNS rebinding / per-hop re-resolution races).
    Idempotent; called once at app/worker startup (and by tests)."""
    global _RESOLVER_INSTALLED
    if _RESOLVER_INSTALLED:
        return
    _RESOLVER_INSTALLED = True

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        results = _orig_getaddrinfo(host, port, *args, **kwargs)
        # literal IPs and local hostnames keep native behavior (validation is done per-request)
        if _is_ip_literal(host):
            return results
        vetted = [r for r in results if is_public_ip(r[4][0])]
        if not vetted:
            raise SSRFError(f"主机 {host} 的解析结果全部位于内网/保留地址段，已拦截")
        return vetted

    socket.getaddrinfo = guarded_getaddrinfo


def uninstall_guarded_resolver() -> None:
    """Restore the native resolver. Production keeps it installed for the process
    lifetime; tests use this to avoid polluting process-wide state."""
    global _RESOLVER_INSTALLED
    if not _RESOLVER_INSTALLED:
        return
    socket.getaddrinfo = _orig_getaddrinfo
    _RESOLVER_INSTALLED = False


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 120_000)
        return dk.hex() == dk_hex
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

def validate_public_url(url: str) -> str:
    """Validate that a URL is a safe public HTTP(S) target. Raises SSRFError otherwise."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFError(f"不允许的协议: {p.scheme or '(空)'}")
    host = p.hostname or ""
    if not host:
        raise SSRFError("URL 缺少主机名")
    if host in ("localhost", "metadata.google.internal") or host.endswith(".internal") or host.endswith(".local"):
        raise SSRFError(f"禁止访问内部主机: {host}")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise SSRFError(f"DNS 解析失败: {host} ({e})")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        nets = PRIVATE_V4 if ip.version == 4 else PRIVATE_V6
        if any(ip in n for n in nets):
            raise SSRFError(f"禁止访问内网/保留地址: {ip}")
    return url


def sanitize_text(text: str, max_len: int = 20000) -> str:
    """Strip control chars, cap length; treat scraped content as untrusted data only."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return cleaned[:max_len]
