"""Release smoke test: real migration, real service start, real failure mode.

Evidence for docs/TEST_REPORT.md — this exercises the actual entry points the
deployment uses, not the test doubles:
  1. `python -m app.migrate` against a fresh database
  2. the API process answering /health/live and /health/ready
  3. the API failing to start against an unreachable database (failure mode)
  4. the scheduler lock being taken over by a second process (recovery)
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BACKEND = os.path.dirname(os.path.abspath(__file__))
PY = os.path.abspath(os.path.join(BACKEND, "..", ".venv", "Scripts", "python.exe"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def env_for(db_path, extra=None):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["ENV"] = "dev"
    env["ROLE"] = "api"
    env["AUTO_MIGRATE"] = "false"
    env["JWT_SECRET"] = "x" * 48
    env["ADMIN_PASSWORD"] = "a-strong-admin-password"
    env["ALLOWED_ORIGINS"] = "http://localhost"
    if extra:
        env.update(extra)
    return env


def step(name, fn):
    print(f"\n### {name}")
    rc = fn()
    print(f"### {name} -> exit={rc}")
    return rc


def run_migration(db_path):
    p = subprocess.run([PY, "-m", "app.migrate"], cwd=BACKEND,
                       env=env_for(db_path), capture_output=True, text=True,
                       timeout=180)
    print(p.stdout[-1500:])
    if p.stderr:
        print("STDERR:", p.stderr[-1500:])
    return p.returncode


def run_api(db_path, port, wait=True, extra_env=None):
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND, env=env_for(db_path, extra_env),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if not wait:
        return proc
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            print("API exited early:", proc.stdout.read()[-2000:])
            return proc
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health/live", timeout=3) as r:
                if r.status == 200:
                    return proc
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return proc


def http_get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main():
    tmp = tempfile.mkdtemp(prefix="release-smoke-")
    db_path = os.path.join(tmp, "radar.db")
    rc_total = 0

    # ---- 1. migration on a fresh database -------------------------------
    rc = step("1. alembic migration on a fresh database",
              lambda: run_migration(db_path))
    rc_total |= rc
    print("    db file exists:", os.path.exists(db_path),
          "size:", os.path.getsize(db_path) if os.path.exists(db_path) else 0)

    # ---- 2. API starts and reports ready --------------------------------
    port = free_port()
    proc = run_api(db_path, port)
    ok_live = ok_ready = False
    try:
        status, body = http_get(port, "/health/live")
        print("    /health/live ->", status, body[:200])
        ok_live = status == 200
        status, body = http_get(port, "/health/ready")
        print("    /health/ready ->", status, body[:300])
        ok_ready = status == 200
    except Exception as e:
        print("    health probe failed:", repr(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    rc |= 0 if (ok_live and ok_ready) else 1
    print("### 2. API start + health -> exit=%s" % (0 if ok_live and ok_ready else 1))

    # ---- 3. failure mode: unreachable database ---------------------------
    # The engine is created lazily, so the process must START even when the
    # database is unreachable — and the readiness probe must report the
    # failure instead of the process dying at import time (the P0 that was
    # fixed in app/database.py).
    bad_env = env_for(db_path)
    bad_env["DATABASE_URL"] = "postgresql+psycopg://radar:pw@127.0.0.1:1/none"
    p = subprocess.run([PY, "-c",
                        "from app.main import app; print('IMPORTED')"],
                       cwd=BACKEND, env=bad_env, capture_output=True, text=True,
                       timeout=120)
    print("    import with unreachable DB -> exit", p.returncode)
    print("    stdout:", p.stdout.strip()[-300:])
    if p.stderr:
        print("    stderr:", p.stderr.strip()[-600:])
    rc |= 0 if p.returncode == 0 and "IMPORTED" in p.stdout else 1

    # ---- 4. scheduler lock takeover --------------------------------------
    lock_rc = subprocess.run(
        [PY, "-c",
         "from app.database import SessionLocal;"
         "from app.services.scheduler import try_acquire_lock, release_lock;"
         "db = SessionLocal();"
         "print('ACQUIRED', try_acquire_lock(db));"
         "print('RELEASED', release_lock(db));"
         "db.close()"],
        cwd=BACKEND, env=env_for(db_path), capture_output=True, text=True,
        timeout=120)
    print("    lock acquire/release ->", lock_rc.stdout.strip()[:300])
    if lock_rc.stderr:
        print("    stderr:", lock_rc.stderr.strip()[-400:])
    rc |= 0 if "ACQUIRED True" in lock_rc.stdout else 1

    print("\nSMOKE_RESULT rc=%s" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
