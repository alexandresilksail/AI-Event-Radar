"""Collector package: registry is populated on import."""
from .base import (BaseCollector, CollectorError, CollectedItem, COLLECTORS,
                   get_collector, register, safe_get, to_utc)  # noqa: F401
from . import rss_sitemap, html_api, search  # noqa: F401  (registers plugins)
from .html_api import HTMLCollector, APICollector, ChangeDetectionCollector  # noqa: F401
from .rss_sitemap import RSSCollector, SitemapCollector  # noqa: F401
from .search import SearchCollector  # noqa: F401
