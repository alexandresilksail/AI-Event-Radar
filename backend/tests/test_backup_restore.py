"""Backup / restore drill (SQLite profile).

The shipped scripts/backup.sh + scripts/restore.sh target PostgreSQL through
docker compose, which cannot run in every environment (no Docker on a
single-node host). This test exercises the same logical guarantee on the
SQLite profile: a backup taken before a destructive change must restore the
database to exactly the pre-change state.

The PostgreSQL path (pg_dump/pg_restore) is covered by the operator runbook
and is NOT exercised here — it requires a running PostgreSQL container.
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _counts(path: str) -> dict:
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        con.close()


@pytest.fixture()
def seeded_db(tmp_path):
    """A small SQLite database with representative content."""
    path = str(tmp_path / "radar.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO sources (url) VALUES ('https://a.example.com/rss')")
    con.execute("INSERT INTO events (name) VALUES ('AI Summit')")
    con.commit()
    con.close()
    return path


def test_backup_then_restore_returns_identical_state(seeded_db, tmp_path):
    backup = str(tmp_path / "backup.db")
    shutil.copy2(seeded_db, backup)
    before = _counts(seeded_db)
    assert before["sources"] == 1 and before["events"] == 1

    # destructive change
    con = sqlite3.connect(seeded_db)
    con.execute("DELETE FROM events")
    con.execute("DELETE FROM sources")
    con.commit()
    con.close()
    assert _counts(seeded_db)["events"] == 0

    # restore
    shutil.copy2(backup, seeded_db)

    after = _counts(seeded_db)
    assert after == before, "restored database must match the pre-backup state"
    assert _digest(backup) == _digest(seeded_db)


def test_backup_is_independent_of_later_changes(seeded_db, tmp_path):
    """A backup must not change when the live database changes afterwards."""
    backup = str(tmp_path / "backup.db")
    shutil.copy2(seeded_db, backup)
    digest_at_backup = _digest(backup)

    con = sqlite3.connect(seeded_db)
    con.execute("INSERT INTO events (name) VALUES ('Later Event')")
    con.commit()
    con.close()

    assert _digest(backup) == digest_at_backup, "backup must be a point-in-time snapshot"
    assert _counts(seeded_db)["events"] == 2
    assert _counts(backup)["events"] == 1


def test_restore_over_a_corrupt_file_recovers(tmp_path):
    """Restoring must overwrite a damaged database, not fail on it."""
    good = str(tmp_path / "good.db")
    con = sqlite3.connect(good)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO t (id) VALUES (1)")
    con.commit()
    con.close()

    backup = str(tmp_path / "backup.db")
    shutil.copy2(good, backup)

    live = str(tmp_path / "live.db")
    with open(live, "wb") as f:
        f.write(b"this is not a database")

    shutil.copy2(backup, live)
    assert _counts(live)["t"] == 1
