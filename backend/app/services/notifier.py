"""Notification module: channel adapters + rule filtering + send dedup.

Without credentials, sends are recorded as 'skipped_no_credentials' and the
platform keeps collecting/storing/displaying normally — notifications are optional.
"""
import json
import logging
import smtplib
from email.mime.text import MIMEText

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Event, NotificationLog, NotificationRule, utcnow
from ..security import validate_public_url

logger = logging.getLogger("radar.notify")


def event_matches_filters(ev: Event, filters: dict) -> bool:
    if not filters:
        return True
    if t := filters.get("event_type"):
        if t not in (ev.type or ""):
            return False
    if cats := filters.get("types"):
        if ev.type not in cats:
            return False
    if country := filters.get("country"):
        if country != (ev.country or ""):
            return False
    if city := filters.get("city"):
        if city not in (ev.city or ""):
            return False
    if kw := filters.get("keyword"):
        text = f"{ev.name} {ev.topics} {ev.summary}"
        if kw not in text:
            return False
    if filters.get("free_only") and ev.is_free is not True:
        return False
    if filters.get("official_only") and ev.confidence_level not in ("A", "B"):
        return False
    if days := filters.get("within_days"):
        if not ev.start_time:
            return False
        from datetime import datetime
        if ev.start_time > utcnow() + __import__("datetime").timedelta(days=int(days)):
            return False
    return True


def _send_webhook(config: dict, payload: dict) -> None:
    url = config.get("url", "")
    # A webhook URL is attacker-controllable (any authenticated user can create
    # a rule), so it must go through the same SSRF policy as crawl targets.
    # Operator-configured internal endpoints are exempt via
    # TRUSTED_INTERNAL_HOSTS / SMTP_HOST / AI_BASE_URL.
    validate_public_url(url)
    r = httpx.post(url, json=payload, timeout=15)
    r.raise_for_status()


def _send_email(config: dict, payload: dict) -> None:
    host = config.get("smtp_host") or settings.SMTP_HOST
    user = config.get("smtp_user") or settings.SMTP_USER
    pwd = config.get("smtp_password") or settings.SMTP_PASSWORD
    to = config.get("to") or []
    if not (host and user and pwd and to):
        raise ValueError("SMTP 凭证未配置完整 (smtp_host/smtp_user/smtp_password/to)")
    msg = MIMEText(payload.get("text", ""), "plain", "utf-8")
    msg["Subject"] = payload.get("subject", "AI Event Radar 通知")
    msg["From"], msg["To"] = settings.SMTP_FROM or user, ", ".join(to)
    port = config.get("smtp_port", settings.SMTP_PORT)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.starttls()
    with server:
        server.login(user, pwd)
        server.sendmail(msg["From"], to, msg.as_string())


def _send_telegram(config: dict, payload: dict) -> None:
    token, chat_id = config.get("bot_token"), config.get("chat_id")
    if not (token and chat_id):
        raise ValueError("Telegram bot_token/chat_id 未配置")
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat_id, "text": payload["text"]}, timeout=15)
    r.raise_for_status()


def _send_wecom(config: dict, payload: dict) -> None:
    key = config.get("wecom_webhook_key") or config.get("url", "")
    if not key:
        raise ValueError("企业微信机器人 webhook key 未配置")
    if key.startswith("http"):
        # a full URL was supplied: validate it like any other webhook target
        url = key
        validate_public_url(url)
    else:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    r = httpx.post(url, json={"msgtype": "text", "text": {"content": payload["text"]}}, timeout=15)
    r.raise_for_status()


SENDERS = {"webhook": _send_webhook, "email": _send_email,
           "telegram": _send_telegram, "wecom": _send_wecom}


def format_event_text(ev: Event, event_type: str = "new_event") -> str:
    prefix = {"new_event": "🆕 新活动", "changed": "🔄 活动变更", "cancelled": "❌ 活动取消"}.get(event_type, "通知")
    lines = [f"【{prefix}】{ev.name}",
             f"类型: {ev.type} | 可信度: {ev.confidence_level}",
             f"时间: {ev.start_time or '未知'} ~ {ev.end_time or '未知'}",
             f"地点: {ev.country} {ev.city} {'(线上)' if ev.online else ''}",
             f"主办方: {ev.organizer}",
             f"详情: {ev.detail_url or '未知'}"]
    return "\n".join(lines)


def dispatch_new_event(db: Session, ev: Event, event_type: str = "new_event") -> int:
    """Evaluate all enabled rules and send. Returns number of successful sends.
    dedup_key guarantees one notification per (rule, event, event_type)."""
    sent = 0
    rules = db.query(NotificationRule).filter_by(enabled=True).all()
    for rule in rules:
        if rule.channel not in SENDERS:
            continue
        if not event_matches_filters(ev, rule.filters or {}):
            continue
        dedup_key = f"{rule.id}:{event_type}:{ev.id}"
        exists = db.query(NotificationLog).filter_by(rule_id=rule.id, dedup_key=dedup_key).first()
        if exists:
            continue
        text = format_event_text(ev, event_type)
        payload = {"subject": text.splitlines()[0], "text": text,
                   "event": {"id": ev.id, "name": ev.name, "start_time": str(ev.start_time),
                             "city": ev.city, "detail_url": ev.detail_url}}
        try:
            SENDERS[rule.channel](rule.config or {}, payload)
            db.add(NotificationLog(rule_id=rule.id, event_id=ev.id, event_type=event_type,
                                   status="sent", dedup_key=dedup_key))
            sent += 1
        except ValueError as e:
            db.add(NotificationLog(rule_id=rule.id, event_id=ev.id, event_type=event_type,
                                   status="skipped_no_credentials", dedup_key=dedup_key,
                                   error=str(e)))
        except Exception as e:
            db.add(NotificationLog(rule_id=rule.id, event_id=ev.id, event_type=event_type,
                                   status="failed", dedup_key=dedup_key, error=str(e)[:1000]))
        db.commit()
    return sent
