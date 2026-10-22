"""Worker entry point: standalone background crawl process.

Usage:  python -m app.worker
Runs the single-active scheduler (DB leader lease). API processes do not schedule.

In production, schema migrations are owned by the one-shot migrate service
(AUTO_MIGRATE=false), so workers never race with API replicas on migrations.

Embedded health server (stdlib only, daemon thread, internal port 8001):
- /health/live       process alive
- /health/ready      scheduler started (leader or standby-waiting)
- /health/scheduler  full scheduler breakdown (leader/paused/jobs/lease)
Bound to 0.0.0.0 inside the container so Docker HEALTHCHECK can curl it;
in production the port is NOT published (internal network only).
"""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import (auto_migrate_enabled, settings, validate_production_config)
from .database import SessionLocal
from .main import migrate_or_init  # legacy/dev bootstrap, gated below
from .routers.auth import ensure_default_admin
from .security import install_guarded_resolver
from .services import scheduler as sched

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("radar.worker")

HEALTH_PORT = settings.WORKER_HEALTH_PORT


class _HealthHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health/live":
            self._send(200, {"status": "alive", "role": "worker"})
        elif path == "/health/ready":
            # readiness = entered the scheduling loop (leader, or standby waiting
            # for the lease). The process is functional either way.
            self._send(200, {"status": "ready",
                             "scheduler_started": sched._scheduler is not None,  # noqa: SLF001
                             "waiting_for_lease": sched._scheduler is None})
        elif path == "/health/scheduler":
            try:
                self._send(200, sched.scheduler_status())
            except Exception as e:  # never crash the health thread
                self._send(500, {"ok": False, "error": str(e)[:200]})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args) -> None:  # silence per-request logging
        return


def _start_health_server() -> ThreadingHTTPServer | None:
    try:
        server = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    except OSError as e:
        log.warning("worker 健康检查服务启动失败（端口 %s 被占用?）: %s", HEALTH_PORT, e)
        return None
    threading.Thread(target=server.serve_forever, name="worker-health",
                     daemon=True).start()
    log.info("worker 健康检查服务已监听 :%s/health/*", HEALTH_PORT)
    return server


def main() -> None:
    # The worker is the process that actually performs outbound crawling, so it
    # MUST install the DNS-level SSRF guard itself. app.main's lifespan() only
    # runs under uvicorn (the API process) — `python -m app.worker` never
    # triggers it, which previously left the worker without TOCTOU protection.
    install_guarded_resolver()
    validate_production_config(settings)
    if auto_migrate_enabled(settings):
        migrate_or_init()
    else:
        log.info("AUTO_MIGRATE=false：worker 跳过启动迁移（由独立 migrate 服务负责）")
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()
    _start_health_server()
    ok = sched.start_scheduler()
    if not ok:
        log.warning("未获取到调度锁，进程进入空闲等待（不会重复启动采集）")
        while not sched.start_scheduler():
            time.sleep(60)
    log.info("worker 已启动（容器重启后将恢复全部调度任务）")
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        sched.stop_scheduler()


if __name__ == "__main__":
    main()
