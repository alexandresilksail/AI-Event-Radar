"""Seed real, publicly accessible sources into the database.

Run:  python scripts/seed_sources.py
All sources below are legal public feeds/pages (robots.txt respected by default UA).
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base, SessionLocal, engine
from app.models import Source

SOURCES = [
    # ---- RSS：AI 科技媒体（公开 RSS，无需凭证）----
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
         compliance_note="公开 RSS；如失效请改用人工提交 URL"),
    # ---- HTML：页面变更检测示例（公开页面）----
    dict(name="WAIC 世界人工智能大会官网（变更检测）", type="change_detection",
         url="https://www.worldaic.com/", country="中国", language="zh",
         frequency_minutes=360,
         compliance_note="仅检测页面文本变更，频率限制 6 小时"),
]

def main():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    existing = {s.url for s in db.query(Source).all()}
    added = 0
    for cfg in SOURCES:
        if cfg["url"] in existing:
            continue
        db.add(Source(status="active", enabled=True, **cfg))
        added += 1
    db.commit()
    print(f"种子数据源写入完成：新增 {added} 个（已有 {len(existing)} 个）")

if __name__ == "__main__":
    main()
