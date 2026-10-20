"""Health endpoints — no side effects, safe for Docker HEALTHCHECK.

Layered checks (never conflate them):
- /health/live   liveness: process alive and serving (no dependencies)
- /health/ready  readiness: dependencies (DB) reachable; 503 when not
- /health        legacy combined check (kept for compatibility)
- /health/scheduler  scheduler lease state: is a valid leader elected?
  Database health is NOT the same as a healthy scheduler: this endpoint
  reports the DB-backed lease honestly, including expiry.
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.scheduler import lease_status

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live():
    """Liveness: the app process is alive and serving."""
    return {"status": "alive"}


@router.get("/ready")
def ready(response: Response, db: Session = Depends(get_db)):
    """Readiness: DB reachable. Reports failure honestly — never fakes ready."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except Exception as e:
        response.status_code = 503
        return {"status": "not-ready", "database": str(e)[:200]}


@router.get("/scheduler")
def scheduler_health():
    """Scheduler health: valid leader lease elected and not expired.

    A missing/expired lease means scheduled crawls are NOT being dispatched
    by anyone — an actionable signal, distinct from process liveness.
    """
    try:
        status = lease_status()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    ok = bool(status.get("leader_elected") and status.get("lease_valid"))
    return {"ok": ok, **status}


@router.get("")
def health(db: Session = Depends(get_db)):
    """Legacy combined health check (kept for compatibility)."""
    try:
        db.execute(text("SELECT 1"))
        return "ok: database reachable"
    except Exception as e:
        return f"error: {e}"
