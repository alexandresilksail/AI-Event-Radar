from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..deps import current_user
from ..models import CrawlLog, Event, EventHistory, EventSource, RawDocument, Source, utcnow
from ..security import create_token  # noqa: F401 (kept for reuse)
from ..services import scheduler as sched

router = APIRouter(tags=["events"])


@router.get("/events")
def list_events(
    q: str = "", keyword: str = "", country: str = "", city: str = "",
    type: str = "", organizer: str = "", source_type: str = "",
    confidence: str = "", verified: str = "", finished: str = "",
    free: str = "", start_from: str = "", start_to: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Event)
    text = q or keyword
    if text:
        like = f"%{text}%"
        query = query.filter(or_(Event.name.like(like), Event.summary.like(like),
                                 Event.topics.like(like)))
    if country:
        query = query.filter(Event.country == country)
    if city:
        query = query.filter(Event.city.like(f"%{city}%"))
    if type:
        query = query.filter(Event.type == type)
    if organizer:
        query = query.filter(Event.organizer.like(f"%{organizer}%"))
    if confidence:
        query = query.filter(Event.confidence_level == confidence)
    if verified in ("true", "false"):
        query = query.filter(Event.verified == (verified == "true"))
    if finished in ("true", "false"):
        query = query.filter(Event.is_finished == (finished == "true"))
    if free in ("true", "false"):
        query = query.filter(Event.is_free == (free == "true"))
    if start_from:
        query = query.filter(Event.start_time >= start_from)
    if start_to:
        query = query.filter(Event.start_time <= start_to)
    total = query.count()
    rows = (query.order_by(Event.start_time.desc().nullslast(), Event.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "items": [_event_dict(e, db) for e in rows]}


@router.get("/events/{event_id}")
def event_detail(event_id: int, db: Session = Depends(get_db)):
    e = db.get(Event, event_id)
    if not e:
        raise HTTPException(404, "活动不存在")
    d = _event_dict(e, db)
    d["history"] = [{"changed_fields": h.changed_fields, "recorded_at": h.recorded_at}
                    for h in e.history]
    d["raw_documents"] = [{"url": r.url, "title": r.title, "fetched_at": r.fetched_at}
                          for r in db.query(RawDocument)
                          .filter(RawDocument.url.in_([s.url for s in e.sources] or [""]))
                          .limit(10).all()]
    return d


@router.get("/calendar")
def calendar_events(view: str = "month", date: str = "", country: str = "",
                    city: str = "", type: str = "", db: Session = Depends(get_db)):
    base = date or utcnow().strftime("%Y-%m-%d")
    anchor = datetime.strptime(base, "%Y-%m-%d")
    if view == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
    else:
        start = anchor.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        end = nxt
    query = db.query(Event).filter(Event.start_time >= start, Event.start_time < end)
    if country:
        query = query.filter(Event.country == country)
    if city:
        query = query.filter(Event.city.like(f"%{city}%"))
    if type:
        query = query.filter(Event.type == type)
    return {"range": [str(start.date()), str(end.date())],
            "items": [_event_dict(e, db) for e in query.order_by(Event.start_time).all()]}


def _event_dict(e: Event, db: Session) -> dict:
    srcs = db.query(EventSource).filter_by(event_id=e.id).all()
    return {
        "id": e.id, "name": e.name, "type": e.type, "organizer": e.organizer,
        "co_organizer": e.co_organizer, "start_time": e.start_time, "end_time": e.end_time,
        "registration_deadline": e.registration_deadline, "city": e.city,
        "address": e.address, "online": e.online, "country": e.country,
        "language": e.language, "registration_url": e.registration_url,
        "detail_url": e.detail_url, "audience": e.audience, "topics": e.topics,
        "requirements": e.requirements, "price": e.price, "is_free": e.is_free,
        "need_registration": e.need_registration, "summary": e.summary,
        "confidence_level": e.confidence_level, "verified": e.verified,
        "status": e.status, "is_finished": e.is_finished,
        "extraction_method": e.extraction_method, "ai_confidence": e.ai_confidence,
        "first_seen_at": e.first_seen_at, "last_confirmed_at": e.last_confirmed_at,
        "sources": [{"source_id": s.source_id, "url": s.url, "title": s.title,
                     "first_seen_at": s.first_seen_at} for s in srcs],
    }


# ---------- crawl logs ----------
@router.get("/crawl-logs")
def crawl_logs(date: str = "", source_id: int = 0, status: str = "",
               page: int = 1, page_size: int = 50,
               user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """Crawl logs (authenticated): contain source URLs and raw error text."""
    query = db.query(CrawlLog)
    if date:
        query = query.filter(func.date(CrawlLog.started_at) == date)
    if source_id:
        query = query.filter(CrawlLog.source_id == source_id)
    if status:
        query = query.filter(CrawlLog.status == status)
    total = query.count()
    rows = (query.order_by(CrawlLog.started_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    items = []
    for r in rows:
        s = db.get(Source, r.source_id)
        items.append({"id": r.id, "task_id": r.task_id, "source_id": r.source_id,
                      "source_name": s.name if s else str(r.source_id),
                      "status": r.status, "started_at": r.started_at,
                      "finished_at": r.finished_at, "found_count": r.found_count,
                      "new_count": r.new_count, "updated_count": r.updated_count,
                      "duplicate_count": r.duplicate_count, "retry_count": r.retry_count,
                      "error": r.error})
    return {"total": total, "items": items}


# ---------- stats ----------
@router.get("/stats/dashboard")
def dashboard_stats(db: Session = Depends(get_db)):
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total_sources = db.query(func.count(Source.id)).scalar() or 0
    healthy = (db.query(func.count(Source.id))
               .filter(Source.enabled == True, Source.consecutive_failures == 0).scalar() or 0)  # noqa: E712
    failing = (db.query(func.count(Source.id))
               .filter(Source.status == "failing").scalar() or 0)
    pending_verify = (db.query(func.count(Event.id))
                      .filter(Event.confidence_level == "待核实").scalar() or 0)
    new_today = (db.query(func.count(Event.id))
                 .filter(Event.first_seen_at >= today_start).scalar() or 0)
    upcoming = (db.query(Event)
                .filter(Event.start_time >= now, Event.start_time <= now + timedelta(days=7),
                        Event.is_finished == False)  # noqa: E712
                .order_by(Event.start_time).limit(8).all())
    trend_rows = (db.query(func.date(CrawlLog.started_at).label("d"),
                           func.count(CrawlLog.id),
                           func.sum(CrawlLog.new_count))
                  .filter(CrawlLog.started_at >= now - timedelta(days=1))
                  .group_by("d").all())
    trend7_rows = (db.query(func.date(CrawlLog.started_at).label("d"),
                            func.count(CrawlLog.id),
                            func.sum(CrawlLog.new_count),
                            func.sum(case((CrawlLog.status == "failed", 1), else_=0)))
                   .filter(CrawlLog.started_at >= now - timedelta(days=7))
                   .group_by("d").all())
    last_success = (db.query(CrawlLog.started_at)
                    .filter(CrawlLog.status == "success")
                    .order_by(CrawlLog.started_at.desc()).first())
    today_logs = (db.query(CrawlLog)
                  .filter(CrawlLog.started_at >= today_start).all())
    today_finished = [l for l in today_logs if l.status in ("success", "partial", "failed")]
    today_success = [l for l in today_finished if l.status in ("success", "partial")]
    today_durations = [(l.finished_at - l.started_at).total_seconds() * 1000
                       for l in today_finished if l.finished_at and l.started_at]
    all_sources = db.query(Source).filter(Source.deleted_at.is_(None)).all()
    health_breakdown = {"healthy": 0, "failing": 0, "paused": 0, "never": 0}
    for s in all_sources:
        if not s.enabled:
            health_breakdown["paused"] += 1
        elif s.consecutive_failures > 0 or s.status == "failing":
            health_breakdown["failing"] += 1
        elif s.last_success_at is None:
            health_breakdown["never"] += 1
        else:
            health_breakdown["healthy"] += 1
    sch = sched.scheduler_status()
    return {
        "new_events_today": new_today,
        "total_sources": len(all_sources),
        "healthy_sources": healthy,
        "failing_sources": failing,
        "pending_verify_events": pending_verify,
        "upcoming_7d": [_event_dict(e, db) for e in upcoming],
        "crawl_trend_24h": [{"date": str(r[0]), "tasks": r[1], "new_events": int(r[2] or 0)}
                            for r in trend_rows],
        "crawl_trend_7d": [{"date": str(r[0]), "tasks": r[1], "new_events": int(r[2] or 0),
                            "failed": int(r[3] or 0)} for r in trend7_rows],
        "today_crawls": {
            "total": len(today_finished),
            "succeeded": len(today_success),
            "success_rate": round(len(today_success) / len(today_finished), 4) if today_finished else None,
            "avg_duration_ms": round(sum(today_durations) / len(today_durations)) if today_durations else None,
        },
        "source_health": health_breakdown,
        "last_success_crawl_at": last_success[0] if last_success else None,
        "system": {"scheduler_running": sch["scheduler_running"],
                   "job_count": sch["job_count"],
                   "running_tasks": sch["running_task_source_ids"]},
    }


@router.get("/stats/analysis")
def analysis_stats(db: Session = Depends(get_db)):
    now = utcnow()

    def bucket(days_from, days_to):
        return (db.query(func.count(Event.id))
                .filter(Event.first_seen_at >= now + timedelta(days=days_from),
                        Event.first_seen_at < now + timedelta(days=days_to)).scalar() or 0)

    daily = dict((str(r[0]), r[1]) for r in
                 db.query(func.date(Event.first_seen_at), func.count(Event.id))
                 .group_by(func.date(Event.first_seen_at))
                 .order_by(func.date(Event.first_seen_at)).limit(60).all())
    by_country = dict((r[0], r[1]) for r in
                      db.query(Event.country, func.count(Event.id))
                      .group_by(Event.country).order_by(func.count(Event.id).desc()).all())
    by_city = dict((r[0], r[1]) for r in
                   db.query(Event.city, func.count(Event.id))
                   .group_by(Event.city).order_by(func.count(Event.id).desc()).limit(15).all())
    by_type = dict((r[0], r[1]) for r in
                   db.query(Event.type, func.count(Event.id))
                   .group_by(Event.type).order_by(func.count(Event.id).desc()).all())
    by_organizer = dict((r[0], r[1]) for r in
                        db.query(Event.organizer, func.count(Event.id))
                        .filter(Event.organizer.notin_(["待核实", "未知"]))
                        .group_by(Event.organizer)
                        .order_by(func.count(Event.id).desc()).limit(10).all())
    total_ev = db.query(func.count(Event.id)).scalar() or 0
    cancelled = db.query(func.count(Event.id)).filter(Event.status == "cancelled").scalar() or 0
    finished = db.query(func.count(Event.id)).filter(Event.is_finished == True).scalar() or 0  # noqa: E712
    verified = db.query(func.count(Event.id)).filter(Event.verified == True).scalar() or 0  # noqa: E712
    src_stats = [{"id": s.id, "name": s.name, "type": s.type,
                  "events": s.total_events_found,
                  "success_rate": (1 if s.consecutive_failures == 0 else 0.0)}
                 for s in db.query(Source).all()]
    return {
        "daily_new": daily,
        "weekly_new": bucket(-7, 0), "monthly_new": bucket(-30, 0),
        "calendar_7d": bucket(0, 7), "calendar_30d": bucket(0, 30),
        "calendar_90d": bucket(0, 90),
        "by_country": by_country, "by_city": by_city, "by_type": by_type,
        "top_organizers": by_organizer,
        "total_events": total_ev,
        "cancelled_rate": round(cancelled / total_ev, 4) if total_ev else 0,
        "finished_rate": round(finished / total_ev, 4) if total_ev else 0,
        "verified_rate": round(verified / total_ev, 4) if total_ev else 0,
        "source_contribution": src_stats,
    }


@router.get("/health", response_class=PlainTextResponse, include_in_schema=False)
def text_health(db: Session = Depends(get_db)):
    from sqlalchemy import text as sql
    try:
        db.execute(sql("SELECT 1"))
        return "ok: database reachable"
    except Exception as e:
        return f"error: {e}"
