"""Regression tests for scheduler lease fencing on the WRITE path (finding #3).

The defect
----------
`lease_version` was documented as a fencing token, but it only ever guarded
the *dispatch* decision:

    _crawl_job:  verify lease -> create_task_and_crawl(...) -> commit results

The verification happened BEFORE the crawl. The crawl itself performs the
network fetch and may take many seconds. If the lease was taken over during
that window (another worker saw a stale heartbeat, or the DB hiccuped), the
demoted leader still committed its events, raw documents and notifications.
The fencing token existed in the schema but protected nothing on the write
path.

The fix
-------
`LeaseGuard` captures `lease_version` when the job starts and is checked
immediately before every business commit. If the version moved on (the lease
was taken over and re-acquired) or the lease went stale, the transaction is
rolled back and nothing is persisted.

These tests use REAL database writes — they assert that no Event row exists
after a fenced-out crawl, which is the property that actually matters.
"""
import os
import sys
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CrawlLog, Event, RawDocument, SchedulerLock, Source, utcnow

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def db(tmp_path):
    """An isolated SQLite database with the full schema."""
    import app.models  # noqa: F401  (register every model)
    eng = create_engine(f"sqlite:///{tmp_path / 'fence.db'}",
                        connect_args={"timeout": 30})
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        eng.dispose()


def _seed_source(db) -> Source:
    s = Source(name="围栏测试源", type="rss",
               url="https://feed.example.com/rss", enabled=True,
               status="active", frequency_minutes=60)
    db.add(s)
    db.commit()
    return s


def _hold_lease(db, holder: str, version: int = 1):
    """Write a lease row owned by `holder`."""
    now = utcnow()
    db.execute(
        SchedulerLock.__table__.insert().values(
            id=1, holder=holder, acquired_at=now, heartbeat_at=now,
            lease_version=version)
        if not db.query(SchedulerLock).filter_by(id=1).first()
        else SchedulerLock.__table__.update().where(
            SchedulerLock.id == 1).values(
            holder=holder, acquired_at=now, heartbeat_at=now,
            lease_version=version)
    )
    db.commit()


def _steal_lease(db, new_holder: str, version: int):
    """Simulate another worker taking the lease over (version bumped)."""
    row = db.query(SchedulerLock).filter_by(id=1).first()
    row.holder = new_holder
    row.lease_version = version
    row.heartbeat_at = utcnow()
    db.commit()


def _age_lease(db, seconds: int):
    row = db.query(SchedulerLock).filter_by(id=1).first()
    row.heartbeat_at = utcnow() - timedelta(seconds=seconds)
    db.commit()


