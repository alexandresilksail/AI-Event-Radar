"""RSS / Atom collector (feedparser) + Sitemap collector."""
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

import feedparser

from .base import CollectedItem, CollectorError, BaseCollector, register, to_utc


@register
class RSSCollector(BaseCollector):
    type_name = "rss"

    def fetch(self, source) -> list[CollectedItem]:
        resp = self.http_get(source.url)
        parsed = feedparser.parse(resp.content)
        # A response that is not a feed at all (captcha wall, login page, CDN
        # interstitial, plain HTML) parses "successfully" into zero entries
        # with no version. That must be an ERROR, not an empty feed —
        # otherwise the source looks healthy while collecting nothing and
        # consecutive_failures never rises, so the breakage stays invisible.
        if not parsed.version and not parsed.entries:
            raise CollectorError(
                "响应不是有效的 RSS/Atom 订阅源（可能被验证码/登录墙拦截，"
                "或返回了 HTML 页面）")
        items = []
        for e in parsed.entries:
            link = e.get("link", "")
            if not link:
                continue
            content = ""
            if e.get("content"):
                content = e["content"][0].get("value", "")
            content = content or e.get("summary", "") or e.get("description", "")
            published = None
            for k in ("published_parsed", "updated_parsed"):
                if e.get(k):
                    published = to_utc(
                        __import__("datetime").datetime(*e[k][:6]))
                    break
            items.append(CollectedItem(
                url=link, title=e.get("title", ""), content=content, published_at=published))
        # A malformed feed (bozo) that yields no usable entry is a genuine
        # failure: reporting success would silently drop the remaining items
        # on every subsequent run.
        if parsed.bozo and not items:
            raise CollectorError(f"RSS 解析失败: {parsed.bozo_exception}")
        return items


@register
class SitemapCollector(BaseCollector):
    """Fetch sitemap.xml, optionally fetch each <loc> page's text (limit N pages per run)."""
    type_name = "sitemap"
    MAX_PAGES_PER_RUN = 20

    def fetch(self, source) -> list[CollectedItem]:
        resp = self.http_get(source.url)
        root = ElementTree.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text.strip() for el in root.findall(".//sm:url/sm:loc", ns) or []]
        if not locs:
            locs = [el.text.strip() for el in root.findall(".//{*}loc")]
        items = []
        for loc in locs[: self.MAX_PAGES_PER_RUN]:
            try:
                page = self.http_get(loc)
                text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", page.text)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                items.append(CollectedItem(url=loc, title=loc, content=text[:10000]))
            except CollectorError:
                continue
        return items
