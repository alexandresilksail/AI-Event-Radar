"""FastAPI application entry point.

Startup hardening:
- Production refuses to boot with unsafe config (see config.validate_production_config).
- Database schema: alembic upgrade head on fresh DBs; legacy DBs (created by
  create_all, no alembic_version) are topped up with any missing tables and stamped.
- Scheduler runs only in ROLE=worker|all, guarded by a DB leader lock.
"""
import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from .config import encode_db_url, is_production, settings, validate_production_config
from .database import Base, SessionLocal, engine
from .routers import auth, events, health, notifications, sources
from .routers.auth import ensure_default_admin
from .security import install_guarded_resolver
from .services.pipeline import refresh_event_status
from .services import scheduler as sched
from .models import Event

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("apscheduler").setLevel(logging.WARNING)

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173",
               "http://localhost:4173", "http://127.0.0.1:4173"]


def allowed_origins() -> list[str]:
    if settings.ALLOWED_ORIGINS.strip():
        return [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    if is_production(settings.ENV):
        raise SystemExit("⛔ 生产环境必须显式配置 ALLOWED_ORIGINS（逗号分隔），拒绝默认通配")
    return DEV_ORIGINS


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", encode_db_url(settings))
    return cfg


def migrate_or_init() -> None:
    """Fresh DB: run migrations. Legacy DB without alembic_version: add missing tables, stamp baseline."""
    insp = inspect(engine)
    has_tables = bool(insp.get_table_names())
    stamped = "alembic_version" in insp.get_table_names()
    if stamped:
        command.upgrade(_alembic_cfg(), "head")
        return
    if not has_tables:
        command.upgrade(_alembic_cfg(), "head")
        return
    # legacy DB: ensure every current table exists, then stamp baseline (data preserved)
    Base.metadata.create_all(engine)
    command.stamp(_alembic_cfg(), "8bcc2e0aae52")


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_guarded_resolver()  # DNS-level SSRF guard for all outbound connections
    validate_production_config(settings)  # fail fast on unsafe production config
    migrate_or_init()
    db = SessionLocal()
    try:
        ensure_default_admin(db)
        # startup recovery: re-evaluate finished events
        for ev in db.query(Event).filter(Event.is_finished == False).all():  # noqa: E712
            refresh_event_status(db, ev)
        db.commit()
    finally:
        db.close()
    # scheduler only in this role, and only while holding the DB leader lock
    if settings.ROLE in ("worker", "all") and settings.SCHEDULER_ENABLED:
        sched.start_scheduler()  # restores all jobs from DB after restart
    else:
        logging.info("role=%s -> scheduler not started in this process", settings.ROLE)
    yield
    sched.stop_scheduler()


app = FastAPI(title=settings.APP_NAME, version="1.1.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=allowed_origins(),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(events.router)
app.include_router(health.router)
app.include_router(notifications.router)


@app.get("/status")
def status():
    sch = sched.scheduler_status()
    return {"app": settings.APP_NAME, "env": settings.ENV, "role": settings.ROLE,
            "scheduler": sch,
            "collectors": ["rss", "sitemap", "html", "api", "change_detection", "search"],
            "search_configured": bool(settings.SEARCH_API_KEY),
            "ai_extraction_configured": bool(settings.AI_API_KEY)}


@app.get("/")
def root():
    return {"app": settings.APP_NAME, "docs": "/docs", "health": "/health/live"}
