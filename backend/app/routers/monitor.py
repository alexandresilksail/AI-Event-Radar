"""系统监控 API：API/Worker/DB 健康、24h 采集统计、最近错误、磁盘与连续运行时间。

原则：所有数字来自真实查询；不可获取时明确返回 null/unknown，不用固定值伪装。
"""
import os
import shutil
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, text

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import CrawlLog, Source
from ..services import scheduler as sched

router = APIRouter(prefix="/monitor", tags=["monitor"])

BOOT_AT = datetime.utcnow()  # 本进程启动时间（重启后归零，反映本次连续运行）


@router.get("")
def monitor(db=Depends(get_db)):
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)

    # --- database ---
    db_ok, db_error = True, ""
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_ok, db_error = False, type(e).__name__

    # --- worker / scheduler leadership ---
    lock = None
    try:
        from ..models import SchedulerLock
        lock = db.get(SchedulerLock, 1)
    except Exception:
        pass
    worker = {"status": "not_started", "holder": None, "heartbeat_age_seconds": None,
              "leader_elected": False}
    if lock and lock.holder:
        age = (now - lock.heartbeat_at).total_seconds() if lock.heartbeat_at else None
        alive = age is not None and age <= settings.SCHEDULER_LOCK_TTL
        worker = {"status": "healthy" if alive else "stale",
                  "holder": lock.holder,
                  "heartbeat_age_seconds": round(age) if age is not None else None,
                  "leader_elected": True}
    sch = sched.scheduler_status()

    # --- 24h crawl stats ---
    logs = (db.query(CrawlLog)
            .filter(CrawlLog.started_at >= day_ago).all())
    by_status = {"success": 0, "partial": 0, "failed": 0, "running": 0}
    durations = []
    for l in logs:
        if l.status in by_status:
            by_status[l.status] += 1
        if l.finished_at and l.started_at:
            durations.append((l.finished_at - l.started_at).total_seconds() * 1000)
    total_24h = len(logs)
    finished_24h = by_status["success"] + by_status["partial"] + by_status["failed"]
    success_rate = round(by_status["success"] / finished_24h, 4) if finished_24h else None

    # --- sources health ---
    sources = db.query(Source).filter(Source.deleted_at.is_(None)).all()
    health = {"healthy": 0, "failing": 0, "paused": 0, "never": 0}
    for s in sources:
        if not s.enabled:
            health["paused"] += 1
        elif s.consecutive_failures > 0 or s.status == "failing":
            health["failing"] += 1
        elif s.last_success_at is None:
            health["never"] += 1
        else:
            health["healthy"] += 1

    # --- recent errors (脱敏：只保留错误类型与前 200 字符) ---
    recent_errors = []
    for l in (db.query(CrawlLog).filter(CrawlLog.status == "failed")
              .order_by(CrawlLog.started_at.desc()).limit(10).all()):
        src = db.get(Source, l.source_id)
        recent_errors.append({
            "log_id": l.id, "source_id": l.source_id,
            "source_name": src.name if src else str(l.source_id),
            "at": l.started_at, "error": (l.error or "")[:200],
        })

    # --- disk ---
    disk = None
    try:
        usage = shutil.disk_usage(os.path.abspath("."))
        disk = {"total_gb": round(usage.total / 1024 ** 3, 1),
                "used_gb": round(usage.used / 1024 ** 3, 1),
                "free_gb": round(usage.free / 1024 ** 3, 1),
                "used_percent": round(usage.used / usage.total * 100, 1)}
    except Exception:
        disk = None

    # --- last success & warnings ---
    last_success = (db.query(CrawlLog.started_at)
                    .filter(CrawlLog.status == "success")
                    .order_by(CrawlLog.started_at.desc()).first())
    last_success_at = last_success[0] if last_success else None
    hours_since_success = (round((now - last_success_at).total_seconds() / 3600, 1)
                           if last_success_at else None)

    warnings = []
    if not db_ok:
        warnings.append({"level": "critical", "msg": "数据库连接异常"})
    if worker["status"] != "healthy":
        warnings.append({"level": "critical" if worker["status"] == "not_started" else "warning",
                         "msg": f"采集 Worker 未正常运行（{worker['status']}）"})
    if success_rate is not None and success_rate < 0.8:
        warnings.append({"level": "warning", "msg": f"24 小时采集成功率低于 80%（当前 {success_rate:.0%}）"})
    if health["failing"] > 0:
        warnings.append({"level": "warning", "msg": f"{health['failing']} 个数据源连续失败"})
    if hours_since_success is not None and hours_since_success > 24:
        warnings.append({"level": "warning",
                         "msg": f"已超过 {hours_since_success} 小时没有成功采集"})

    return {
        "generated_at": now,
        "env": settings.ENV, "role": settings.ROLE,
        "uptime_seconds": int((now - BOOT_AT).total_seconds()),
        "components": {
            "api": {"status": "healthy", "role": settings.ROLE},
            "worker": worker,
            "database": {"status": "healthy" if db_ok else "down", "error": db_error or None},
        },
        "scheduler": {
            "running": sch["scheduler_running"],
            "job_count": sch["job_count"],
            "jobs": sch["scheduled_jobs"][:20],
            "running_source_ids": sch["running_task_source_ids"],
        },
        "crawl_24h": {
            "total": total_24h, "by_status": by_status,
            "success_rate": success_rate,
            "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
            "running_now": len(sch["running_task_source_ids"]),
        },
        "sources": {"total": len(sources), "health": health},
        "recent_errors": recent_errors,
        "last_success_crawl_at": last_success_at,
        "hours_since_last_success": hours_since_success,
        "disk": disk,
        "warnings": warnings,
        "notes": {
            "queue": "无独立消息队列：采集由 Worker 进程内线程池执行（max_workers=%d）" % settings.MAX_CONCURRENT_CRAWLS,
            "search_configured": bool(settings.SEARCH_API_KEY),
            "ai_extraction_configured": bool(settings.AI_API_KEY),
        },
    }
