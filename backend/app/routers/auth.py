import threading
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import audit, current_user
from ..models import User
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------- login brute-force protection ----------
# Failed logins are counted per (client IP, username). After
# LOGIN_MAX_ATTEMPTS failures inside LOGIN_WINDOW_SECONDS the pair is locked
# out for LOGIN_LOCKOUT_SECONDS. A successful login clears the counter.
#
# The window is kept in process memory, which is correct for the single API
# container this app ships with. Behind multiple API replicas the lockout is
# per-replica; the failed attempts are still written to the audit log, so the
# operator can see them centrally.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCKOUT_SECONDS = 300

_login_attempts: dict[str, list[float]] = defaultdict(list)
_login_locked_until: dict[str, float] = {}
_login_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """Best-effort client IP.

    X-Forwarded-For is only honoured when the app runs behind a trusted proxy.
    In the shipped topology nginx is the only ingress and it overwrites
    X-Forwarded-For, so the left-most entry is the real client. If the header
    is absent we fall back to the socket peer.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _attempt_key(request: Request, username: str) -> str:
    return f"{_client_ip(request)}|{(username or '').lower()}"


def _is_locked(key: str) -> int:
    """Return the remaining lockout seconds (0 when not locked).

    NOTE: this must NOT clear the failure counter — the counter is what
    decides when the lockout starts. Only an expired lockout is cleared
    here; the attempt list is pruned by `_register_failure` on the next
    failure (and by `_clear_failures` on a successful login).
    """
    with _login_lock:
        until = _login_locked_until.get(key, 0.0)
        now = time.monotonic()
        if until <= now:
            _login_locked_until.pop(key, None)
            return 0
        return int(until - now)


def _register_failure(key: str) -> None:
    with _login_lock:
        now = time.monotonic()
        attempts = [t for t in _login_attempts.get(key, [])
                    if now - t < LOGIN_WINDOW_SECONDS]
        attempts.append(now)
        _login_attempts[key] = attempts
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            _login_locked_until[key] = now + LOGIN_LOCKOUT_SECONDS


def _clear_failures(key: str) -> None:
    with _login_lock:
        _login_attempts.pop(key, None)
        _login_locked_until.pop(key, None)


class LoginBody(BaseModel):
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def strong(cls, v):
        if len(v) < 12:
            raise ValueError("新密码长度至少 12 位")
        return v


class ResetPasswordBody(BaseModel):
    reset_token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def strong(cls, v):
        if len(v) < 12:
            raise ValueError("新密码长度至少 12 位")
        return v


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    """Password login with brute-force protection.

    The error message is identical for an unknown user and a wrong password,
    so the endpoint does not reveal which usernames exist.
    """
    key = _attempt_key(request, body.username)
    remaining = _is_locked(key)
    if remaining:
        raise HTTPException(
            429, f"登录尝试过于频繁，请在 {remaining} 秒后重试")
    user = db.query(User).filter_by(username=body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        _register_failure(key)
        audit(request, {"id": None}, "login_failed",
              f"username={body.username[:64]}", db)
        raise HTTPException(401, "用户名或密码错误")
    _clear_failures(key)
    return {"token": create_token(user.id, user.username), "username": user.username}


@router.post("/change-password")
def change_password(body: ChangePasswordBody, request: Request,
                    user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """Authenticated self-service password change (idempotent, never auto-reset on boot)."""
    db_user = db.get(User, user["id"])
    if not db_user or not verify_password(body.old_password, db_user.password_hash):
        raise HTTPException(403, "原密码不正确")
    db_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"changed": True, "username": db_user.username,
            "note": "后续 .env 中的 ADMIN_PASSWORD 仅在账号不存在时用于初始化，不会再覆盖此密码"}


@router.post("/reset-password")
def reset_password(body: ResetPasswordBody, request: Request, db: Session = Depends(get_db)):
    """Secure reset via ADMIN_RESET_TOKEN (set to empty to disable this entry point)."""
    if not settings.ADMIN_RESET_TOKEN:
        raise HTTPException(403, "重置入口未启用（ADMIN_RESET_TOKEN 未配置）")
    import secrets as _secrets
    if not _secrets.compare_digest(body.reset_token, settings.ADMIN_RESET_TOKEN):
        audit(request, {"id": None}, "reset_password_failed", "token 不匹配", db)
        raise HTTPException(403, "重置令牌不正确")
    db_user = db.query(User).filter_by(username=settings.ADMIN_USERNAME).first()
    if not db_user:
        db_user = User(username=settings.ADMIN_USERNAME)
        db.add(db_user)
    db_user.password_hash = hash_password(body.new_password)
    db.commit()
    audit(request, {"id": db_user.id}, "reset_password", "管理员密码已重置", db)
    return {"reset": True, "username": db_user.username}


def ensure_default_admin(db: Session) -> None:
    """Idempotent: creates the account on first boot only; NEVER resets an existing password."""
    if not db.query(User).filter_by(username=settings.ADMIN_USERNAME).first():
        db.add(User(username=settings.ADMIN_USERNAME,
                    password_hash=hash_password(settings.ADMIN_PASSWORD)))
        db.commit()
