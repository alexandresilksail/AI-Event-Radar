"""Database engine & session management. SQLite for dev, PostgreSQL for production."""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import encode_db_url, is_production, settings

DB_URL = encode_db_url(settings)
os.makedirs("data", exist_ok=True)

# Engine options.
#
# The PostgreSQL branch used to be merged into `create_engine()` with `**`,
# while `connect_args` and `pool_pre_ping` were ALSO passed positionally by
# keyword. On PostgreSQL that produced
#     TypeError: create_engine() got multiple values for keyword argument
#     'pool_pre_ping'
# at import time — i.e. the API and the worker could not start in production
# at all. Only SQLite ever ran, so the test suite never noticed. Build ONE
# options dict instead, so the two branches cannot collide.
_engine_kwargs: dict = {"pool_pre_ping": True}
if DB_URL.startswith("postgresql"):
    _engine_kwargs.update({
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 1800,
        "connect_args": {"connect_timeout": 10},
    })
else:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DB_URL, **_engine_kwargs)

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
