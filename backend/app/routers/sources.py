from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..collectors import COLLECTORS
from ..database import get_db
from ..deps import audit, current_user
from ..models import CrawlLog, Source
from ..services.pipeline import create_task_and_crawl
from ..services import scheduler as sched

router = APIRouter(prefix="/sources", tags=["sources"])

VALID_TYPES = set(COLLECTORS.keys()) - {"search"} | {"search"}
VALID_STATUS = {"candidate", "active", "paused", "failing"}


class SourceBody(BaseModel):
    name: str
    type: str
    url: str
    country: str = "未知"
    language: str = "zh"
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


@router.get("")
def list_sources(db: Session = Depends(get_db)):
    rows = db.query(Source).order_by(Source.id).all()
    return {"items": [s.to_dict() if hasattr(s, "to_dict") else {
        "id": s.id, "name": s.name, "type": s.type, "url": s.url,
        "country": s.country, "language": s.language,
        "frequency_minutes": s.frequency_minutes, "enabled": s.enabled,
        "status": s.status, "last_success_at": s.last_success_at,
        "last_failure_at": s.last_failure_at, "last_error": s.last_error,
        "consecutive_failures": s.consecutive_failures,
        "last_item_count": s.last_item_count, "total_events_found": s.total_events_found,
        "compliance_note": s.compliance_note, "discovery_path": s.discovery_path,
    } for s in rows]}


@router.post("", status_code=201)
def create_source(body: SourceBody, request: Request,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = Source(**body.model_dump())
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
    if not s:
        raise HTTPException(404, "数据源不存在")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    sched.schedule_source(s)
    audit(request, user, "update_source", f"source #{s.id}", db)
    return {"id": s.id, "status": s.status}


@router.post("/{source_id}/pause")
def pause_source(source_id: int, request: Request,
                 user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s:
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
    if not s:
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
    if not s:
        raise HTTPException(404, "数据源不存在")
    s.status, s.enabled = "active", True
    db.commit()
    sched.schedule_source(s)
    audit(request, user, "approve_source", f"source #{s.id}", db)
    return {"id": s.id, "status": s.status}


@router.delete("/{source_id}")
def delete_source(source_id: int, request: Request,
                  user: dict = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Source, source_id)
    if not s:
        raise HTTPException(404, "数据源不存在")
    db.delete(s)
    db.commit()
    sched.remove_source(source_id)
    audit(request, user, "delete_source", f"source #{source_id}", db)
    return {"deleted": source_id}


@router.post("/{source_id}/trigger")
def trigger_source(source_id: int, request: Request,
                   user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """Manual crawl of a single source (synchronous, with retry/backoff)."""
    s = db.get(Source, source_id)
    if not s:
        raise HTTPException(404, "数据源不存在")
    log = create_task_and_crawl(db, s)
    audit(request, user, "trigger_source", f"source #{s.id}", db)
    return {"log_id": log.id, "status": log.status, "found": log.found_count,
            "new_events": log.new_count, "updated": log.updated_count,
            "duplicates": log.duplicate_count, "error": log.error}


@router.post("/{source_id}/test")
def test_source(source_id: int, db: Session = Depends(get_db)):
    """Connectivity test without touching event pipeline."""
    s = db.get(Source, source_id)
    if not s:
        raise HTTPException(404, "数据源不存在")
    from ..collectors import get_collector, CollectorError
    try:
        items = get_collector(s.type).fetch(s)
        return {"ok": True, "items": len(items)}
    except CollectorError as e:
        return {"ok": False, "error": str(e)}
