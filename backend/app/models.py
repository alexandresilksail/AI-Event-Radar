"""SQLAlchemy ORM models — all 13 core tables."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32))  # rss | html | api | sitemap | search | change_detection
    url: Mapped[str] = mapped_column(String(1024))
    country: Mapped[str] = mapped_column(String(64), default="未知")
    language: Mapped[str] = mapped_column(String(16), default="zh")
    method: Mapped[dict] = mapped_column(JSON, default=dict)  # type-specific config (selectors etc.)
    frequency_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="active")  # candidate|active|paused|failing
    topics: Mapped[str] = mapped_column(String(500), default="")        # 监控主题/关键词（逗号分隔）
    timezone: Mapped[str] = mapped_column(String(64), default="")       # 可选时区备注
    note: Mapped[str] = mapped_column(Text, default="")                 # 备注
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # 最近一次采集（无论成败）
    last_crawl_status: Mapped[str] = mapped_column(String(16), default="")             # success|partial|failed|""
    last_error: Mapped[str] = mapped_column(Text, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_item_count: Mapped[int] = mapped_column(Integer, default=0)
    total_events_found: Mapped[int] = mapped_column(Integer, default=0)
    compliance_note: Mapped[str] = mapped_column(Text, default="")
    discovery_path: Mapped[str] = mapped_column(Text, default="")  # how this source was discovered
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # soft delete
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    logs = relationship("CrawlLog", back_populates="source", cascade="all,delete-orphan")


class CrawlTask(Base):
    __tablename__ = "crawl_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|success|partial|failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|scheduled|retry|seed
    retry_of: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 原任务 ID（重试关联）
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class CrawlLog(Base):
    __tablename__ = "crawl_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # success|failed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")

    source = relationship("Source", back_populates="logs")


class RawDocument(Base):
    __tablename__ = "raw_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("source_id", "content_hash", name="uq_doc_hash"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    type: Mapped[str] = mapped_column(String(64), default="未知")
    organizer: Mapped[str] = mapped_column(String(300), default="待核实")
    co_organizer: Mapped[str] = mapped_column(String(300), default="未知")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    registration_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    city: Mapped[str] = mapped_column(String(100), default="未知", index=True)
    address: Mapped[str] = mapped_column(String(500), default="未知")
    online: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None=未知
    country: Mapped[str] = mapped_column(String(64), default="未知", index=True)
    language: Mapped[str] = mapped_column(String(32), default="未知")
    registration_url: Mapped[str] = mapped_column(String(2048), default="")
    detail_url: Mapped[str] = mapped_column(String(2048), default="")
    audience: Mapped[str] = mapped_column(Text, default="未知")
    topics: Mapped[str] = mapped_column(Text, default="未知")
    requirements: Mapped[str] = mapped_column(Text, default="未知")
    price: Mapped[str] = mapped_column(String(200), default="未知")
    is_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    need_registration: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence_level: Mapped[str] = mapped_column(String(16), default="C", index=True)  # A|B|C|待核实
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="active")  # active|finished|cancelled|postponed|unknown
    is_finished: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_method: Mapped[str] = mapped_column(String(32), default="rule")  # rule|ai
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    sources = relationship("EventSource", back_populates="event", cascade="all,delete-orphan")
    history = relationship("EventHistory", back_populates="event", cascade="all,delete-orphan")
    tags = relationship("EventTag", back_populates="event", cascade="all,delete-orphan")


Index("ix_events_name_time_city", Event.name, Event.start_time, Event.city)


class EventSource(Base):
    __tablename__ = "event_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("event_id", "url", name="uq_event_url"),)

    event = relationship("Event", back_populates="sources")


class EventHistory(Base):
    __tablename__ = "event_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    changed_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event = relationship("Event", back_populates="history")


class EventTag(Base):
    __tablename__ = "event_tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    tag: Mapped[str] = mapped_column(String(100), index=True)

    event = relationship("Event", back_populates="tags")


class NotificationRule(Base):
    __tablename__ = "notification_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(32))  # webhook|email|telegram|wecom
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("notification_rules.id"))
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), default="new_event")  # new_event|changed|cancelled
    status: Mapped[str] = mapped_column(String(16))  # sent|failed|skipped_no_credentials
    dedup_key: Mapped[str] = mapped_column(String(200), index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("rule_id", "dedup_key", name="uq_notif_dedup"),)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SchedulerLock(Base):
    """Single active scheduler instance across API/Worker processes (DB-backed leader election).

    lease_version is a fencing token: it increases on every acquisition, so an
    old leader that lost the lease (or was taken over) can be detected by any
    job before it performs side effects."""
    __tablename__ = "scheduler_locks"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    holder: Mapped[str] = mapped_column(String(128), default="")
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)


class SourceState(Base):
    """Persisted per-source state that must survive restarts (e.g. change-detection content hash)."""
    __tablename__ = "source_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
