"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, SessionLocal, engine
from .routers.auth import ensure_default_admin
from .routers import auth, events, notifications, sources
from .services import scheduler as sched
from .services.pipeline import refresh_event_status
from .models import Event

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("apscheduler").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        ensure_default_admin(db)
        # startup recovery: re-evaluate finished events
        for ev in db.query(Event).filter(Event.is_finished == False).all():  # noqa: E712
            refresh_event_status(db, ev)
        db.commit()
    finally:
        db.close()
    sched.start_scheduler()  # restores all jobs from DB after restart
    yield
    sched.stop_scheduler()


app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(events.router)
app.include_router(notifications.router)


@app.get("/status")
def status():
    sch = sched.scheduler_status()
    return {"app": settings.APP_NAME, "env": settings.ENV,
            "scheduler": sch,
            "collectors": ["rss", "sitemap", "html", "api", "change_detection", "search"],
            "search_configured": bool(settings.SEARCH_API_KEY),
            "ai_extraction_configured": bool(settings.AI_API_KEY)}
