"""数据源管理 API：列表（筛选/搜索/分页）、增改、启停、审核、测试、立即采集、批量触发、详情、趋势、软删除。

安全边界：
- 所有可配置 URL 在保存时即经过 SSRF 校验（协议/主机名/解析结果），采集时还会再次逐跳校验。
- 读接口公开；写接口需要管理员 JWT（deps.current_user）。
- 删除默认软删除（deleted_at + 停用调度），永久删除需显式 permanent=true 并在审计中单独记录。
"""
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..collectors import COLLECTORS
from ..database import get_db
from ..deps import audit, current_user
from ..models import (CrawlLog, CrawlTask, Event, EventSource, RawDocument,
                      Source, SourceState)
from ..security import SSRFError, validate_public_url
from ..services.pipeline import create_task_and_crawl
from ..services import scheduler as sched

router = APIRouter(prefix="/sources", tags=["sources"])

VALID_STATUS = {"candidate", "active", "paused", "failing"}
SORTABLE = {"id", "created_at", "updated_at", "last_crawled_at", "last_success_at", "name"}


# ---------- helpers ----------

def validate_source_url(url: str) -> str:
    """URL 基本格式 + SSRF 校验（保存时即拦截内网/元数据/非法协议目标）。"""
    url = (url or "").strip()
    if len(url) > 1024:
        raise HTTPException(400, "URL 过长（上限 1024 字符）")
    try:
        p = urlparse(url)
    except Exception:
        raise HTTPException(400, "URL 格式无效")
    if p.scheme not in ("http", "https") or not p.netloc:
        raise HTTPException(400, "URL 必须是 http(s) 地址且包含有效主机名")
    try:
        validate_public_url(url)
    except SSRFError as e:
        raise HTTPException(400, f"URL 未通过安全校验：{e}")
    return url


def health_of(s: Source) -> str:
    """Derived health: paused | failing | never | healthy（与 enabled/失败次数/成功历史相关）。"""
    if not s.enabled:
        return "paused"
    if s.consecutive_failures > 0 or s.status == "failing":
        return "failing"
    if s.last_success_at is None:
        return "never"
    return "healthy"


def source_dict(s: Source) -> dict:
    return {
        "id": s.id, "name": s.name, "type": s.type, "url": s.url,
        "country": s.country, "language": s.language, "topics": s.topics,
        "timezone": s.timezone, "note": s.note,
        "frequency_minutes": s.frequency_minutes, "enabled": s.enabled,
        "status": s.status, "health": health_of(s),
        "last_success_at": s.last_success_at, "last_failure_at": s.last_failure_at,
        "last_crawled_at": s.last_crawled_at, "last_crawl_status": s.last_crawl_status,
        "last_error": s.last_error, "consecutive_failures": s.consecutive_failures,
        "last_item_count": s.last_item_count, "total_events_found": s.total_events_found,
        "compliance_note": s.compliance_note, "discovery_path": s.discovery_path,
        "method": s.method, "created_at": s.created_at, "updated_at": s.updated_at,
    }


def _bucket_by_hour(rows) -> list[dict]:
    """rows: (started_at, new_count) — bucket into 24 hourly slots ending now."""
    from datetime import datetime, timedelta
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    slots = {}
    for i in range(24, 0, -1):
        slots[(now - timedelta(hours=i)).strftime("%m-%d %H时")] = {"tasks": 0, "new_events": 0}
    for started, new_count in rows:
        if not started:
            continue
        key = started.replace(minute=0, second=0, microsecond=0).strftime("%m-%d %H时")
        if key in slots:
            slots[key]["tasks"] += 1
            slots[key]["new_events"] += int(new_count or 0)
    return [{"hour": k, **v} for k, v in slots.items()]


def _bucket_by_day(rows, days: int = 7) -> list[dict]:
    from datetime import datetime, timedelta
    now = datetime.utcnow().date()
    slots = {}
    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        slots[d.isoformat()] = {"tasks": 0, "new_events": 0, "failed": 0}
    for started, new_count, status in rows:
        if not started:
            continue
        key = started.date().isoformat()
        if key in slots:
            slots[key]["tasks"] += 1
            slots[key]["new_events"] += int(new_count or 0)
            if status == "failed":
                slots[key]["failed"] += 1
    return [{"date": k, **v} for k, v in slots.items()]


