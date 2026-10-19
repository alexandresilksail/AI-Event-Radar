"""Idempotent source seed for container/production environments.

Usage:  python -m app.seed
Re-running never duplicates sources (unique by URL and by name).
"""
import logging
import sys

from .database import Base, SessionLocal, engine
from .models import Source

log = logging.getLogger("seed")

SOURCES = [
    # ---- RSS：公开订阅源，无需凭证 ----
    dict(name="MIT News — Artificial intelligence", type="rss",
         url="https://news.mit.edu/rss/topic/artificial-intelligence2",
         country="美国", language="en", frequency_minutes=60,
         compliance_note="MIT News 官方公开 RSS"),
    dict(name="MIT Technology Review", type="rss",
         url="https://www.technologyreview.com/feed/",
         country="美国", language="en", frequency_minutes=60,
         compliance_note="公开 RSS"),
    dict(name="VentureBeat — AI", type="rss",
         url="https://venturebeat.com/category/ai/feed/",
         country="美国", language="en", frequency_minutes=60,
         compliance_note="公开 RSS"),
    dict(name="机器之心", type="rss",
         url="https://www.jiqizhixin.com/rss",
         country="中国", language="zh", frequency_minutes=60,
         compliance_note="公开 RSS；如源失效请改用人工提交/官方 API"),
    # ---- 页面变更检测示例（公开页面）----
    dict(name="WAIC 世界人工智能大会官网（变更检测）", type="change_detection",
         url="https://www.worldaic.com/", country="中国", language="zh",
         frequency_minutes=360,
         compliance_note="仅检测页面文本变更，频率限制 6 小时"),
]


def seed() -> int:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        existing_urls = {s.url for s in db.query(Source).all()}
        existing_names = {s.name for s in db.query(Source).all()}
        added = 0
        for cfg in SOURCES:
            if cfg["url"] in existing_urls or cfg["name"] in existing_names:
                continue
            db.add(Source(status="active", enabled=True, **cfg))
            existing_urls.add(cfg["url"])
            existing_names.add(cfg["name"])
            added += 1
        db.commit()
        log.info("种子数据源：新增 %d 个（已有 %d 个，跳过重复）",
                 added, len(existing_urls))
        return added
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(0)
