from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import audit, current_user
from ..models import User
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


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
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
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
