"""Crawl pipeline: fetch -> save raw docs -> extract -> dedup/merge -> notify."""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..collectors import CollectedItem, get_collector, CollectorError
from ..models import (CrawlLog, CrawlTask, Event, EventSource, EventTag,
                     NotificationRule, RawDocument, Source, utcnow)
from ..security import sanitize_text, sanitize_url
from .dedup import find_duplicate, grade_confidence, merge_event
from .extraction import extract, is_ai_related, UNKNOWN
from .notifier import dispatch_new_event
from .discovery import handle_discovered_items

logger = logging.getLogger("radar.pipeline")

OFFICIAL_URL_HINTS = ("gov", "gov.cn", ".edu", ".edu.cn", "official")


def is_official_source(source: Source) -> bool:
    url = (source.url or "").lower()
    return any(h in url for h in OFFICIAL_URL_HINTS)


def crawl_source(db: Session, source: Source, task_id: int | None = None) -> CrawlLog:
    """Run one crawl for a source with retry/backoff bookkeeping. Returns the CrawlLog."""
    log = CrawlLog(task_id=task_id, source_id=source.id, status="running",
                   started_at=utcnow())
    db.add(log)
    db.commit()
    doc_errors = notify_errors = 0

    try:
        collector = get_collector(source.type)
        items: list[CollectedItem] = collector.fetch(source)
    except CollectorError as e:
        log.status, log.error, log.finished_at = "failed", str(e)[:2000], utcnow()
        source.consecutive_failures += 1
        source.last_failure_at = utcnow()
        source.last_crawled_at = utcnow()
        source.last_crawl_status = "failed"
        source.last_error = str(e)[:2000]
        if source.consecutive_failures >= 5:
            source.status = "failing"
        db.commit()
        return log

    new_count = updated_count = dup_count = 0
    if source.type == "search":
        handle_discovered_items(db, source, items)
        db.commit()

    for item in items:
        # A hostile feed can put a `javascript:` URL in an item link. It is
        # persisted and later rendered as an <a href>, so the scheme is
        # checked here — at the boundary where untrusted data enters the DB.
        item_url = sanitize_url(item.url)
        # save raw document (content-hash dedup at storage level)
        try:
            db.add(RawDocument(
                source_id=source.id, url=item_url,
                title=sanitize_text(item.title, 500), content=sanitize_text(item.content, 30000),
                content_hash=item.content_hash, published_at=item.published_at))
            db.commit()
        except Exception:
            db.rollback()
            doc_errors += 1
            continue  # already-fetched document: skip re-processing

        if source.type == "search":
            continue  # search results are for source discovery, not direct events

        if not item_url:
            continue  # unusable (unsafe) link — nothing to attach the event to

        if not is_ai_related(f"{item.title} {item.content[:1500]}"):
            continue
        fields = extract(item)
        fields["detail_url"] = item_url
        if not fields.get("is_event"):
            continue

        dup = find_duplicate(db, fields, item_url)
        if dup is None:
            ev = Event(
                name=fields.get("name") or "待核实",
                type=fields.get("type", UNKNOWN),
                organizer=fields.get("organizer", "待核实"),
                co_organizer=fields.get("co_organizer", UNKNOWN),
                start_time=fields.get("start_time"), end_time=fields.get("end_time"),
                registration_deadline=fields.get("registration_deadline"),
                city=fields.get("city", UNKNOWN), address=fields.get("address", UNKNOWN),
                online=fields.get("online"), country=fields.get("country", UNKNOWN),
                language=fields.get("language", UNKNOWN),
                registration_url=fields.get("registration_url", ""),
                detail_url=fields.get("detail_url", item_url),
                audience=fields.get("audience", UNKNOWN), topics=fields.get("topics", UNKNOWN),
                requirements=fields.get("requirements", UNKNOWN), price=fields.get("price", UNKNOWN),
                is_free=fields.get("is_free"), need_registration=fields.get("need_registration"),
                summary=fields.get("summary", ""),
                extraction_method=fields.get("extraction_method", "rule"),
                ai_confidence=fields.get("ai_confidence", 0.0),
            )
            db.add(ev)
            db.flush()
            db.add(EventSource(event_id=ev.id, source_id=source.id, url=item_url, title=item.title))
            ev.confidence_level = grade_confidence(ev, is_official_source(source))
            # finished detection
            refresh_event_status(db, ev)
            db.commit()
            new_count += 1
            try:
                dispatch_new_event(db, ev)
            except Exception as e:
                logger.warning("notification dispatch failed: %s", e)
                notify_errors += 1
        else:
            _, changed = merge_event(db, dup, fields, item_url, item.title, source.id)
            refresh_event_status(db, dup)
            db.commit()
            dup_count += 1
            if changed:
                updated_count += 1
                # a real change (reschedule, venue move, …) is worth telling
                # subscribers about — the notifier dedups per (rule, event, type)
                try:
                    dispatch_new_event(db, dup, event_type="changed")
                except Exception as e:
                    logger.warning("change notification dispatch failed: %s", e)
                    notify_errors += 1

    # partial success: fetch OK, but some documents skipped or notifications failed
    log.status = "partial" if (doc_errors or notify_errors) else "success"
    log.finished_at = utcnow()
    log.found_count, log.new_count = len(items), new_count
    log.updated_count, log.duplicate_count = updated_count, dup_count
    source.last_success_at = utcnow()
    source.last_crawled_at = utcnow()
    source.last_crawl_status = log.status
    source.consecutive_failures = 0
    source.last_item_count = len(items)
    source.total_events_found += new_count
    if source.status == "failing":
        source.status = "active"
    db.commit()
    return log


def refresh_event_status(db: Session, ev: Event) -> None:
    """Detect finished events; changes recorded via merge/history elsewhere."""
    now = utcnow()
    ref = ev.end_time or ev.start_time
    if ref and ref < now - timedelta(days=1) and not ev.is_finished:
        old = ev.status
        ev.is_finished = True
        ev.status = "finished"
        from ..models import EventHistory
        db.add(EventHistory(event_id=ev.id, changed_fields={
            "status": {"old": old, "new": "finished (活动已按时间自动判定结束)"}}))


def create_task_and_crawl(db: Session, source: Source, trigger: str = "manual",
                          retry_of: int | None = None, max_retry: int = 3) -> CrawlLog:
    """Manual/API/scheduled crawl wrapped in a persistent CrawlTask with retry + exponential backoff."""
    import time
    started = utcnow()
    task = CrawlTask(source_id=source.id, status="running", trigger=trigger,
                     retry_of=retry_of, started_at=started)
    db.add(task)
    db.commit()
    log = None
    for attempt in range(1, max_retry + 1):
        task.attempts = attempt
        log = crawl_source(db, source, task.id)
        if log.status in ("success", "partial"):
            break
        if attempt < max_retry:
            log.retry_count = attempt
            db.commit()
            time.sleep(min(2 ** attempt, 30))  # exponential backoff: 2s, 4s, 8s... cap 30s
    ok = bool(log and log.status in ("success", "partial"))
    task.status = log.status if ok else "failed"
    task.error = log.error if log else ""
    task.finished_at = utcnow()
    task.duration_ms = int((task.finished_at - started).total_seconds() * 1000)
    if log:
        task.found_count = log.found_count
        task.new_count = log.new_count
        task.updated_count = log.updated_count
        task.duplicate_count = log.duplicate_count
    db.commit()
    return log
