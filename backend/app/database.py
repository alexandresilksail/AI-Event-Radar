"""Database engine & session management. SQLite for dev, PostgreSQL for production."""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import encode_db_url, is_production, settings

DB_URL = encode_db_url(settings)
os.makedirs("data", exist_ok=True)

_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
_pool_kwargs = {}
if DB_URL.startswith("postgresql"):
    _pool_kwargs = {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True,
                    "pool_recycle": 1800, "connect_args": {"connect_timeout": 10}}

engine = create_engine(DB_URL, connect_args=_connect_args, pool_pre_ping=True, **_pool_kwargs)

if DB_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
