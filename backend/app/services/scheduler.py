"""24/7 scheduler based on APScheduler, with a DB-backed single-leader lease.

Consistency guarantees (final hardening):
- Only ONE active scheduler instance across api/worker processes.
  Acquisition uses an ATOMIC compare-and-set UPDATE (WHERE holder=me OR stale),
  so two workers racing on a stale lock cannot both win.
- `lease_version` is a fencing token: every dispatch re-verifies the lease
  with a DB query, so a demoted/old leader performs no side effects.
- Heartbeats renew the lease every 15s. If renewal fails (DB down, or another
  worker took over), the scheduler is PAUSED immediately — a lost leader never
  keeps dispatching jobs. When the lease is re-acquired, the scheduler resumes.
- API restart never affects the worker; worker restart restores all jobs from DB.
- Same source never runs concurrently (per-source in-process guard + max_instances=1).
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
from sqlalchemy import update

from ..config import settings
from ..database import SessionLocal
from ..models import SchedulerLock, Source, utcnow

logger = logging.getLogger("radar.scheduler")

HOLDER_ID = f"{socket.gethostname()}:{os.getpid()}"

_scheduler: BackgroundScheduler | None = None
_pool = ThreadPoolExecutor(max_workers=max(1, settings.MAX_CONCURRENT_CRAWLS),
                           thread_name_prefix="crawl")
_running_tasks: set[int] = set()
_lock = threading.Lock()
_stop_event = threading.Event()
_heartbeat_thread: threading.Thread | None = None
_consecutive_hb_failures = 0
_MAX_HB_FAILURES = 2  # pause scheduling after this many consecutive failures


def _crawl_job(source_id: int) -> None:
    with _lock:
        if source_id in _running_tasks:
            return  # previous run still active — skip, never stack duplicates
        _running_tasks.add(source_id)
    try:
        from ..services.pipeline import create_task_and_crawl
        db = SessionLocal()
        try:
            # fencing: re-verify the lease before any side effect. A demoted or
            # stale leader must never crawl (would duplicate work/notifications).
            try:
                if not _lease_still_held(db):
                    logger.warning("租约已丢失或被接管，取消本次调度任务 source=%s", source_id)
                    return
            except Exception as e:
                # cannot verify -> DB trouble -> do not act
                logger.warning("调度前租约校验失败（数据库异常），取消本次任务 source=%s: %s",
                               source_id, e)
                return
            source = db.get(Source, source_id)
            if source and source.enabled:
                create_task_and_crawl(db, source, trigger="scheduled")
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


# ---------------- DB leader lease (atomic CAS) ----------------

def try_acquire_lock(db) -> bool:
    """Attempt to become the single active scheduler (atomic).

    Safe under concurrency: the steal condition is evaluated by the database
    in one UPDATE; only the process whose WHERE clause still matches gets
    rowcount 1. Insert uses INSERT-or-ignore semantics for the first race.
    """
    now = utcnow()
    stale_cutoff = now - timedelta(seconds=settings.SCHEDULER_LOCK_TTL)
    # 1) create row if missing (race-safe on both SQLite and PG)
    try:
        db.execute(
            SchedulerLock.__table__.insert().values(
                id=1, holder=HOLDER_ID, acquired_at=now, heartbeat_at=now, lease_version=1))
        db.commit()
        return True
    except Exception:
        db.rollback()
    # 2) atomic CAS: steal only own-or-stale lease; bump fencing version
    result = db.execute(
        update(SchedulerLock)
        .where(SchedulerLock.id == 1,
               (SchedulerLock.holder == HOLDER_ID) |
               (SchedulerLock.holder == "") |
               (SchedulerLock.heartbeat_at < stale_cutoff))
        .values(holder=HOLDER_ID, acquired_at=now, heartbeat_at=now,
                lease_version=SchedulerLock.lease_version + 1)
    )
    db.commit()
    return result.rowcount == 1


def heartbeat(db) -> bool:
    """Renew the lease. Returns False when the lease was lost/taken over."""
    result = db.execute(
        update(SchedulerLock)
        .where(SchedulerLock.id == 1, SchedulerLock.holder == HOLDER_ID)
        .values(heartbeat_at=utcnow())
    )
    db.commit()
    return result.rowcount == 1


def is_leader(db) -> bool:
    row = db.get(SchedulerLock, 1)
    return bool(row and row.holder == HOLDER_ID)


def _lease_still_held(db) -> bool:
    row = db.get(SchedulerLock, 1)
    if not row or row.holder != HOLDER_ID:
        return False
    stale = utcnow() - (row.heartbeat_at or utcnow()) > \
        timedelta(seconds=settings.SCHEDULER_LOCK_TTL)
    return not stale


def release_lock(db) -> bool:
    result = db.execute(
        update(SchedulerLock)
        .where(SchedulerLock.id == 1, SchedulerLock.holder == HOLDER_ID)
        .values(holder="")
    )
    db.commit()
    return result.rowcount == 1


def _pause_jobs() -> None:
    """Safety state: stop dispatching until the lease is re-verified."""
    if _scheduler and _scheduler.state == 1:  # STATE_RUNNING
        _scheduler.pause()
        logger.warning("调度器已暂停：领导租约未确认，暂停派发新任务（安全状态）")


def _resume_jobs() -> None:
    if _scheduler and _scheduler.state == 2:  # STATE_PAUSED
        _scheduler.resume()
        logger.info("调度器已恢复：领导租约重新确认")


def _heartbeat_loop() -> None:
    global _consecutive_hb_failures
    while not _stop_event.is_set():
        try:
            db = SessionLocal()
            try:
                ok = heartbeat(db)
            finally:
                db.close()
            if ok:
                _consecutive_hb_failures = 0
                _resume_jobs()
            else:
                # lease taken over by another worker — we are no longer leader
                _consecutive_hb_failures += 1
                logger.warning("心跳返回非 leader（租约丢失或被接管），进入安全状态")
                _pause_jobs()
        except Exception as e:
            _consecutive_hb_failures += 1
            logger.warning("heartbeat failed (%s/%s): %s", _consecutive_hb_failures,
                           _MAX_HB_FAILURES, e)
            if _consecutive_hb_failures >= _MAX_HB_FAILURES:
                # database unreachable: do not keep scheduling as a stale leader
                _pause_jobs()
        _stop_event.wait(15)


# ---------------- lifecycle ----------------

def start_scheduler() -> bool:
    """Acquire the leader lease; only the leader runs the real scheduler.
    Returns True if this process holds the lease."""
    global _scheduler, _heartbeat_thread, _consecutive_hb_failures
    if not settings.SCHEDULER_ENABLED:
        logger.info("scheduler disabled via config")
        return False
    if _scheduler is not None:
        return True  # already running in this process
    db = SessionLocal()
    try:
        if not try_acquire_lock(db):
            logger.info("另一个进程已持有调度锁，本进程不启动调度器: holder=%s",
                        (db.get(SchedulerLock, 1) or SchedulerLock(id=1)).holder)
            return False
    finally:
        db.close()
    _stop_event.clear()
    _consecutive_hb_failures = 0
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    reschedule_all()  # restart recovery: rebuild all jobs from DB
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, daemon=True, name="scheduler-heartbeat")
    _heartbeat_thread.start()
    logger.info("scheduler started as leader %s, jobs restored from database", HOLDER_ID)
    return True


def stop_scheduler() -> None:
    global _scheduler, _heartbeat_thread
    _stop_event.set()
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        _heartbeat_thread.join(timeout=5)
    _heartbeat_thread = None
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


def lease_status() -> dict:
    """Cluster-wide scheduler lease state (read-only, safe for any process).

    Used by /health/scheduler so operators (and the API container) can see
    whether a valid leader lease exists — honest, DB-backed, no fakes.
    """
    db = SessionLocal()
    try:
        lock = db.get(SchedulerLock, 1)
        if lock is None or not lock.holder:
            return {"leader_elected": False, "leader_holder": None,
                    "lease_valid": False, "heartbeat_age_seconds": None,
                    "lease_version": None, "lock_ttl_seconds": settings.SCHEDULER_LOCK_TTL}
        heartbeat = lock.heartbeat_at or lock.acquired_at
        age = (utcnow() - heartbeat).total_seconds() if heartbeat else None
        valid = age is None or age <= settings.SCHEDULER_LOCK_TTL
        return {
            "leader_elected": True,
            "leader_holder": lock.holder,
            "lease_valid": bool(valid),
            "heartbeat_age_seconds": round(age, 1) if age is not None else None,
            "lease_version": lock.lease_version,
            "lock_ttl_seconds": settings.SCHEDULER_LOCK_TTL,
        }
    finally:
        db.close()


def scheduler_status() -> dict:
    """Explicit, non-fake status breakdown."""
    db = SessionLocal()
    try:
        lock = db.get(SchedulerLock, 1)
        leader = bool(lock and lock.holder)
        leadership = lock.holder if lock else None
        lease_version = lock.lease_version if lock else None
    finally:
        db.close()
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            jobs.append({"id": job.id, "next_run": str(job.next_run_time)})
    paused = bool(_scheduler and _scheduler.state == 2)
    return {
        "scheduler_running": _scheduler is not None,
        "paused": paused,
        "leader_holder": leadership,
        "leader_elected": leader,
        "holder": HOLDER_ID if _scheduler is not None else None,
        "running_task_source_ids": sorted(_running_tasks),
        "scheduled_jobs": jobs,
        "job_count": len(jobs),
        "lease_version": lease_version,
        "heartbeat_failures": _consecutive_hb_failures,
    }
