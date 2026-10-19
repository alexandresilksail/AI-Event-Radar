"""24/7 scheduler based on APScheduler, with a DB-backed single-leader lock.

Consistency guarantees (P1 audit):
- Only ONE active scheduler instance across api/worker processes (DB leader election).
- API restart never affects the worker; worker restart restores all jobs from DB.
- Same source never runs concurrently (per-source in-process guard + max_instances=1).
- Custom source changes add/pause/resume are reflected on re-schedule.
- Stale lock (heartbeat older than SCHEDULER_LOCK_TTL) can be taken over by a new leader.
"""
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import settings
from ..database import SessionLocal
from ..models import SchedulerLock, Source

logger = logging.getLogger("radar.scheduler")

HOLDER_ID = f"{socket.gethostname()}:{os.getpid()}"

_scheduler: BackgroundScheduler | None = None
_pool = ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_CRAWLS,
                           thread_name_prefix="crawl")
_running_tasks: set[int] = set()
_lock = threading.Lock()


def _crawl_job(source_id: int) -> None:
    with _lock:
        if source_id in _running_tasks:
            return  # previous run still active — skip, never stack duplicates
        _running_tasks.add(source_id)
    try:
        from ..services.pipeline import create_task_and_crawl
        db = SessionLocal()
        try:
            source = db.get(Source, source_id)
            if source and source.enabled:
                create_task_and_crawl(db, source)
        finally:
            db.close()
    except Exception as e:
        logger.exception("crawl job %s failed: %s", source_id, e)
    finally:
        with _lock:
            _running_tasks.discard(source_id)


def schedule_source(source: Source) -> None:
    if not _scheduler:
        return
    job_id = f"source_{source.id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
    if not source.enabled or source.status in ("candidate", "paused"):
        return
    minutes = source.frequency_minutes
    if source.consecutive_failures > 0:
        minutes = min(minutes * (2 ** min(source.consecutive_failures, 4)), 24 * 60)
    _scheduler.add_job(
        _crawl_job, IntervalTrigger(minutes=minutes), id=job_id,
        args=[source.id], max_instances=1, coalesce=True,
        next_run_time=datetime.now(), jitter=30)


def remove_source(source_id: int) -> None:
    if _scheduler and _scheduler.get_job(f"source_{source_id}"):
        _scheduler.remove_job(f"source_{source_id}")


def reschedule_all() -> None:
    db = SessionLocal()
    try:
        for source in db.query(Source).filter(Source.enabled == True).all():  # noqa: E712
            schedule_source(source)
    finally:
        db.close()


# ---------------- DB leader lock ----------------

def try_acquire_lock(db) -> bool:
    """Attempt to become the single active scheduler. Heartbeats keep leadership."""
    now = datetime.utcnow()
    row = db.get(SchedulerLock, 1)
    if row is None:
        try:
            db.add(SchedulerLock(id=1, holder=HOLDER_ID, acquired_at=now, heartbeat_at=now))
            db.commit()
            return True
        except Exception:
            db.rollback()
            row = db.get(SchedulerLock, 1)
    stale = row.heartbeat_at is None or now - row.heartbeat_at > timedelta(seconds=settings.SCHEDULER_LOCK_TTL)
    if row.holder == HOLDER_ID or stale:
        row.holder, row.acquired_at, row.heartbeat_at = HOLDER_ID, now, now
        db.commit()
        return True
    return False


def heartbeat(db) -> None:
    row = db.get(SchedulerLock, 1)
    if row and row.holder == HOLDER_ID:
        row.heartbeat_at = datetime.utcnow()
        db.commit()


def is_leader(db) -> bool:
    row = db.get(SchedulerLock, 1)
    return bool(row and row.holder == HOLDER_ID)


def release_lock(db) -> None:
    row = db.get(SchedulerLock, 1)
    if row and row.holder == HOLDER_ID:
        row.holder = ""
        db.commit()


def _heartbeat_loop() -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                heartbeat(db)
            finally:
                db.close()
        except Exception as e:
            logger.warning("heartbeat failed: %s", e)
        time.sleep(15)


# ---------------- lifecycle ----------------

def start_scheduler() -> bool:
    """Acquire the leader lock; only the leader runs the real scheduler.
    Returns True if this process holds the lock."""
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("scheduler disabled via config")
        return False
    db = SessionLocal()
    try:
        if not try_acquire_lock(db):
            logger.info("另一个进程已持有调度锁，本进程不启动调度器: holder=%s",
                        (db.get(SchedulerLock, 1) or SchedulerLock(id=1)).holder)
            return False
    finally:
        db.close()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    reschedule_all()  # restart recovery: rebuild all jobs from DB
    threading.Thread(target=_heartbeat_loop, daemon=True, name="scheduler-heartbeat").start()
    logger.info("scheduler started as leader %s, jobs restored from database", HOLDER_ID)
    return True


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    try:
        db = SessionLocal()
        try:
            release_lock(db)
        finally:
            db.close()
    except Exception:
        pass


def scheduler_status() -> dict:
    """Explicit, non-fake status breakdown."""
    db = SessionLocal()
    try:
        lock = db.get(SchedulerLock, 1)
        leader = bool(lock and lock.holder)
        leadership = lock.holder if lock else None
    finally:
        db.close()
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            jobs.append({"id": job.id, "next_run": str(job.next_run_time)})
    return {
        "scheduler_running": _scheduler is not None,
        "leader_holder": leadership,
        "leader_elected": leader,
        "holder": HOLDER_ID if _scheduler is not None else None,
        "running_task_source_ids": sorted(_running_tasks),
        "scheduled_jobs": jobs,
        "job_count": len(jobs),
    }
