"""Worker entry point: standalone background crawl process.

Usage:  python -m app.worker
Runs the single-active scheduler (DB leader lock). API processes do not schedule.
"""
import logging

from .config import settings, validate_production_config
from .database import SessionLocal
from .main import migrate_or_init  # schema is ensured by the shared bootstrap
from .routers.auth import ensure_default_admin
from .services import scheduler as sched

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("radar.worker")


def main() -> None:
    validate_production_config(settings)
    migrate_or_init()
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()
    ok = sched.start_scheduler()
    if not ok:
        log.warning("未获取到调度锁，进程进入空闲等待（不会重复启动采集）")
        import time
        while True:
            time.sleep(60)
            db = SessionLocal()
            try:
                if sched.try_acquire_lock(db):
                    sched.start_scheduler()
                    break
            finally:
                db.close()
    log.info("worker 已启动（容器重启后将恢复全部调度任务）")
    try:
        import time
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        sched.stop_scheduler()


if __name__ == "__main__":
    main()
