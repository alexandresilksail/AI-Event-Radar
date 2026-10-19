"""Health endpoints — no side effects, safe for Docker HEALTHCHECK."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db

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


@router.get("")
def health(db: Session = Depends(get_db)):
    """Legacy combined health check (kept for compatibility)."""
    try:
        db.execute(text("SELECT 1"))
        return "ok: database reachable"
    except Exception as e:
        return f"error: {e}"