# --------------------------------------------------------------------------
# 1. LeaseGuard basics
# --------------------------------------------------------------------------
class TestLeaseGuard:
    def test_guard_is_inactive_without_a_lease(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "nobody"
        guard = sched.LeaseGuard(db)
        assert guard.active is False
        assert guard.token() is None

    def test_guard_captures_the_current_version(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=7)
        guard = sched.LeaseGuard(db)
        assert guard.active is True
        assert guard.token() == 7

    def test_check_passes_while_the_lease_is_untouched(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        guard = sched.LeaseGuard(db)
        guard.check(db)  # must not raise

    def test_check_raises_after_takeover(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        guard = sched.LeaseGuard(db)
        _steal_lease(db, "other", version=2)
        with pytest.raises(sched.LeaseLostError):
            guard.check(db)

    def test_check_raises_after_the_lease_goes_stale(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        guard = sched.LeaseGuard(db)
        _age_lease(db, 10_000)  # far beyond SCHEDULER_LOCK_TTL
        with pytest.raises(sched.LeaseLostError):
            guard.check(db)

    def test_guard_is_permanently_disabled_after_being_fenced_out(self, db):
        """Once fenced out, a process must not write anything else — even if
        it later re-acquires leadership."""
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        guard = sched.LeaseGuard(db)
        _steal_lease(db, "other", version=2)
        with pytest.raises(sched.LeaseLostError):
            guard.check(db)
        assert guard.active is False
        # even if we take the lease back, the guard stays closed
        _steal_lease(db, "me", version=3)
        with pytest.raises(sched.LeaseLostError):
            guard.check(db)

    def test_check_fails_closed_when_the_db_errors(self, db, monkeypatch):
        """If the lease cannot be verified (DB trouble), writing is refused."""
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        guard = sched.LeaseGuard(db)

        def boom(*a, **kw):
            raise RuntimeError("database is down")

        monkeypatch.setattr(sched, "current_lease_version", boom)
        with pytest.raises(sched.LeaseLostError):
            guard.check(db)
        assert guard.active is False


# --------------------------------------------------------------------------
# 2. lease_still_valid — the version comparison itself
# --------------------------------------------------------------------------
class TestLeaseStillValid:
    def test_same_version_is_valid(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=5)
        assert sched.lease_still_valid(db, 5) is True

    def test_version_moved_on_is_invalid(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=5)
        _steal_lease(db, "me", version=6)  # SAME holder, NEW version
        assert sched.lease_still_valid(db, 5) is False

    def test_none_token_is_never_valid(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        assert sched.lease_still_valid(db, None) is False

    def test_other_holder_is_invalid(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "someone-else", version=1)
        assert sched.lease_still_valid(db, 1) is False


# --------------------------------------------------------------------------
# 3. The write path: no Event may survive a fenced-out crawl
# --------------------------------------------------------------------------
class TestCrawlWritePathIsFenced:
    """The property that matters: after a lease is lost mid-crawl, the
    database must contain NO event and NO raw document from that crawl."""

    def _fake_collector(self, monkeypatch, items):
        """Install a collector that returns the given items."""
        from app.collectors.base import CollectedItem
        from app.services import pipeline as pipe

        class FakeCollector:
            def fetch(self, source):
                return [CollectedItem(url=it[0], title=it[1], content=it[2])
                        for it in items]

        monkeypatch.setattr(pipe, "get_collector", lambda t: FakeCollector())
        monkeypatch.setattr(pipe, "is_ai_related", lambda text: True)
        monkeypatch.setattr(pipe, "extract", lambda item: {
            "is_event": True, "name": item.title, "type": "会议",
            "organizer": "某组织", "start_time": None, "end_time": None,
            "city": "上海", "country": "中国", "online": False,
            "registration_url": "", "summary": "", "extraction_method": "rule",
            "ai_confidence": 0.9,
        })
        monkeypatch.setattr(pipe, "dispatch_new_event", lambda db, ev, **kw: 0)

    def test_events_are_written_when_the_lease_is_held(self, db, monkeypatch):
        from app.services import scheduler as sched
        from app.services import pipeline as pipe
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        src = _seed_source(db)
        self._fake_collector(monkeypatch, [
            ("https://example.com/evt1", "2026 AI 大会",
             "2026年5月1日上海，人工智能大会，报名参会。")])
        guard = sched.LeaseGuard(db)
        pipe.crawl_source(db, src, task_id=None, lease_guard=guard)
        assert db.query(Event).count() >= 1, "sanity: the happy path must write"

    def test_no_event_is_written_after_the_lease_is_taken_over(self, db, monkeypatch):
        """THE regression: the lease is stolen between the start of the crawl
        and the commit of the first event. Nothing may be persisted."""
        from app.services import scheduler as sched
        from app.services import pipeline as pipe
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        src = _seed_source(db)

        # Steal the lease at the moment the collector returns, i.e. AFTER the
        # crawl started but BEFORE the first business commit.
        from app.collectors.base import CollectedItem

        class StealingCollector:
            def fetch(self, source):
                _steal_lease(db, "other-worker", version=2)
                return [CollectedItem(
                    url="https://example.com/evt1", title="2026 AI 大会",
                    content="2026年5月1日上海，人工智能大会，报名参会。")]

        monkeypatch.setattr(pipe, "get_collector", lambda t: StealingCollector())
        monkeypatch.setattr(pipe, "is_ai_related", lambda text: True)
        monkeypatch.setattr(pipe, "extract", lambda item: {
            "is_event": True, "name": item.title, "type": "会议",
            "organizer": "某组织", "start_time": None, "end_time": None,
            "city": "上海", "country": "中国", "online": False,
            "registration_url": "", "summary": "", "extraction_method": "rule",
            "ai_confidence": 0.9,
        })
        monkeypatch.setattr(pipe, "dispatch_new_event", lambda db, ev, **kw: 0)

        guard = sched.LeaseGuard(db)
        log = pipe.crawl_source(db, src, task_id=None, lease_guard=guard)

        assert db.query(Event).count() == 0, \
            "a fenced-out leader must not persist any event"
        assert db.query(RawDocument).count() == 0, \
            "a fenced-out leader must not persist raw documents"
        assert log.status == "failed", "the aborted crawl must be recorded as failed"

    def test_no_event_is_written_when_the_lease_goes_stale(self, db, monkeypatch):
        from app.services import scheduler as sched
        from app.services import pipeline as pipe
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        src = _seed_source(db)

        from app.collectors.base import CollectedItem

        class StalingCollector:
            def fetch(self, source):
                _age_lease(db, 10_000)
                return [CollectedItem(
                    url="https://example.com/evt1", title="2026 AI 大会",
                    content="2026年5月1日上海，人工智能大会，报名参会。")]

        monkeypatch.setattr(pipe, "get_collector", lambda t: StalingCollector())
        monkeypatch.setattr(pipe, "is_ai_related", lambda text: True)
        monkeypatch.setattr(pipe, "extract", lambda item: {
            "is_event": True, "name": item.title, "type": "会议",
            "organizer": "某组织", "start_time": None, "end_time": None,
            "city": "上海", "country": "中国", "online": False,
            "registration_url": "", "summary": "", "extraction_method": "rule",
            "ai_confidence": 0.9,
        })
        monkeypatch.setattr(pipe, "dispatch_new_event", lambda db, ev, **kw: 0)

        guard = sched.LeaseGuard(db)
        pipe.crawl_source(db, src, task_id=None, lease_guard=guard)
        assert db.query(Event).count() == 0

    def test_manual_crawl_without_a_guard_still_writes(self, db, monkeypatch):
        """Manual/API crawls are authorized by the request, not by the lease:
        they must keep working with lease_guard=None."""
        from app.services import scheduler as sched
        from app.services import pipeline as pipe
        sched.HOLDER_ID = "me"
        src = _seed_source(db)
        self._fake_collector(monkeypatch, [
            ("https://example.com/evt2", "2026 AI 论坛",
             "2026年6月1日北京，人工智能论坛，报名。")])
        pipe.crawl_source(db, src, task_id=None, lease_guard=None)
        assert db.query(Event).count() >= 1

    def test_the_aborted_crawl_does_not_touch_source_success_state(self, db, monkeypatch):
        """A lost lease says nothing about the source's health — the source
        must not be marked as successfully crawled."""
        from app.services import scheduler as sched
        from app.services import pipeline as pipe
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=1)
        src = _seed_source(db)

        from app.collectors.base import CollectedItem

        class StealingCollector:
            def fetch(self, source):
                _steal_lease(db, "other-worker", version=2)
                return [CollectedItem(
                    url="https://example.com/evt1", title="2026 AI 大会",
                    content="2026年5月1日上海，人工智能大会，报名参会。")]

        monkeypatch.setattr(pipe, "get_collector", lambda t: StealingCollector())
        monkeypatch.setattr(pipe, "is_ai_related", lambda text: True)
        monkeypatch.setattr(pipe, "extract", lambda item: {
            "is_event": True, "name": item.title, "type": "会议",
            "organizer": "某组织", "start_time": None, "end_time": None,
            "city": "上海", "country": "中国", "online": False,
            "registration_url": "", "summary": "", "extraction_method": "rule",
            "ai_confidence": 0.9,
        })
        monkeypatch.setattr(pipe, "dispatch_new_event", lambda db, ev, **kw: 0)

        guard = sched.LeaseGuard(db)
        pipe.crawl_source(db, src, task_id=None, lease_guard=guard)
        db.refresh(src)
        assert src.last_crawl_status != "success"
        assert src.consecutive_failures == 0, \
            "a lease loss is not a source failure"


# --------------------------------------------------------------------------
# 4. heartbeat() must be version-aware
# --------------------------------------------------------------------------
class TestHeartbeatVersioning:
    def test_heartbeat_renews_while_the_version_matches(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=3)
        assert sched.heartbeat(db) is True

    def test_heartbeat_fails_when_another_holder_took_over(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        _hold_lease(db, "me", version=3)
        _steal_lease(db, "other", version=4)
        assert sched.heartbeat(db) is False

    def test_heartbeat_fails_when_the_row_is_missing(self, db):
        from app.services import scheduler as sched
        sched.HOLDER_ID = "me"
        assert sched.heartbeat(db) is False


# --------------------------------------------------------------------------
# 5. Multi-process: an old leader must not write after a takeover
# --------------------------------------------------------------------------
_WORKER_SRC = r'''
import os, sys, json, time
sys.path.insert(0, sys.argv[1])
BACKEND = sys.argv[1]
DB = sys.argv[2]
MODE = sys.argv[3]
HOLDER = sys.argv[4]

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import Base
import app.models  # noqa: F401
from app.models import Event, SchedulerLock, utcnow
from app.services import scheduler as sched

sched.HOLDER_ID = HOLDER

eng = create_engine("sqlite:///" + DB, connect_args={"timeout": 30})
Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

def run():
    db = Session()
    try:
        if MODE == "hold":
            # become leader, report the version, then wait to be fenced out
            ok = sched.try_acquire_lock(db)
            guard = sched.LeaseGuard(db)
            print(json.dumps({"acquired": ok, "version": guard.token()}),
                  flush=True)
            # wait until someone else takes the lease over
            for _ in range(200):
                time.sleep(0.05)
                row = db.query(SchedulerLock).filter_by(id=1).first()
                if row and row.holder != HOLDER:
                    break
            # now try to write through the guard — must be refused
            try:
                guard.check(db)
                wrote = True
            except sched.LeaseLostError:
                wrote = False
            print(json.dumps({"fenced": not wrote}), flush=True)
            return 0
        if MODE == "steal":
            time.sleep(0.6)
            db2 = Session()
            try:
                # force the lease stale, then take it over
                row = db2.query(SchedulerLock).filter_by(id=1).first()
                if row:
                    row.heartbeat_at = utcnow() - __import__("datetime").timedelta(seconds=99999)
                    db2.commit()
                ok = sched.try_acquire_lock(db2)
                print(json.dumps({"stole": ok,
                                  "holder": (db2.query(SchedulerLock).filter_by(id=1).first() or
                                             SchedulerLock(id=1)).holder}), flush=True)
            finally:
                db2.close()
            return 0
    finally:
        db.close()

sys.exit(run())
'''


class TestMultiProcessFencing:
    def test_old_leader_is_fenced_out_after_a_takeover(self, tmp_path):
        """Two real OS processes: A becomes leader, B steals the lease while
        A waits. A must then be unable to write."""
        import json
        import subprocess

        db_path = str(tmp_path / "mp.db")
        worker = tmp_path / "worker.py"
        worker.write_text(_WORKER_SRC, encoding="utf-8")

        import app.models  # noqa: F401
        eng = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
        s = Session()
        s.add(Source(name="mp", type="rss", url="https://x.example.com/rss"))
        s.commit()
        s.close()
        eng.dispose()

        env = dict(os.environ)
        env["PYTHONPATH"] = BACKEND
        env["ENV"] = "dev"
        env["SCHEDULER_ENABLED"] = "false"

        holder = subprocess.Popen(
            [sys.executable, str(worker), BACKEND, db_path, "hold", "A"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        # wait for A to become leader
        line = holder.stdout.readline()
        assert line.strip(), f"holder produced no output: {holder.stderr.read()}"
        first = json.loads(line)
        assert first["acquired"] is True, "A must acquire the lease first"
        assert first["version"] is not None

        thief = subprocess.Popen(
            [sys.executable, str(worker), BACKEND, db_path, "steal", "B"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        tout, terr = thief.communicate(timeout=120)
        assert thief.returncode == 0, f"{tout}\n{terr}"
        tline = [ln for ln in tout.strip().splitlines() if ln.startswith("{")]
        assert tline, f"thief produced no output: {tout}\n{terr}"
        stole = json.loads(tline[-1])
        assert stole["stole"] is True, "B must be able to take over the stale lease"
        assert stole["holder"] != "A", "the lease must now belong to B"

        hout, herr = holder.communicate(timeout=120)
        assert holder.returncode == 0, f"{hout}\n{herr}"
        hlines = [ln for ln in hout.strip().splitlines() if ln.startswith("{")]
        assert hlines, f"holder produced no output: {hout}\n{herr}"
        final = json.loads(hlines[-1])
        assert final["fenced"] is True, \
            "the old leader must be fenced out and unable to write"


# --------------------------------------------------------------------------
# 6. The identity-map trap (the subtlest part of the fix)
# --------------------------------------------------------------------------
class TestStaleSessionIsNotTrusted:
    """`db.get(SchedulerLock, 1)` returns the CACHED object.

    SQLAlchemy does not re-query when the instance is already in the
    session's identity map. A long-lived session therefore keeps serving a
    STALE lease row — which silently defeats fencing: a leader that was
    taken over still believes it holds the lease.

    The lease reads must bypass the identity map.
    """

    def test_plain_db_get_returns_the_cached_object(self, db):
        """Documents WHY the fresh read is necessary (this is SQLAlchemy
        behaviour, not a bug in our code)."""
        from app.models import SchedulerLock as SL
        db.add(SL(id=1, holder="A", acquired_at=utcnow(),
                  heartbeat_at=utcnow(), lease_version=1))
        db.commit()
        first = db.get(SL, 1)
        # a second connection changes the row
        other = sessionmaker(bind=db.get_bind(), autoflush=False,
                             expire_on_commit=False)()
        row = other.get(SL, 1)
        row.holder = "B"
        row.lease_version = 2
        other.commit()
        other.close()
        second = db.get(SL, 1)
        assert second is first, "precondition: db.get returns the cached instance"
        assert second.holder == "A", "the cached row is stale"

    def test_fresh_read_sees_the_committed_change(self, db):
        from app.models import SchedulerLock as SL
        from app.services import scheduler as sched
        db.add(SL(id=1, holder="A", acquired_at=utcnow(),
                  heartbeat_at=utcnow(), lease_version=1))
        db.commit()
        other = sessionmaker(bind=db.get_bind(), autoflush=False,
                             expire_on_commit=False)()
        row = other.get(SL, 1)
        row.holder = "B"
        row.lease_version = 2
        other.commit()
        other.close()

        fresh = sched._read_lease_row_fresh(db)
        assert fresh.holder == "B", \
            "the fresh read must observe the committed takeover"
        assert fresh.lease_version == 2

    def test_current_lease_version_does_not_use_a_stale_row(self, db):
        from app.models import SchedulerLock as SL
        from app.services import scheduler as sched
        sched.HOLDER_ID = "A"
        db.add(SL(id=1, holder="A", acquired_at=utcnow(),
                  heartbeat_at=utcnow(), lease_version=1))
        db.commit()
        # prime the identity map with the stale row
        assert sched.current_lease_version(db) == 1

        other = sessionmaker(bind=db.get_bind(), autoflush=False,
                             expire_on_commit=False)()
        row = other.get(SL, 1)
        row.holder = "B"
        row.lease_version = 2
        other.commit()
        other.close()

        assert sched.current_lease_version(db) is None, \
            "A must not believe it still holds the lease after B took over"

    def test_lease_still_valid_does_not_use_a_stale_row(self, db):
        from app.models import SchedulerLock as SL
        from app.services import scheduler as sched
        sched.HOLDER_ID = "A"
        db.add(SL(id=1, holder="A", acquired_at=utcnow(),
                  heartbeat_at=utcnow(), lease_version=1))
        db.commit()
        assert sched.lease_still_valid(db, 1) is True

        other = sessionmaker(bind=db.get_bind(), autoflush=False,
                             expire_on_commit=False)()
        row = other.get(SL, 1)
        row.holder = "B"
        row.lease_version = 2
        other.commit()
        other.close()

        assert sched.lease_still_valid(db, 1) is False, \
            "a takeover must invalidate the fencing token immediately"

    def test_heartbeat_does_not_use_a_stale_row(self, db):
        from app.models import SchedulerLock as SL
        from app.services import scheduler as sched
        sched.HOLDER_ID = "A"
        db.add(SL(id=1, holder="A", acquired_at=utcnow(),
                  heartbeat_at=utcnow(), lease_version=1))
        db.commit()
        other = sessionmaker(bind=db.get_bind(), autoflush=False,
                             expire_on_commit=False)()
        row = other.get(SL, 1)
        row.holder = "B"
        row.lease_version = 2
        other.commit()
        other.close()
        assert sched.heartbeat(db) is False, \
            "a demoted leader must not be able to renew the lease"