class SourceBody(BaseModel):
    name: str
    type: str
    url: str
    country: str = "未知"
    language: str = "zh"
    topics: str = ""
    timezone: str = ""
    note: str = ""
    method: dict = {}
    frequency_minutes: int = 60
    enabled: bool = True
    status: str = "active"
    compliance_note: str = ""

    @field_validator("frequency_minutes")
    @classmethod
    def freq_ok(cls, v):
        if v < 5:
            raise ValueError("采集频率不能低于 5 分钟")
        return v

    @field_validator("type")
    @classmethod
    def type_ok(cls, v):
        if v not in COLLECTORS:
            raise ValueError(f"类型必须是 {sorted(COLLECTORS)}")
        return v

    @field_validator("status")
    @classmethod
    def status_ok(cls, v):
        if v not in VALID_STATUS:
            raise ValueError(f"状态必须是 {sorted(VALID_STATUS)}")
        return v


# ---------- list ----------

@router.get("")
def list_sources(keyword: str = "", type: str = "", status: str = "",
                 enabled: str = "", health: str = "",
                 sort: str = "id", order: str = "asc",
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
                 db: Session = Depends(get_db)):
    q = db.query(Source).filter(Source.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter(or_(Source.name.like(like), Source.url.like(like),
                         Source.topics.like(like), Source.note.like(like)))
    if type:
        q = q.filter(Source.type == type)
    if status:
        q = q.filter(Source.status == status)
    if enabled in ("true", "false"):
        q = q.filter(Source.enabled == (enabled == "true"))
    rows = q.all()
    items = [source_dict(s) for s in rows]
    if health:
        items = [d for d in items if d["health"] == health]
    reverse = order == "desc"
    if sort in SORTABLE:
        items.sort(key=lambda d: (d.get(sort) is None, d.get(sort)),
                   reverse=reverse)
    total = len(items)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "items": items[start:start + page_size],
            "summary": {
                "healthy": sum(1 for d in items if d["health"] == "healthy"),
                "failing": sum(1 for d in items if d["health"] == "failing"),
                "paused": sum(1 for d in items if d["health"] == "paused"),
                "never": sum(1 for d in items if d["health"] == "never"),
            }}


# ---------- create / update ----------

