"""Dedicated, concurrency-safe database migration entry point.

Production flow (docker-compose.prod.yml):
    db healthy -> migrate (this module, one-shot) -> api + worker start.
API/Worker processes never migrate in production (AUTO_MIGRATE resolves to
False there), so replicas cannot race on the same schema change.

The migration itself is guarded by a transactional mutex (`_migration_guard`
table): if another process is already migrating, this process waits until it
finishes and then re-runs `upgrade head`, which is idempotent.

Windows/SQLite concurrency notes (hardened after stress testing):
- A guard-table DDL can hit SQLITE_BUSY / SQLITE_READONLY while the other
  process holds the write lock mid-DDL. CREATE TABLE IF NOT EXISTS is
  idempotent, so bounded retry (deadline = WAIT_TIMEOUT) turns that transient
  failure into a wait instead of a crash or a live-lock.
- Every guard DB access opens a FRESH engine: a connection that opens the DB
  file while another process is initializing it degrades to read-only at open
  time (Windows sharing), and every later write on that pooled connection
  then fails with "attempt to write a readonly database" — permanently.
- A guard row owned by a live pid means "wait"; a row owned by a dead pid
  (crashed migrate) is cleared and taken over (cross-platform pid liveness).
"""
import logging
import os
import time
from datetime import datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from .config import encode_db_url, settings

log = logging.getLogger("radar.migrate")

GUARD_TABLE = "_migration_guard"
WAIT_TIMEOUT = 120  # seconds to wait for a concurrent migration to finish


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", encode_db_url(settings))
    return cfg


def _fresh_engine():
    """Brand-new short-lived engine for one guard operation.

    Never reuse a pooled connection across guard attempts: a connection that
    opened the database while a sibling process was still initializing it is
    stuck in read-only mode for its whole lifetime (see module docstring).
    """
    return create_engine(encode_db_url(settings), poolclass=NullPool)


def _ensure_guard_table() -> None:
    eng = _fresh_engine()
    try:
        with eng.begin() as conn:
            conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS {GUARD_TABLE} ("
                " id INTEGER PRIMARY KEY,"
                " pid TEXT NOT NULL,"
                " started_at TEXT NOT NULL)"))
            # older databases created between v1.0/v1.2 may lack the columns
            if eng.url.get_backend_name() == "sqlite":
                cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({GUARD_TABLE})"))}
            else:
                cols = {r[0] for r in conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{GUARD_TABLE}'"))}
            for col, ddl in (("pid", "TEXT"), ("started_at", "TEXT")):
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE {GUARD_TABLE} ADD COLUMN {col} {ddl}"))
    finally:
        eng.dispose()


def _try_enter_guard() -> bool:
    """Insert the guard row; returns False when another migration holds it.

    On SQLite, racing writers raise OperationalError("database is locked")
    once the busy timeout expires — that also means "someone else is
    migrating", so it is handled exactly like the PK IntegrityError on
    PostgreSQL. Never let a transient lock kill the migrate process.
    """
    eng = _fresh_engine()
    try:
        with eng.begin() as conn:
            conn.execute(text(
                f"INSERT INTO {GUARD_TABLE} (id, pid, started_at) "
                "VALUES (1, :pid, :now)"),
                {"pid": str(os.getpid()),
                 "now": datetime.utcnow().isoformat()})
        return True
    except (IntegrityError, SQLAlchemyError):
        return False
    finally:
        eng.dispose()


def _exit_guard() -> None:
    eng = _fresh_engine()
    try:
        with eng.begin() as conn:
            conn.execute(text(f"DELETE FROM {GUARD_TABLE} WHERE id = 1"))
    finally:
        eng.dispose()


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness check for a process id.

    POSIX: signal 0 existence probe. Windows: OpenProcess + exit-code probe
    (os.kill(pid, 0) does NOT raise ProcessLookupError there — a dead pid
    yields ERROR_INVALID_PARAMETER, which must not be read as "alive").
    """
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION: enough for GetExitCodeProcess and
        # works for processes owned by other sessions (unlike SYNCHRONIZE-only,
        # which makes GetExitCodeProcess fail with ACCESS_DENIED).
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False  # no process object with this pid
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                # A terminated process may still expose a handle object; the
                # exit code is the truth: 259 (STILL_ACTIVE) means running.
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists but owned by another user
    except OSError:
        return True
    return True


def _someone_migrating() -> bool:
    """Is a LIVE migration holding the guard? A row from a dead pid (crashed
    migrate process) is stale and must not block forever.

    On transient DB errors (lock contention on the read): conservatively
    report True — waiting is always safe, guessing "free" is not.
    """
    eng = _fresh_engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(f"SELECT pid FROM {GUARD_TABLE} WHERE id = 1")).first()
    except SQLAlchemyError:
        return True
    finally:
        eng.dispose()
    if row is None:
        return False
    pid = str(row[0]).strip()
    if not pid.isdigit():
        return True  # unknown format: stay conservative, keep waiting
    try:
        alive = _pid_alive(int(pid))
    except Exception:
        return True  # probe failed: waiting is always safe
    if not alive:
        # holder crashed mid-migration: clear the stale row and take over
        log.warning("检测到僵死迁移守卫 (pid=%s 已不存在)，清理后接管", pid)
        try:
            _exit_guard()
        except SQLAlchemyError:
            pass  # cleared on the next iteration instead
        return False
    return True


def upgrade_head(with_guard: bool = True) -> None:
    """Run `alembic upgrade head` with cross-process mutual exclusion."""
    if not with_guard:
        command.upgrade(_alembic_cfg(), "head")
        return
    deadline = time.monotonic() + WAIT_TIMEOUT
    while True:
        try:
            _ensure_guard_table()
            break
        except SQLAlchemyError as e:
            if time.monotonic() > deadline:
                raise SystemExit(f"⛔ 无法创建迁移守卫表（数据库被持续占用?）: {e}")
            log.info("等待数据库写入锁（创建迁移守卫表）: %s", str(e)[:120])
            time.sleep(1)
    while True:
        if _try_enter_guard():
            break
        if not _someone_migrating():
            # stale guard row was just cleared, or a transient lock refused
            # the insert: brief backoff, then retry (bounded!)
            if time.monotonic() > deadline:
                raise SystemExit("⛔ 无法获取迁移守卫（数据库被持续占用?）")
            time.sleep(0.5)
            continue
        if time.monotonic() > deadline:
            raise SystemExit("⛔ 等待其他迁移完成超时，请检查是否有卡住的 migrate 进程")
        log.info("另一进程正在执行迁移，等待中…")
        time.sleep(2)
    try:
        command.upgrade(_alembic_cfg(), "head")
        log.info("数据库迁移完成（alembic upgrade head）")
    finally:
        try:
            _exit_guard()
        except SQLAlchemyError as e:
            # releasing must never turn a successful migration into a failure;
            # a leftover row is handled by the dead-pid takeover next time
            log.warning("释放迁移守卫失败（下次将通过僵死检测接管）: %s", str(e)[:120])


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log.info("开始数据库迁移: role=%s env=%s",
             settings.ROLE, settings.ENV)
    upgrade_head()
    log.info("迁移流程结束，可以启动 api/worker")


if __name__ == "__main__":
    main()
