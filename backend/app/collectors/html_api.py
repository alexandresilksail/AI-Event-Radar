"""HTML / generic API / change-detection collectors — driven by per-source `method` config.

No site rules are hardcoded here; all selectors live in sources.method (JSON), e.g.:
  {"list_selector": ".event-item", "title": "h3 a", "link": "h3 a@href", "detail_selector": "article",
   "fields": {"city": ".city", "date": ".date"}}

Fixes (audit):
- `hashlib` import was missing in ChangeDetectionCollector -> NameError at runtime.
- Change-detection state is persisted to DB (source_states) so it survives restarts;
  a failed request must NOT overwrite the last successful hash.
- Date parsing: dead code removed; time zones are normalized to naive UTC consistently.
- API collector validates result types; a URL starting with "http" is not assumed safe.
"""
import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import (CollectedItem, CollectorError, BaseCollector, register,
                   safe_get, to_utc)
from ..security import sanitize_text


def _parse_date(raw: str) -> datetime | None:
    """Parse common date formats; naive UTC is the storage convention."""
    raw = (raw or "").strip()
    if not raw:
        return None
    fmts = ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
            "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M"]
    for f in fmts:
        try:
            return to_utc(datetime.strptime(raw, f))
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
    if m:
        try:
            return to_utc(datetime(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            return None
    return None


def _abs_url(base: str, link: str) -> str:
    if not link:
        return ""
    if link.startswith("http"):
        return link
    p = urlparse(base)
    if link.startswith("//"):
        return f"{p.scheme}:{link}"
    prefix = f"{p.scheme}://{p.netloc}"
    if link.startswith("/"):
        return prefix + link
    return prefix + "/" + link.lstrip("/")


def _pick(soup_or_node, spec: str) -> str:
    """spec: 'selector' or 'selector@attr' (@text/@href/@src/@content)."""
    if not spec:
        return ""
    sel, _, attr = spec.partition("@")
    node = soup_or_node.select_one(sel)
    if node is None:
        return ""
    if not attr or attr == "text":
        return node.get_text(strip=True)
    return node.get(attr, "") or ""


@register
class HTMLCollector(BaseCollector):
    type_name = "html"

    def fetch(self, source) -> list[CollectedItem]:
        cfg = source.method or {}
        resp = self.http_get(source.url)
        if not resp.encoding:
            resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        list_sel = cfg.get("list_selector")
        items: list[CollectedItem] = []
        if list_sel:
            blocks = soup.select(list_sel)
            if not blocks:
                raise CollectorError(f"list_selector '{list_sel}' 未匹配到任何元素")
            for b in blocks:
                title = _pick(b, cfg.get("title", "a"))
                link = _abs_url(source.url, _pick(b, cfg.get("link", "a@href")))
                if not link:
                    continue
                fields = {k: _pick(b, v) for k, v in (cfg.get("fields") or {}).items()}
                content = json.dumps(fields, ensure_ascii=False) if fields else title
                items.append(CollectedItem(
                    url=link, title=title or "无标题", content=content,
                    published_at=_parse_date(fields.get("date", "")), extra=fields))
        else:
            detail_sel = cfg.get("detail_selector", "article, main, .content, body")
            node = soup.select_one(detail_sel)
            text = node.get_text("\n", strip=True) if node else soup.get_text("\n", strip=True)
            title = _pick(soup, cfg.get("page_title", "title"))
            items.append(CollectedItem(
                url=source.url, title=title or source.name, content=sanitize_text(text, 20000),
                extra={k: _pick(soup, v) for k, v in (cfg.get("fields") or {}).items()}))
        return items


@register
class APICollector(BaseCollector):
    """Generic JSON API collector.
    method config: {"path_to_array": "data.items", "url_field": "url", "title_field": "title",
                    "content_field": "content", "date_field": "published_at"}
    """
    type_name = "api"
    MAX_ITEMS = 200

    def fetch(self, source) -> list[CollectedItem]:
        cfg = source.method or {}
        resp = self.http_get(source.url)
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise CollectorError(f"API 返回非 JSON: {e}")
        for key in (cfg.get("path_to_array") or "").split("."):
            if key and isinstance(data, dict):
                data = data.get(key, [])
        if not isinstance(data, list):
            raise CollectorError("path_to_array 未指向 JSON 数组")
        items = []
        for row in data[: self.MAX_ITEMS]:
            if not isinstance(row, dict):
                continue  # type check: non-object rows are skipped, not trusted
            url = row.get(cfg.get("url_field", "url"))
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            title = row.get(cfg.get("title_field", "title"))
            content = row.get(cfg.get("content_field", "content"))
            if content is None:
                content = row.get("description", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            items.append(CollectedItem(
                url=url, title=str(title or ""),
                content=sanitize_text(content, 20000),
                published_at=_parse_date(str(row.get(cfg.get("date_field", "published_at")) or "")),
            ))
        return items


@register
class ChangeDetectionCollector(BaseCollector):
    """Page-change detection: hash the page body; emit only when content changed.

    State is persisted in DB (source_states) — survives container restarts.
    A FAILED request never overwrites the last successful hash.
    """
    type_name = "change_detection"

    def fetch(self, source) -> list[CollectedItem]:
        resp = safe_get(source.url)  # raises CollectorError on failure; hash untouched
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        new_hash = hashlib.sha256(text.encode()).hexdigest()

        # read persisted hash only AFTER a successful fetch
        from ..database import SessionLocal
        from ..models import SourceState
        db = SessionLocal()
        try:
            row = db.query(SourceState).filter_by(source_id=source.id).first()
            if row is None:
                db.add(SourceState(source_id=source.id, content_hash=new_hash))
                db.commit()
                first = True
            elif row.content_hash == new_hash:
                return []  # unchanged: no duplicate record
            else:
                row.content_hash = new_hash
                db.commit()
                first = False
        finally:
            db.close()

        title = soup.title.get_text(strip=True) if soup.title else source.name
        return [CollectedItem(url=source.url, title=title,
                              content=("内容变更（首次基线）: " if first else "内容变更: ") + sanitize_text(text, 20000))]
