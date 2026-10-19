"""Search-engine discovery collector — pluggable providers.

Providers: serper.dev (Google), Tavily, Bing Web Search v7.
Without API keys configured, fetch() raises a clear error — never fakes results.
Discovered candidate domains are returned as items; pipeline puts them into a review queue.
"""
import re
from urllib.parse import urlparse

import httpx

from .base import CollectedItem, CollectorError, BaseCollector, register
from ..config import settings


class SearchProvider:
    def search(self, query: str, **kw) -> list[dict]:
        raise NotImplementedError


class SerperProvider(SearchProvider):
    def search(self, query, **kw):
        r = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": settings.SEARCH_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": kw.get("num", 10), "tbs": kw.get("tbs", "")},
            timeout=30,
        )
        r.raise_for_status()
        return [{"url": it.get("link", ""), "title": it.get("title", ""),
                 "snippet": it.get("snippet", "")} for it in r.json().get("organic", [])]


class TavilyProvider(SearchProvider):
    def search(self, query, **kw):
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.SEARCH_API_KEY, "query": query,
                  "search_depth": "basic", "max_results": kw.get("num", 10)},
            timeout=30,
        )
        r.raise_for_status()
        return [{"url": it.get("url", ""), "title": it.get("title", ""),
                 "snippet": it.get("content", "")} for it in r.json().get("results", [])]


class BingProvider(SearchProvider):
    def search(self, query, **kw):
        r = httpx.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": settings.SEARCH_API_KEY},
            params={"q": query, "count": kw.get("num", 10), "freshness": kw.get("freshness", "")},
            timeout=30,
        )
        r.raise_for_status()
        return [{"url": it.get("url", ""), "title": it.get("name", ""),
                 "snippet": it.get("snippet", "")} for it in
                r.json().get("webPages", {}).get("value", [])]


PROVIDERS = {"serper": SerperProvider, "tavily": TavilyProvider, "bing": BingProvider}


def get_search_provider() -> SearchProvider:
    if not settings.SEARCH_PROVIDER or not settings.SEARCH_API_KEY:
        raise CollectorError(
            "搜索引擎未配置：请在 .env 中设置 SEARCH_PROVIDER (serper|tavily|bing) 与 SEARCH_API_KEY。"
            "未配置凭证时系统不会伪装成已实现全网搜索。")
    cls = PROVIDERS.get(settings.SEARCH_PROVIDER)
    if not cls:
        raise CollectorError(f"未知搜索引擎: {settings.SEARCH_PROVIDER}")
    return cls()


DEFAULT_KEYWORDS_ZH = ["人工智能大会", "AI 峰会", "人工智能展", "AI 博览会", "大模型论坛",
                       "智能体大会", "AI 创业大赛", "AI 开发者大会", "AI 发布会"]
DEFAULT_KEYWORDS_EN = ["AI conference", "AI summit", "artificial intelligence expo",
                       "machine learning conference", "AI startup event", "generative AI event"]


@register
class SearchCollector(BaseCollector):
    """source.url unused; source.method may hold {"keywords": [...], "freshness": "m30"}."""
    type_name = "search"

    def fetch(self, source) -> list[CollectedItem]:
        provider = get_search_provider()
        cfg = source.method or {}
        keywords = cfg.get("keywords") or (DEFAULT_KEYWORDS_ZH + DEFAULT_KEYWORDS_EN)
        freshness = cfg.get("freshness", "")
        items, seen = [], set()
        for kw in keywords:
            try:
                results = provider.search(kw, num=cfg.get("num", 10), freshness=freshness)
            except CollectorError:
                raise
            except Exception as e:
                raise CollectorError(f"搜索请求失败: {type(e).__name__}: {e}")
            for r in results:
                url = r.get("url") or ""
                host = urlparse(url).netloc
                if not host or host in seen:
                    continue
                seen.add(host)
                items.append(CollectedItem(
                    url=url, title=r.get("title", ""), content=r.get("snippet", ""),
                    extra={"discovered_keyword": kw, "discovered_host": host}))
        return items
