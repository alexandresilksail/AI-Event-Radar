from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import audit, current_user
from ..models import NotificationLog, NotificationRule, SystemSetting
from ..security import create_token  # noqa: F401
from ..config import settings as app_settings

router = APIRouter(tags=["notifications-settings"])


class RuleBody(BaseModel):
    name: str
    channel: str  # webhook|email|telegram|wecom
    config: dict = {}
    filters: dict = {}
    enabled: bool = True


@router.get("/notification-rules")
def list_rules(db: Session = Depends(get_db)):
    rules = db.query(NotificationRule).all()
    logs = (db.query(NotificationLog).order_by(NotificationLog.sent_at.desc())
            .limit(50).all())
    return {"rules": [{"id": r.id, "name": r.name, "channel": r.channel,
                       "config": {k: ("***" if "password" in k or "token" in k or "key" in k else v)
                                  for k, v in (r.config or {}).items()},
                       "filters": r.filters, "enabled": r.enabled} for r in rules],
            "recent_logs": [{"id": l.id, "rule_id": l.rule_id, "event_id": l.event_id,
                             "event_type": l.event_type, "status": l.status,
                             "error": l.error, "sent_at": l.sent_at} for l in logs]}


@router.post("/notification-rules", status_code=201)
def create_rule(body: RuleBody, request: Request,
                user: dict = Depends(current_user), db: Session = Depends(get_db)):
    if body.channel not in ("webhook", "email", "telegram", "wecom"):
        raise HTTPException(400, "channel 必须是 webhook|email|telegram|wecom")
    r = NotificationRule(**body.model_dump())
    db.add(r)
    db.commit()
    audit(request, user, "create_notification_rule", f"rule #{r.id}", db)
    return {"id": r.id}


@router.put("/notification-rules/{rule_id}")
def update_rule(rule_id: int, body: RuleBody, request: Request,
                user: dict = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(NotificationRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    for k, v in body.model_dump().items():
        if k == "config" and not v:
            continue  # don't blank out stored credentials
        setattr(r, k, v)
    db.commit()
    audit(request, user, "update_notification_rule", f"rule #{r.id}", db)
    return {"id": r.id}


@router.delete("/notification-rules/{rule_id}")
def delete_rule(rule_id: int, request: Request,
                user: dict = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(NotificationRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    db.delete(r)
    db.commit()
    audit(request, user, "delete_notification_rule", f"rule #{rule_id}", db)
    return {"deleted": rule_id}


@router.post("/notification-rules/{rule_id}/test")
def test_rule(rule_id: int, db: Session = Depends(get_db)):
    """Send a test notification; reports exactly why it failed if credentials missing."""
    from ..services.notifier import SENDERS
    r = db.get(NotificationRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    sender = SENDERS.get(r.channel)
    if not sender:
        return {"ok": False, "error": f"未知通道 {r.channel}"}
    try:
        sender(r.config or {}, {"subject": "AI Event Radar 测试通知",
                                "text": "这是一条 AI Event Radar 测试通知 ✅"})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- system settings (non-sensitive only) ----------
SENSITIVE_KEYS = {"jwt_secret", "smtp_password", "ai_api_key", "search_api_key"}


@router.get("/settings")
def get_settings():
    return {
        "timezone": "UTC",
        "language": "zh",
        "max_concurrent_crawls": app_settings.MAX_CONCURRENT_CRAWLS,
        "crawl_timeout_seconds": app_settings.CRAWL_TIMEOUT_SECONDS,
        "scheduler_enabled": app_settings.SCHEDULER_ENABLED,
        "ai_configured": bool(app_settings.AI_API_KEY),
        "search_provider_configured": bool(app_settings.SEARCH_API_KEY),
        "note": "敏感凭证仅存于服务端环境变量，不会返回给浏览器",
    }


@router.put("/settings/system_setting")
def put_setting(key: str, value: dict, request: Request,
                user: dict = Depends(current_user), db: Session = Depends(get_db)):
    if key.lower() in SENSITIVE_KEYS:
        raise HTTPException(400, "敏感凭证不允许通过该接口写入数据库")
    row = db.get(SystemSetting, key)
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))
    db.commit()
    audit(request, user, "put_setting", key, db)
    return {"key": key}
