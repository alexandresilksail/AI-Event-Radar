"""Migration & deployment-oriented tests: alembic upgrade/downgrade, legacy DB stamping."""
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(db_path: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          capture_output=True, text=True, cwd=str(BACKEND),
                          env=env, timeout=120)


@contextmanager
def open_db(db_path: str):
    eng = create_engine(f"sqlite:///{db_path}")
    try:
        yield eng
    finally:
        eng.dispose()  # Windows: release file handle before temp dir cleanup


@pytest.fixture()
def temp_db():
    with tempfile.TemporaryDirectory() as d:
        yield os.path.join(d, "mig.db")


class TestAlembic:
    def test_upgrade_creates_all_tables(self, temp_db):
        r = _alembic(temp_db, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        with open_db(temp_db) as eng:
            names = {t for t in inspect(eng).get_table_names() if t != "alembic_version"}
            assert len(names) == 15, names
            # new audit-fix tables included
            assert {"scheduler_locks", "source_states"} <= names

    def test_downgrade_reverses(self, temp_db):
        assert _alembic(temp_db, "upgrade", "head").returncode == 0
        r = _alembic(temp_db, "downgrade", "base")
        assert r.returncode == 0, r.stderr
        with open_db(temp_db) as eng:
            names = {t for t in inspect(eng).get_table_names() if t != "alembic_version"}
            assert names == set()

    def test_history_single_head(self, temp_db):
        assert _alembic(temp_db, "upgrade", "head").returncode == 0
        r = _alembic(temp_db, "history")
        assert "baseline all radar tables" in r.stdout

    def test_legacy_db_gets_new_tables(self, temp_db):
        """A DB created by the old create_all path (no alembic_version) is topped up,
        not wiped: existing data is preserved."""
        with open_db(temp_db) as eng:
            with eng.begin() as c:
                c.execute(text("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY)"))
                c.execute(text("INSERT INTO legacy_marker (id) VALUES (42)"))
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{temp_db}"
        env["ENV"] = "dev"
        code = ("import sys; sys.path.insert(0, %r);"
                "from app.main import migrate_or_init; migrate_or_init()" % str(BACKEND))
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True,
                           cwd=str(BACKEND), env=env, timeout=120)
        assert r.returncode == 0, r.stderr
        with open_db(temp_db) as eng:
            names = {t for t in inspect(eng).get_table_names() if t != "alembic_version"}
            assert "legacy_marker" in names          # old data preserved
            assert "scheduler_locks" in names and "source_states" in names
            assert "alembic_version" in inspect(eng).get_table_names()  # stamped
            with eng.connect() as c:
                assert c.execute(text("SELECT id FROM legacy_marker")).scalar() == 42
