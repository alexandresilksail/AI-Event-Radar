"""Collector plugin system.

Every collector implements: fetch(source) -> list[CollectedItem].
New source types are added by registering a Collector subclass — no core rewrite.

SSRF/safety hardening (final):
- Redirects are followed MANUALLY: every hop is re-validated before connecting
  (blocks public-host -> internal-IP redirects, DNS-rebinding between hops, scheme change).
- Response body size, timeout (connect/read split), redirect count are hard-capped.
- A process-wide bounded semaphore caps concurrent outbound requests; it is
  re-entrant per thread (nested fetches, e.g. Sitemap page lists, do not deadlock).
"""
import hashlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..security import SSRFError, validate_public_url

# Domain-level rate limiting: min interval between requests to the same host (seconds)
_DOMAIN_LAST_ACCESS: dict[str, float] = {}
DOMAIN_MIN_INTERVAL = 3.0

MAX_CONCURRENT = max(1, settings.MAX_CONCURRENT_CRAWLS)

# Global outbound concurrency cap. Re-entrant per thread: only the outermost
# safe_get acquires a permit, so Sitemap/AHK nested fetches cannot deadlock.
_global_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_tls = threading.local()


class _depth:
    @staticmethod
    def get() -> int:
        return getattr(_tls, "depth", 0)

    @staticmethod
    def inc() -> None:
        _tls.depth = getattr(_tls, "depth", 0) + 1

    @staticmethod
    def dec() -> None:
        _tls.depth = max(0, getattr(_tls, "depth", 0) - 1)


def _throttle(url: str) -> None:
    host = httpx.URL(url).host or ""
    now = time.monotonic()
    elapsed = now - _DOMAIN_LAST_ACCESS.get(host, 0)
    if elapsed < DOMAIN_MIN_INTERVAL:
        time.sleep(DOMAIN_MIN_INTERVAL - elapsed)
    _DOMAIN_LAST_ACCESS[host] = time.monotonic()


def safe_get(url: str, timeout: int | None = None,
             method: str = "GET") -> httpx.Response:
    """HTTP GET/HEAD with per-hop SSRF validation and hard resource caps.

    Raises CollectorError on: non-http(s) URL, internal/private targets at ANY hop,
    redirect loops/too many hops, oversized bodies, timeouts, or HTTP errors.
    """
    current = url
    max_redirects = settings.MAX_REDIRECTS
    total_timeout = timeout or settings.CRAWL_TIMEOUT_SECONDS
    # connect timeout is bounded separately from read timeout
    read_timeout = httpx.Timeout(total_timeout, connect=min(10, total_timeout))
    outermost = _depth.get() == 0
    if outermost:
        _global_sem.acquire()
    _depth.inc()
    try:
        for hop in range(max_redirects + 1):
            try:
                validate_public_url(current)
            except SSRFError as e:
                raise CollectorError(f"SSRF 防护拦截（第 {hop + 1} 跳）: {e}") from e
            _throttle(current)
            try:
                with httpx.Client(follow_redirects=False, timeout=read_timeout) as client:
                    req = client.build_request(method, current,
                                               headers={"User-Agent": settings.USER_AGENT})
                    resp = client.send(req, stream=True)
                    try:
                        if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                            location = resp.headers["location"]
                            nxt = str(httpx.URL(current).join(location))
                            if hop >= max_redirects:
                                raise CollectorError(f"重定向次数超过上限 {max_redirects}")
                            current = nxt
                            continue
                        # read body with size cap (also guards against zip-bomb style streams)
                        chunks, size = [], 0
                        for chunk in resp.iter_bytes():
                            size += len(chunk)
                            if size > settings.MAX_RESPONSE_BYTES:
                                raise CollectorError(f"响应体超过大小上限 {settings.MAX_RESPONSE_BYTES} 字节")
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        full = httpx.Response(resp.status_code, content=content,
                                              headers=resp.headers, request=req)
                        full.raise_for_status()
                        return full
                    finally:
                        resp.close()
            except CollectorError:
                raise
            except httpx.HTTPStatusError as e:
                raise CollectorError(f"HTTP {e.response.status_code}: {current}") from e
            except SSRFError as e:
                # guarded DNS resolver rejected the actual connect target
                raise CollectorError(f"SSRF 防护拦截（连接时 DNS 校验，第 {hop + 1} 跳）: {e}") from e
            except httpx.HTTPError as e:
                raise CollectorError(f"请求失败: {type(e).__name__}: {e}") from e
        raise CollectorError("重定向次数超过上限")
    finally:
        _depth.dec()
        if outermost:
            _global_sem.release()


@dataclass
class CollectedItem:
    url: str
    title: str
    content: str
    published_at: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update((self.url or "").encode())
        h.update(b"\x00")
        h.update((self.content or self.title or "").encode()[:20000])
        return h.hexdigest()


class CollectorError(Exception):
    pass


class BaseCollector:
    type_name = "base"

    def fetch(self, source) -> list[CollectedItem]:
        raise NotImplementedError

    def http_get(self, url: str, timeout: int | None = None) -> httpx.Response:
        return safe_get(url, timeout=timeout)


COLLECTORS: dict[str, type[BaseCollector]] = {}


def register(cls: type[BaseCollector]):
    COLLECTORS[cls.type_name] = cls
    return cls


def get_collector(source_type: str) -> BaseCollector:
    cls = COLLECTORS.get(source_type)
    if cls is None:
        raise CollectorError(f"未知数据源类型: {source_type}，可用: {sorted(COLLECTORS)}")
    return cls()


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
