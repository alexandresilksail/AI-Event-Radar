"""采集任务管理 API：任务列表（筛选/分页）、任务详情（含日志与关联活动）、重试、进行中任务、批量触发。

去重与并发：
- 重试会创建新任务并通过 retry_of 关联原任务，原任务历史保留。
- 同一数据源的并发执行由调度器的进程内守卫 + APScheduler max_instances=1 阻止；
  手动触发为同步执行，天然不会与自身叠加。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import audit, current_user
from ..models import CrawlLog, CrawlTask, Event, EventSource, RawDocument, Source, utcnow
from ..services import scheduler as sched
from ..services.pipeline import create_task_and_crawl

router = APIRouter(prefix="/crawl-tasks", tags=["crawl-tasks"])

VALID_TASK_STATUS = {"running", "success", "partial", "failed"}
VALID_TRIGGERS = {"manual", "scheduled", "retry", "seed"}
STATUS_LABELS = {"running": "执行中", "success": "成功", "partial": "部分成功", "failed": "失败",
                 "pending": "等待执行"}
TRIGGER_LABELS = {"manual": "手动触发", "scheduled": "定时调度", "retry": "失败重试", "seed": "种子初始化"}


def _task_dict(t: CrawlTask, source_name: str | None = None) -> dict:
    return {
        "id": t.id, "source_id": t.source_id, "source_name": source_name,
        "status": t.status, "status_label": STATUS_LABELS.get(t.status, t.status),
        "trigger": t.trigger, "trigger_label": TRIGGER_LABELS.get(t.trigger, t.trigger),
        "retry_of": t.retry_of, "attempts": t.attempts,
        "started_at": t.started_at, "finished_at": t.finished_at,
        "duration_ms": t.duration_ms,
        "found_count": t.found_count, "new_count": t.new_count,
        "updated_count": t.updated_count, "duplicate_count": t.duplicate_count,
        "error": t.error,
    }


@router.get("")
def list_tasks(source_id: int = 0, status: str = "", trigger: str = "",
               date_from: str = "", date_to: str = "",
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
               user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """Task list (authenticated): contains source names and raw error text."""
    q = db.query(CrawlTask)
    if source_id:
        q = q.filter(CrawlTask.source_id == source_id)
    if status:
        q = q.filter(CrawlTask.status == status)
    if trigger:
        q = q.filter(CrawlTask.trigger == trigger)
    if date_from:
        q = q.filter(CrawlTask.started_at >= date_from)
    if date_to:
        q = q.filter(CrawlTask.started_at <= f"{date_to} 23:59:59")
    total = q.count()
    rows = (q.order_by(CrawlTask.started_at.desc(), CrawlTask.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    names = {s.id: s.name for s in db.query(Source).all()}
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_task_dict(t, names.get(t.source_id)) for t in rows]}


@router.get("/running")
def running_tasks(user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """正在执行的任务（DB 中的 running 任务 + 调度器实际运行中的源）。"""
    rows = (db.query(CrawlTask).filter(CrawlTask.status == "running")
            .order_by(CrawlTask.started_at.desc()).all())
    names = {s.id: s.name for s in db.query(Source).all()}
    sch = sched.scheduler_status()
    return {
        "db_running": [_task_dict(t, names.get(t.source_id)) for t in rows],
        "scheduler_running_source_ids": sch["running_task_source_ids"],
        "scheduler_is_leader": sch["scheduler_running"],
    }


@router.get("/{task_id}")
def task_detail(task_id: int, user: dict = Depends(current_user),
                db: Session = Depends(get_db)):
    """Task detail (authenticated): contains source URL and raw error text."""
    t = db.get(CrawlTask, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    source = db.get(Source, t.source_id)
    logs = (db.query(CrawlLog).filter(CrawlLog.task_id == t.id)
            .order_by(CrawlLog.started_at).all())
    # 关联活动：任务执行窗口内该源发现的活动
    related = []
    if t.started_at:
        end = t.finished_at or utcnow()
        docs = (db.query(RawDocument.url)
                .filter(RawDocument.source_id == t.source_id,
                        RawDocument.fetched_at >= t.started_at,
                        RawDocument.fetched_at <= end + timedelta(minutes=5))
                .all())
        urls = {d[0] for d in docs}
        if urls:
            related = (db.query(Event)
                       .join(EventSource, EventSource.event_id == Event.id)
                       .filter(EventSource.source_id == t.source_id,
                               or_(Event.detail_url.in_(urls), EventSource.url.in_(urls)))
                       .order_by(Event.first_seen_at.desc()).limit(20).all())
    retries = (db.query(CrawlTask).filter(CrawlTask.retry_of == t.id)
               .order_by(CrawlTask.started_at).all())
    names = {s.id: s.name for s in db.query(Source).all()}
    return {
        "task": _task_dict(t, names.get(t.source_id)),
        "source": {"id": source.id, "name": source.name, "type": source.type,
                   "url": source.url} if source else None,
        "logs": [{
            "id": l.id, "status": l.status, "started_at": l.started_at,
            "finished_at": l.finished_at, "found_count": l.found_count,
            "new_count": l.new_count, "updated_count": l.updated_count,
            "duplicate_count": l.duplicate_count, "retry_count": l.retry_count,
            "error": l.error,
            "duration_ms": (int((l.finished_at - l.started_at).total_seconds() * 1000)
                            if l.finished_at and l.started_at else None),
        } for l in logs],
        "related_events": [{
            "id": e.id, "name": e.name, "type": e.type, "start_time": e.start_time,
            "city": e.city, "country": e.country,
            "confidence_level": e.confidence_level, "first_seen_at": e.first_seen_at,
        } for e in related],
        "retries": [_task_dict(r) for r in retries],
    }


@router.post("/{task_id}/retry")
def retry_task(task_id: int, request: Request,
               user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """重试失败任务：创建新任务（trigger=retry, retry_of=原任务），同步执行一次。"""
    old = db.get(CrawlTask, task_id)
    if not old:
        raise HTTPException(404, "任务不存在")
    if old.status == "running":
        raise HTTPException(409, "任务仍在执行中，不能重复触发")
    source = db.get(Source, old.source_id)
    if not source or source.deleted_at:
        raise HTTPException(404, "关联数据源不存在或已删除")
    log = create_task_and_crawl(db, source, trigger="retry", retry_of=old.id, max_retry=1)
    new_task = db.get(CrawlTask, log.task_id) if log.task_id else None
    audit(request, user, "retry_task", f"task #{task_id} -> #{log.task_id}", db)
    return {"new_task_id": log.task_id, "status": log.status,
            "found": log.found_count, "new_events": log.new_count,
            "updated": log.updated_count, "duplicates": log.duplicate_count,
            "duration_ms": new_task.duration_ms if new_task else None,
            "error": log.error}
