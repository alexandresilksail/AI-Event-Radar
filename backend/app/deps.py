"""Shared FastAPI dependencies: auth + audit logging."""
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuditLog
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer),
                 db: Session = Depends(get_db)) -> dict:
    if cred is None:
        raise HTTPException(401, "未登录：请在请求头携带 Bearer token")
    payload = decode_token(cred.credentials)
    if not payload:
        raise HTTPException(401, "token 无效或已过期")
    return {"id": int(payload["sub"]), "username": payload.get("name", "")}


def audit(request: Request, user: dict, action: str, detail: str = "",
          db: Session | None = None):
    from .database import SessionLocal
    session = db or SessionLocal()
    try:
        session.add(AuditLog(user_id=user.get("id"), action=action,
                             detail=f"{request.method} {request.url.path} {detail}"[:2000]))
        session.commit()
    finally:
        if db is None:
            session.close()