@router.post("", status_code=201)
def create_source(body: SourceBody, request: Request,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    url = validate_source_url(body.url)
    s = Source(**{**body.model_dump(), "url": url})
    db.add(s)
    db.commit()
    db.refresh(s)
    sched.schedule_source(s)
    audit(request, user, "create_source", f"source #{s.id} {s.name}", db)
    return {"id": s.id, "name": s.name, "status": s.status}


@router.put("/{source_id}")
def update_source(source_id: int, body: SourceBody, request: Request,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s or s.deleted_at:
        raise HTTPException(404, "数据源不存在")
    url = validate_source_url(body.url)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    s.url = url
    db.commit()
    sched.schedule_source(s)
    audit(request, user, "update_source", f"source #{s.id}", db)
    return {"id": s.id, "status": s.status}


# ---------- state changes ----------

@router.post("/{source_id}/pause")
def pause_source(source_id: int, request: Request,
                 user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s or s.deleted_at:
        raise HTTPException(404, "数据源不存在")
    s.enabled, s.status = False, "paused"
    db.commit()
    sched.remove_source(source_id)
    audit(request, user, "pause_source", f"source #{s.id}", db)
    return {"id": s.id, "status": s.status}


@router.post("/{source_id}/resume")
def resume_source(source_id: int, request: Request,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s or s.deleted_at:
        raise HTTPException(404, "数据源不存在")
    if s.status == "candidate":
        raise HTTPException(400, "候选来源需先通过 /approve 审核")
    s.enabled, s.status = True, "active"
    db.commit()
    sched.schedule_source(s)
    audit(request, user, "resume_source", f"source #{s.id}", db)
    return {"id": s.id, "status": s.status}


@router.post("/{source_id}/approve")
def approve_source(source_id: int, request: Request,
                   user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s or s.deleted_at:
        raise HTTPException(404, "数据源不存在")
    s.status, s.enabled = "active", True
    db.commit()
    sched.schedule_source(s)
    audit(request, user, "approve_source", f"source #{s.id}", db)
    return {"id": s.id, "status": s.status}


@router.post("/{source_id}/restore")
def restore_source(source_id: int, request: Request,
                   user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """恢复软删除的数据源（历史日志与活动关联保持不变）。"""
    s = db.get(Source, source_id)
    if not s or not s.deleted_at:
        raise HTTPException(404, "数据源不存在或未被删除")
    s.deleted_at = None
    db.commit()
    audit(request, user, "restore_source", f"source #{s.id}", db)
    return {"id": s.id, "restored": True}


# ---------- delete (soft by default) ----------

@router.delete("/{source_id}")
def delete_source(source_id: int, request: Request, permanent: bool = False,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """默认软删除（保留历史数据，可从回收恢复）；permanent=true 才真正删除。"""
    s = db.get(Source, source_id)
    if not s:
        raise HTTPException(404, "数据源不存在")
    sched.remove_source(source_id)
    if permanent:
        db.delete(s)  # cascade removes its crawl logs
        db.commit()
        audit(request, user, "delete_source_permanent", f"source #{source_id} {s.name}", db)
        return {"deleted": "permanent", "id": source_id}
    s.deleted_at = datetime.utcnow()
    s.enabled, s.status = False, "paused"
    db.commit()
    audit(request, user, "delete_source", f"soft delete source #{source_id} {s.name}", db)
    return {"deleted": "soft", "id": source_id}


# ---------- operations ----------

@router.post("/{source_id}/trigger")
def trigger_source(source_id: int, request: Request,
                   user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """单源立即采集（同步执行，含失败自动重试与指数退避）。"""
    s = db.get(Source, source_id)
    if not s or s.deleted_at:
        raise HTTPException(404, "数据源不存在")
    log = create_task_and_crawl(db, s, trigger="manual")
    audit(request, user, "trigger_source", f"source #{s.id}", db)
    return {"log_id": log.id, "task_id": log.task_id, "status": log.status,
            "found": log.found_count, "new_events": log.new_count,
            "updated": log.updated_count, "duplicates": log.duplicate_count,
            "error": log.error}


class BatchBody(BaseModel):
    ids: list[int]

    @field_validator("ids")
    @classmethod
    def not_empty(cls, v):
        if not v or len(v) > 50:
            raise ValueError("批量触发数量必须在 1–50 之间")
        return v


@router.post("/batch-trigger")
def batch_trigger(body: BatchBody, request: Request,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """批量立即采集（逐源同步执行，失败不影响其他源）。"""
    results = []
    for sid in body.ids:
        s = db.get(Source, sid)
        if not s or s.deleted_at:
            results.append({"source_id": sid, "status": "failed", "error": "数据源不存在"})
            continue
        try:
            log = create_task_and_crawl(db, s, trigger="manual", max_retry=1)
            results.append({"source_id": sid, "name": s.name, "status": log.status,
                            "found": log.found_count, "new_events": log.new_count,
                            "error": log.error})
        except Exception as e:  # never let one source break the batch
            results.append({"source_id": sid, "name": s.name, "status": "failed",
                            "error": str(e)[:500]})
    audit(request, user, "batch_trigger", f"{len(body.ids)} sources", db)
    ok = sum(1 for r in results if r["status"] in ("success", "partial"))
    return {"triggered": len(results), "succeeded": ok, "results": results}


@router.post("/{source_id}/test")
def test_source(source_id: int, request: Request,
                user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """连通性测试：真实发起一次请求，但不进入活动管线、不写库。"""
    s = db.get(Source, source_id)
    if not s or s.deleted_at:
        raise HTTPException(404, "数据源不存在")
    from ..collectors import get_collector, CollectorError
    try:
        items = get_collector(s.type).fetch(s)
        audit(request, user, "test_source", f"source #{s.id} ok items={len(items)}", db)
        return {"ok": True, "items": len(items)}
    except CollectorError as e:
        audit(request, user, "test_source", f"source #{s.id} failed", db)
        return {"ok": False, "error": str(e)}


# ---------- detail ----------

@router.get("/{source_id}")
def source_detail(source_id: int, db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s:
        raise HTTPException(404, "数据源不存在")
    logs = (db.query(CrawlLog).filter(CrawlLog.source_id == s.id)
            .order_by(CrawlLog.started_at.desc()).limit(200).all())
    total_logs = db.query(func.count(CrawlLog.id)).filter(CrawlLog.source_id == s.id).scalar() or 0
    counts = {"success": 0, "partial": 0, "failed": 0}
    for l in logs:
        if l.status in counts:
            counts[l.status] += 1
    now = datetime.utcnow()
    logs_24h = [l for l in logs if l.started_at and l.started_at >= now - timedelta(hours=24)]
    logs_7d = [l for l in logs if l.started_at and l.started_at >= now - timedelta(days=7)]
    durations = [(l.finished_at - l.started_at).total_seconds() * 1000
                 for l in logs_24h if l.finished_at and l.started_at]
    docs = (db.query(RawDocument).filter(RawDocument.source_id == s.id)
            .order_by(RawDocument.fetched_at.desc()).limit(10).all())
    tasks = (db.query(CrawlTask).filter(CrawlTask.source_id == s.id)
             .order_by(CrawlTask.started_at.desc()).limit(10).all())
    related_events = (db.query(Event)
                      .join(EventSource, EventSource.event_id == Event.id)
                      .filter(EventSource.source_id == s.id)
                      .order_by(Event.first_seen_at.desc()).limit(10).all())
    state = db.get(SourceState, s.id)
    # 内容变更记录：change_detection 哈希变化（SourceState 持久化）
    change_records = []
    if state:
        change_records.append({"content_hash": state.content_hash,
                               "updated_at": state.updated_at,
                               "note": "页面内容指纹（用于变更检测，重启后仍有效）"})
    return {
        "source": source_dict(s),
        "stats": {
            "total_crawls": total_logs,
            "by_status": counts,
            "success_24h": sum(1 for l in logs_24h if l.status in ("success", "partial")),
            "total_24h": len(logs_24h),
            "avg_duration_ms_24h": round(sum(durations) / len(durations)) if durations else None,
        },
        "trend_24h": _bucket_by_hour([(l.started_at, l.new_count) for l in logs_24h]),
        "trend_7d": _bucket_by_day([(l.started_at, l.new_count, l.status) for l in logs_7d]),
        "recent_logs": [{
            "id": l.id, "task_id": l.task_id, "status": l.status,
            "started_at": l.started_at, "finished_at": l.finished_at,
            "found_count": l.found_count, "new_count": l.new_count,
            "updated_count": l.updated_count, "duplicate_count": l.duplicate_count,
            "retry_count": l.retry_count, "error": l.error,
        } for l in logs[:10]],
        "recent_tasks": [{
            "id": t.id, "status": t.status, "trigger": t.trigger, "retry_of": t.retry_of,
            "attempts": t.attempts, "started_at": t.started_at, "finished_at": t.finished_at,
            "duration_ms": t.duration_ms, "found_count": t.found_count,
            "new_count": t.new_count, "updated_count": t.updated_count,
            "duplicate_count": t.duplicate_count, "error": t.error,
        } for t in tasks],
        "recent_documents": [{
            "id": d.id, "url": d.url, "title": d.title,
            "fetched_at": d.fetched_at, "content_hash": d.content_hash,
            "processed": d.processed,
        } for d in docs],
        "related_events": [{
            "id": e.id, "name": e.name, "type": e.type,
            "start_time": e.start_time, "city": e.city, "country": e.country,
            "confidence_level": e.confidence_level, "first_seen_at": e.first_seen_at,
        } for e in related_events],
        "change_records": change_records,
        "schedule": {
            "frequency_minutes": s.frequency_minutes,
            "enabled": s.enabled,
            "status": s.status,
            "effective_interval_minutes": (
                min(s.frequency_minutes * (2 ** min(s.consecutive_failures, 4)), 24 * 60)
                if s.consecutive_failures > 0 else s.frequency_minutes),
            "backoff_active": s.consecutive_failures > 0,
            "next_jobs": [j for j in sched.scheduler_status()["scheduled_jobs"]
                          if j["id"] == f"source_{s.id}"],
        },
    }
