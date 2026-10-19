"""Collector plugin system.

Every collector implements: fetch(source) -> list[CollectedItem].
New source types are added by registering a Collector subclass — no core rewrite.
"""
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..security import SSRFError, validate_public_url

# Domain-level rate limiting: min interval between requests to the same host (seconds)
_DOMAIN_LAST_ACCESS: dict[str, float] = {}
DOMAIN_MIN_INTERVAL = 3.0

# In-process concurrency semaphore for live crawls
MAX_CONCURRENT = settings.MAX_CONCURRENT_CRAWLS


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
        try:
            validate_public_url(url)
        except SSRFError as e:
            raise CollectorError(f"SSRF 防护拦截: {e}") from e
        host = httpx.URL(url).host or ""
        now = time.monotonic()
        elapsed = now - _DOMAIN_LAST_ACCESS.get(host, 0)
        if elapsed < DOMAIN_MIN_INTERVAL:
            time.sleep(DOMAIN_MIN_INTERVAL - elapsed)
        _DOMAIN_LAST_ACCESS[host] = time.monotonic()
        try:
            resp = httpx.get(
                url,
                timeout=timeout or settings.CRAWL_TIMEOUT_SECONDS,
                headers={"User-Agent": settings.USER_AGENT, "Accept": "*/*"},
                follow_redirects=True,
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            raise CollectorError(f"HTTP {e.response.status_code}: {url}") from e
        except httpx.HTTPError as e:
            raise CollectorError(f"请求失败: {type(e).__name__}: {e}") from e


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
