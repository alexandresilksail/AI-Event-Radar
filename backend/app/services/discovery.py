"""Source discovery: turn search results into a candidate review queue.

Candidates are created as disabled sources with status='candidate'. An admin must
approve them (PUT /sources/{id}/approve) before they enter long-term monitoring.
Discovery paths are recorded to avoid unbounded crawler expansion.
"""
import re
from collections import Counter
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ..models import AuditLog, Source

_BLOCKED_HOST_PARTS = ("google.", "bing.com", "baidu.", "tavily", "serper",
                       "youtube.", "facebook.", "twitter.", "x.com", "zhihu.com/question")


def extract_main_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    return host


def handle_discovered_items(db: Session, search_source: Source, items) -> int:
    """Create candidate sources for newly-discovered hosts. Returns count added."""
    hosts = Counter()
    titles = {}
    for it in items:
        host = (it.extra or {}).get("discovered_host") or extract_main_domain(it.url)
        if not host or any(b in host for b in _BLOCKED_HOST_PARTS):
            continue
        hosts[host] += 1
        titles.setdefault(host, it.title)

    existing = {s.name for s in db.query(Source.name).all()}
    added = 0
    for host, hits in hosts.most_common(20):
        if host in existing:
            continue
        db.add(Source(
            name=host, type="html", url=f"https://{host}",
            country=search_source.country, language=search_source.language,
            enabled=False, status="candidate", frequency_minutes=1440,
            compliance_note=f"搜索引擎发现候选源（关键词命中 {hits} 次），需人工审核后启用",
            discovery_path=f"via source #{search_source.id} ({search_source.name})",
        ))
        existing.add(host)
        added += 1
    if added:
        db.add(AuditLog(action="discover_sources",
                        detail=f"搜索源 {search_source.name} 发现 {added} 个候选来源待审核"))
        db.commit()
    return added
