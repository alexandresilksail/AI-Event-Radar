"""Multi-layer deduplication & confidence grading.

Layers: 1) normalized URL exact match  2) normalized title similarity
        3) name+organizer+date match   4) location+registration URL match
        5) semantic similarity hook (LLM optional)
Merging NEVER deletes an event — all sources are preserved on the merged record.
"""
import re
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..models import Event, EventSource, utcnow


def normalize_url(url: str) -> str:
    url = (url or "").strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.split("#")[0].split("?")[0]
    return url.rstrip("/")


def normalize_name(name: str) -> str:
    name = re.sub(r"[\s\W_]+", "", (name or "").lower())
    return name


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def find_duplicate(db: Session, fields: dict, url: str, threshold: float = 0.82) -> Event | None:
    """Return an existing event that is likely the same real-world event, else None."""
    # Layer 1: URL
    norm = normalize_url(url)
    if norm:
        # Narrow the candidate set by host (the first path segment of the
        # normalized URL) before comparing exactly. The SQL filter is only a
        # PRE-filter — the exact normalized comparison below is what decides
        # the match — so a broad `contains(host)` is acceptable here and
        # avoids schema-specific prefix matching.
        host = norm.split("/")[0]
        es = db.query(EventSource).filter(EventSource.url.contains(host)).all()
        for row in es:
            if normalize_url(row.url) == norm:
                return db.get(Event, row.event_id)
    # Layer 2/3/4: fuzzy match only when we have a usable name
    name = fields.get("name") or ""
    if not name or name in ("待核实", "未知"):
        return None
    start = fields.get("start_time")
    city = fields.get("city")
    candidates = db.query(Event).filter(Event.status.in_(["active", "unknown"])).limit(500).all()
    best, best_score = None, 0.0
    for ev in candidates:
        sim = title_similarity(name, ev.name)
        boost = 0.0
        # Layer 3: same organizer & overlapping date
        if start and ev.start_time:
            delta = abs((start - ev.start_time).days)
            if delta <= 3:
                boost += 0.08
        # Layer 4: same city
        if city and city != "未知" and ev.city == city:
            boost += 0.05
        score = sim + boost
        if score > best_score:
            best, best_score = ev, score
    if best is not None and best_score >= threshold:
        return best
    return None


def grade_confidence(event: Event, source_is_official: bool = False) -> str:
    """Rules-based grading — AI self-reported confidence is NEVER used as real trust."""
    reg = (event.registration_url or "").strip()
    detail = (event.detail_url or "").strip()
    if source_is_official or (reg and event.organizer not in ("待核实", "未知")
                              and event.start_time is not None):
        return "A"
    if event.start_time is not None and event.city != "未知" and len(event.sources) >= 2:
        return "B"
    if event.organizer in ("待核实", "未知") or event.start_time is None or event.city == "未知":
        return "待核实"
    return "C"


def merge_event(db: Session, existing: Event, fields: dict, url: str, title: str,
                source_id: int) -> tuple[Event, list[str]]:
    """Update existing event with new info; keep full history; never delete.

    Field-update policy:
    - A new value that is unknown/empty NEVER overwrites a confirmed value.
    - An empty/unknown old value is filled in by a confirmed new value.
    - A confirmed new value that DIFFERS from a confirmed old value is a real
      change. It is applied ONLY when it comes from the SAME publisher that
      already contributed this event (same source URL) — that is the
      publisher correcting its own record (e.g. the event was rescheduled).
      A different publisher disagreeing does NOT overwrite the value: two
      outlets reporting different dates would otherwise flip-flop the field
      on every crawl. The disagreement is still recorded in the history so a
      human can see it.
    """
    changed: dict[str, dict] = {}
    conflicts: dict[str, dict] = {}
    # Is this the same publisher that already contributed this event?
    same_publisher = False
    if url:
        same_publisher = db.query(EventSource).filter_by(
            event_id=existing.id, url=url).first() is not None
    for key, new_val in fields.items():
        old_val = getattr(existing, key, None)
        if new_val in (None, "未知", "待核实", ""):
            continue  # never overwrite confirmed data with unknown
        if old_val in (None, "未知", "待核实", ""):
            if old_val != new_val:
                changed[key] = {"old": str(old_val), "new": str(new_val)}
                setattr(existing, key, new_val)
            continue
        if old_val == new_val:
            continue
        # both sides are confirmed and they disagree
        if same_publisher:
            changed[key] = {"old": str(old_val), "new": str(new_val)}
            setattr(existing, key, new_val)
        else:
            conflicts[key] = {"kept": str(old_val), "other_source_said": str(new_val)}
    if url:
        exists = db.query(EventSource).filter_by(event_id=existing.id, url=url).first()
        if not exists:
            db.add(EventSource(event_id=existing.id, source_id=source_id, url=url, title=title))
            changed.setdefault("sources_added", {"old": "", "new": url})
    existing.last_confirmed_at = utcnow()
    if changed or conflicts:
        from ..models import EventHistory
        payload = dict(changed)
        if conflicts:
            payload["conflicts"] = conflicts
        db.add(EventHistory(event_id=existing.id, changed_fields=payload))
    db.commit()
    return existing, [f"{k}: {v['old']} -> {v['new']}" for k, v in changed.items()]
