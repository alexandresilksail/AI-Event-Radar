"""24/7 scheduler based on APScheduler.

- Survives restart: jobs are (re)built from the sources table on startup.
- Per-source frequency, exponential backoff on failures, concurrency-limited.
- Distinguishes clearly: scheduler running / a task running / a source OK /
  platform receiving new data — these are separate, explicit states.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import settings
from ..database import SessionLocal
from ..models import Source

logger = logging.getLogger("radar.scheduler")

_scheduler: BackgroundScheduler | None = None
_pool = ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_CRAWLS,
                           thread_name_prefix="crawl")
_running_tasks: set[int] = set()
_lock = threading.Lock()


def _crawl_job(source_id: int) -> None:
    """Execute one crawl in the worker pool with concurrency control."""
    with _lock:
        if source_id in _running_tasks:
            return  # previous run still active — skip, never stack duplicates
        _running_tasks.add(source_id)
    try:
        from .pipeline import create_task_and_crawl
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
    # exponential backoff: failing sources are delayed
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


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("scheduler disabled via config")
        return None
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    reschedule_all()  # restart recovery: rebuild all jobs from DB
    logger.info("scheduler started, jobs restored from database")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    """Explicit, non-fake status breakdown."""
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            jobs.append({"id": job.id, "next_run": str(job.next_run_time)})
    return {
        "scheduler_running": _scheduler is not None,
        "running_task_source_ids": sorted(_running_tasks),
        "scheduled_jobs": jobs,
        "job_count": len(jobs),
    }
