"""HTML / generic API / change-detection collectors — driven by per-source `method` config.

No site rules are hardcoded here; all selectors live in sources.method (JSON), e.g.:
  {"list_selector": ".event-item", "title": "h3 a", "link": "h3 a@href", "detail_selector": "article",
   "fields": {"city": ".city", "date": ".date"}}
"""
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from .base import CollectedItem, CollectorError, BaseCollector, register, to_utc
from ..security import sanitize_text


def _parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    fmts = ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y年%m月%d日 %H:%M"]
    for f in fmts:
        try:
            return to_utc(datetime.strptime(raw[:19] if "T" in f else raw[:10].replace("年", "-").replace("月", "-").replace("日", ""), f)
                          ) if False else to_utc(datetime.strptime(raw, f))
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
    if m:
        try:
            return to_utc(datetime(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            return None
    return None


def _pick(soup: BeautifulSoup, spec: str) -> str:
    """spec: 'selector' or 'selector@attr' (@text/@href/@src/@content)."""
    if not spec:
        return ""
    sel, _, attr = spec.partition("@")
    node = soup.select_one(sel)
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
        resp.encoding = resp.encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        list_sel = cfg.get("list_selector")
        items: list[CollectedItem] = []
        if list_sel:
            blocks = soup.select(list_sel)
            if not blocks:
                raise CollectorError(f"list_selector '{list_sel}' 未匹配到任何元素")
            for b in blocks:
                title = _pick(b, cfg.get("title", "a"))
                link = _pick(b, cfg.get("link", "a@href"))
                if link.startswith("/"):
                    base = "{0.scheme}://{0.netloc}".format(__import__("urllib.parse", fromlist=["urlparse"]).urlparse(source.url))
                    link = base + link
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
        for row in data:
            if not isinstance(row, dict):
                continue
            url = str(row.get(cfg.get("url_field", "url"), ""))
            if not url.startswith("http"):
                continue
            items.append(CollectedItem(
                url=url,
                title=str(row.get(cfg.get("title_field", "title"), "")),
                content=sanitize_text(str(row.get(cfg.get("content_field", "content") or row.get("description", "")))),
                published_at=_parse_date(str(row.get(cfg.get("date_field", "published_at"), ""))),
            ))
        return items


@register
class ChangeDetectionCollector(BaseCollector):
    """Page-change detection: hash the page body; only emit an item when content changed."""
    type_name = "change_detection"
    _last_hash: dict[str, str] = {}

    def fetch(self, source) -> list[CollectedItem]:
        resp = self.http_get(source.url)
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        h = hashlib.sha256(text.encode()).hexdigest()
        if self._last_hash.get(source.url) == h:
            return []
        self._last_hash[source.url] = h
        title = soup.title.get_text(strip=True) if soup.title else source.name
        return [CollectedItem(url=source.url, title=title, content=sanitize_text(text, 20000))]
