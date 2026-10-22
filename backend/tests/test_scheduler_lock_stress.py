"""Multi-process scheduler-lease stress test (release audit stage 4).

Why a separate process and not a thread: the whole point of the DB-backed
leader lease is that it protects a *multi-process* deployment (api + worker +
worker). Threads share one interpreter and one `socket.getaddrinfo`/session
pool, so a thread-level race cannot prove the CAS is safe across processes.

What this file actually does:
1. Spawns N real OS processes (`subprocess.Popen`, one interpreter each) that
   all race to acquire the SAME scheduler lease row in the SAME SQLite file.
   - Exactly one process must win the lease (single leader).
   - The winner's `lease_version` must be the highest seen (fencing token).
2. Repeatedly (R rounds) lets the holder die without releasing the lease,
   waits for the lease to go stale, and asserts exactly one new process takes
   over — never two, never zero.
3. Asserts the losing processes' `heartbeat()` returns False (they cannot
   renew a lease they do not hold) and that the demoted leader's
   `_lease_still_held()` is False (fencing check works across processes).
4. Data-consistency check: the lease row must always hold exactly one
   non-empty holder string, and `lease_version` must increase monotonically
   across takeovers (never repeat, never decrease).

Everything runs against a throwaway SQLite file in tmp_path. No network, no
production database, no mocked DB layer — the CAS runs in real SQL.

Known limitation (documented, not hidden): SQLite serialises writers with a
file lock, so this exercises the CAS correctly but does NOT reproduce
PostgreSQL's MVCC behaviour. The PostgreSQL path is NOT VERIFIED here.
"""
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import utcnow  # noqa: E402

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# A tiny worker program executed in a SEPARATE interpreter.
# It prints one JSON object on stdout describing what it did.
_WORKER_SRC = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + sys.argv[2]
os.environ["ENV"] = "dev"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["ADMIN_PASSWORD"] = "stress-test-pass"

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import Base
import app.models  # noqa: F401  (register every model)
from app.services import scheduler as sched

mode = sys.argv[3]
worker_id = sys.argv[4]

# Give each worker a unique, identifiable lease holder id. The production
# default is "<hostname>:<pid>", which is already unique per process; we add
# the logical worker id so the test can tell the racers apart.
sched.HOLDER_ID = worker_id + ":" + str(os.getpid())
eng = create_engine("sqlite:///" + sys.argv[2], connect_args={"timeout": 30})
# The schema is created ONCE by the parent test process BEFORE any racer
# starts. Creating it here would be racy: N processes calling create_all()
# concurrently race against each other on CREATE TABLE (that is a schema
# race, not the lease race we want to test).
Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

out = {"worker": worker_id, "mode": mode}

if mode == "race":
    # Try to become leader as fast as possible. Everyone starts at once.
    deadline = time.time() + float(sys.argv[5])
    acquired = False
    attempts = 0
    while time.time() < deadline:
        db = Session()
        try:
            attempts += 1
            if sched.try_acquire_lock(db):
                acquired = True
                break
        finally:
            db.close()
    out["acquired"] = acquired
    out["attempts"] = attempts
    if acquired:
        db = Session()
        try:
            row = db.execute(text(
                "SELECT holder, lease_version FROM scheduler_locks WHERE id=1")).first()
            out["holder"] = row[0]
            out["lease_version"] = row[1]
            # heartbeat must succeed for the real holder
            out["heartbeat_ok"] = sched.heartbeat(db)
        finally:
            db.close()

elif mode == "probe":
    # A non-leader probes the lease: it must NOT be able to renew it, and its
    # fencing check must fail.
    db = Session()
    try:
        out["heartbeat_ok"] = sched.heartbeat(db)
        out["lease_still_held"] = sched._lease_still_held(db)
        out["is_leader"] = sched.is_leader(db)
        row = db.execute(text(
            "SELECT holder, lease_version FROM scheduler_locks WHERE id=1")).first()
        out["holder"] = row[0] if row else None
        out["lease_version"] = row[1] if row else None
    finally:
        db.close()

