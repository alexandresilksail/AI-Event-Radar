"""Collector package: registry is populated on import."""
from .base import (BaseCollector, CollectorError, CollectedItem, COLLECTORS,
                   get_collector, register)  # noqa: F401
from . import rss_sitemap, html_api, search  # noqa: F401  (registers plugins)