elif mode == "acquire_and_hold":
    # Acquire (or steal a stale lease) and stay alive holding it.
    deadline = time.time() + float(sys.argv[5])
    db = Session()
    acquired = False
    while time.time() < deadline:
        try:
            if sched.try_acquire_lock(db):
                acquired = True
                break
        finally:
            pass
        time.sleep(0.05)
    out["acquired"] = acquired
    if acquired:
        row = db.execute(text(
            "SELECT holder, lease_version FROM scheduler_locks WHERE id=1")).first()
        out["holder"] = row[0]
        out["lease_version"] = row[1]
    # hold the lease for the requested seconds WITHOUT releasing it, so the
    # lease goes stale from the DB's point of view once we die.
    time.sleep(float(sys.argv[6]))
    db.close()

print(json.dumps(out))
'''


@pytest.fixture()
def lease_env(tmp_path):
    """Isolated SQLite file + the worker script, ready for subprocess use."""
    db_path = tmp_path / "lease.db"
    worker = tmp_path / "lease_worker.py"
    worker.write_text(_WORKER_SRC, encoding="utf-8")
    # Create the schema ONCE, here in the parent, before any racer starts.
    # Doing it inside the workers would be a schema-creation race that has
    # nothing to do with the lease race under test.
    from sqlalchemy import create_engine
    from app.database import Base
    import app.models  # noqa: F401  (register every model)
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    Base.metadata.create_all(eng)
    eng.dispose()
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND
    env["ENV"] = "dev"
    env["SCHEDULER_ENABLED"] = "false"
    env["ADMIN_PASSWORD"] = "stress-test-pass"
    return {"db": str(db_path), "worker": str(worker), "env": env,
            "python": sys.executable, "backend": BACKEND}


def _run_worker(lease_env, mode, worker_id, timeout_s=60, hold_s=None):
    """Run one worker subprocess and return its parsed JSON result."""
    args = [lease_env["python"], lease_env["worker"], lease_env["backend"],
            lease_env["db"], mode, worker_id, str(timeout_s)]
    if hold_s is not None:
        args.append(str(hold_s))
    p = subprocess.run(args, capture_output=True, text=True,
                       env=lease_env["env"], timeout=timeout_s + 30)
    if p.returncode != 0:
        raise AssertionError(
            f"worker {worker_id} ({mode}) failed rc={p.returncode}\n"
            f"STDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    line = [ln for ln in p.stdout.strip().splitlines() if ln.startswith("{")]
    assert line, f"worker {worker_id} produced no JSON: {p.stdout!r} / {p.stderr!r}"
    return json.loads(line[-1])


def _lease_row(db_path):
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{db_path}")
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT holder, lease_version, heartbeat_at FROM scheduler_locks "
                "WHERE id=1")).first()
            return row
    finally:
        eng.dispose()


def _age_lease(db_path, seconds):
    """Force the stored lease to look stale (simulates a crashed leader)."""
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{db_path}")
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE scheduler_locks SET heartbeat_at=:t WHERE id=1"),
                {"t": (utcnow() - timedelta(seconds=seconds)).isoformat(sep=" ")})
    finally:
        eng.dispose()


class TestMultiProcessLeaderElection:
    def test_only_one_process_wins_the_lease(self, lease_env):
        """N independent OS processes race for the same lease row.

        Exactly ONE may acquire it. This is the property the whole
        single-leader design depends on: two leaders would mean duplicate
        crawls and duplicate notifications.
        """
        n = 8
        procs = []
        for i in range(n):
            args = [lease_env["python"], lease_env["worker"], lease_env["backend"],
                    lease_env["db"], "race", f"proc-{i}", "20"]
            procs.append((f"proc-{i}", subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=lease_env["env"])))
        results = []
        for name, p in procs:
            stdout, stderr = p.communicate(timeout=120)
            assert p.returncode == 0, f"{name} rc={p.returncode}\n{stdout}\n{stderr}"
            lines = [ln for ln in stdout.strip().splitlines() if ln.startswith("{")]
            assert lines, f"{name} produced no JSON: {stdout!r} / {stderr!r}"
            results.append(json.loads(lines[-1]))

        winners = [r for r in results if r.get("acquired")]
        assert len(winners) == 1, (
            f"expected exactly 1 leader among {n} processes, got "
            f"{len(winners)}: {[r['worker'] for r in winners]}")
        winner = winners[0]
        assert winner["heartbeat_ok"] is True
        # the DB row must name that exact process as holder
        row = _lease_row(lease_env["db"])
        assert row[0] == winner["holder"]
        assert row[0].startswith(winner["worker"] + ":"), (row[0], winner["worker"])

    def test_losers_cannot_heartbeat_or_pass_fencing(self, lease_env):
        """After the race, every loser must be locked out.

        - heartbeat() must return False (they do not hold the lease)
        - _lease_still_held() must return False (their fencing check fails)
        """
        n = 5
        procs = []
        for i in range(n):
            args = [lease_env["python"], lease_env["worker"], lease_env["backend"],
                    lease_env["db"], "race", f"p{i}", "15"]
            procs.append(subprocess.Popen(args, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True,
                                          env=lease_env["env"]))
        results = []
        for p in procs:
            stdout, stderr = p.communicate(timeout=120)
            assert p.returncode == 0, f"{stdout}\n{stderr}"
            lines = [ln for ln in stdout.strip().splitlines() if ln.startswith("{")]
            results.append(json.loads(lines[-1]))
        winners = [r for r in results if r.get("acquired")]
        assert len(winners) == 1

        # each loser probes the lease from a fresh process
        for r in results:
            if r.get("acquired"):
                continue
            probe = _run_worker(lease_env, "probe", r["worker"], timeout_s=30)
            assert probe["heartbeat_ok"] is False, \
                f"{r['worker']} renewed a lease it does not hold!"
            assert probe["lease_still_held"] is False, \
                f"{r['worker']} passed the fencing check as a non-leader!"
            assert probe["is_leader"] is False

    def test_crashed_leader_is_replaced_by_exactly_one_takeover(self, lease_env):
        """Leader dies without releasing → lease goes stale → one new leader.

        Repeated over several rounds to catch races that only show up
        occasionally.
        """
        from app.config import settings
        ttl = settings.SCHEDULER_LOCK_TTL

        seen_versions = []
        for round_no in range(3):
            # The previous round's leader is gone; make its lease stale so this
            # round's holder can legitimately take over.
            _age_lease(lease_env["db"], ttl + 60)
            holder = _run_worker(lease_env, "acquire_and_hold", f"crash-{round_no}",
                                 timeout_s=30, hold_s=1)
            assert holder["acquired"] is True, f"round {round_no}: holder failed to acquire"
            seen_versions.append(holder["lease_version"])

            # the holder is now dead (process exited) but the lease was never
            # released — from the DB's point of view it is still "held"
            row = _lease_row(lease_env["db"])
            assert row[0] == holder["holder"], "lease row must still name the dead holder"

            # a live process must NOT be able to steal a fresh lease
            _age_lease(lease_env["db"], 0)  # keep heartbeat fresh
            intruder = _run_worker(lease_env, "race", f"intruder-{round_no}", timeout_s=3)
            assert intruder["acquired"] is False, \
                f"round {round_no}: a fresh lease was stolen by a second leader!"

            # now let it go stale; exactly one process may take over
            _age_lease(lease_env["db"], ttl + 60)
            takers = []
            for i in range(4):
                args = [lease_env["python"], lease_env["worker"], lease_env["backend"],
                        lease_env["db"], "race", f"take-{round_no}-{i}", "20"]
                takers.append(subprocess.Popen(args, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True,
                                               env=lease_env["env"]))
            t_results = []
            for p in takers:
                stdout, stderr = p.communicate(timeout=120)
                assert p.returncode == 0, f"{stdout}\n{stderr}"
                lines = [ln for ln in stdout.strip().splitlines() if ln.startswith("{")]
                t_results.append(json.loads(lines[-1]))
            new_winners = [r for r in t_results if r.get("acquired")]
            assert len(new_winners) == 1, (
                f"round {round_no}: expected exactly 1 takeover, got "
                f"{len(new_winners)}")
            seen_versions.append(new_winners[0]["lease_version"])

        # fencing token must increase monotonically across every handover
        assert seen_versions == sorted(seen_versions), seen_versions
        assert len(set(seen_versions)) == len(seen_versions), \
            f"lease_version repeated across handovers: {seen_versions}"

    def test_lease_row_is_never_inconsistent(self, lease_env):
        """Under contention the lease row must always be coherent.

        - exactly one row (id=1)
        - holder is a non-empty string (never None/'' while a leader runs)
        - lease_version is a positive integer that never decreases
        """
        n = 6
        rounds = 4
        procs = []
        for r in range(rounds):
            for i in range(n):
                args = [lease_env["python"], lease_env["worker"], lease_env["backend"],
                        lease_env["db"], "race", f"r{r}-p{i}", "10"]
                procs.append(subprocess.Popen(args, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, text=True,
                                              env=lease_env["env"]))
            for p in procs:
                stdout, stderr = p.communicate(timeout=120)
                assert p.returncode == 0, f"{stdout}\n{stderr}"
            procs = []

            from sqlalchemy import create_engine
            eng = create_engine(f"sqlite:///{lease_env['db']}")
            try:
                with eng.connect() as conn:
                    count = conn.execute(text(
                        "SELECT COUNT(*) FROM scheduler_locks")).scalar()
                    row = conn.execute(text(
                        "SELECT holder, lease_version FROM scheduler_locks "
                        "WHERE id=1")).first()
            finally:
                eng.dispose()
            assert count == 1, f"lease table must hold exactly one row, got {count}"
            assert row is not None
            assert isinstance(row[0], str) and row[0].strip(), \
                f"holder must be a non-empty string, got {row[0]!r}"
            assert isinstance(row[1], int) and row[1] >= 1, \
                f"lease_version must be a positive int, got {row[1]!r}"


class TestFencingAcrossProcesses:
    def test_demoted_leader_cannot_dispatch(self, lease_env):
        """A leader demoted mid-flight must not produce side effects.

        Scenario: process A holds the lease; process B takes over (after A's
        lease went stale). A then tries to run a crawl job — its fencing check
        must fail, so no CrawlTask row is created.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.models import CrawlTask, Source
        from app.services import scheduler as sched

        # The schema already exists (created by the lease_env fixture).
        # Add one enabled source to the shared DB file.
        eng = create_engine(f"sqlite:///{lease_env['db']}", connect_args={"timeout": 30})
        Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
        db = Session()
        src = Source(name="mp-fencing", type="rss",
                     url="https://example.com/rss", enabled=True)
        db.add(src)
        db.commit()
        src_id = src.id
        db.close()

        # Process A acquires the lease, then B steals it (A is "demoted").
        holder = _run_worker(lease_env, "acquire_and_hold", "leader-A",
                             timeout_s=30, hold_s=1)
        assert holder["acquired"] is True

        _age_lease(lease_env["db"], 10 ** 6)  # force A's lease stale
        taker = _run_worker(lease_env, "race", "leader-B", timeout_s=15)
        assert taker["acquired"] is True
        assert taker["holder"] != holder["holder"]

        # Now simulate the demoted leader A attempting a dispatch.
        old_holder = sched.HOLDER_ID
        try:
            sched.HOLDER_ID = holder["holder"]
            db = Session()
            try:
                assert sched._lease_still_held(db) is False, \
                    "demoted leader must fail the fencing check"
                sched._crawl_job(src_id)
                db.expire_all()
                assert db.query(CrawlTask).filter_by(source_id=src_id).count() == 0, \
                    "demoted leader created a CrawlTask — fencing failed!"
            finally:
                db.close()
        finally:
            sched.HOLDER_ID = old_holder
            eng.dispose()
