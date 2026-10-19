"""API integration tests + dedup pipeline tests + notification tests + auth/security tests.

All external HTTP is mocked via monkeypatching httpx.get/post.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from app.models import Event, EventSource, Source
from app.collectors.base import CollectedItem


def _rss_xml(items):
    entries = "".join(f"""
    <item><title>{t}</title><link>{u}</link>
    <description>{d}</description><pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate></item>"""
                      for t, u, d in items)
    return f"""<?xml version="1.0"?><rss version="2.0"><channel>
    <title>test feed</title>{entries}</channel></rss>"""


EVENT_ARTICLE = ("2026 世界人工智能大会（WAIC）将于 2026年11月3日 至 11月5日 在上海世博展览馆举办，"
                 "由上海市人工智能行业协会主办。大会聚焦大模型与具身智能，面向开发者与研究人员，"
                 "门票免费，需提前报名。详情见 https://example.com/waic/register")


@pytest.fixture()
def source(db):
    s = Source(name="测试 RSS 源", type="rss", url="https://feed.example.com/rss",
               country="中国", language="zh", frequency_minutes=60)
    db.add(s)
    db.commit()
    return s


def _mock_rss(monkeypatch, items):
    # 仅测试环境放开 SSRF 域名校验（假域名 feed.example.com 无法真实解析）
    monkeypatch.setattr("app.collectors.base.validate_public_url", lambda url: url)

    def fake_get(url, **kw):
        m = httpx.Response(200, content=_rss_xml(items).encode(), request=httpx.Request("GET", url))
        return m
    monkeypatch.setattr("app.collectors.base.httpx.get", fake_get)


class TestAuthAndSecurity:
    def test_login_success(self, client):
        r = client.post("/auth/login", json={"username": "admin", "password": "test-admin-pass"})
        assert r.status_code == 200 and "token" in r.json()

    def test_login_failure(self, client):
        assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401

    def test_write_requires_auth(self, client):
        assert client.post("/sources", json={"name": "x", "type": "rss",
                                             "url": "https://a.com/rss"}).status_code == 401

    def test_read_is_public(self, client):
        assert client.get("/events").status_code == 200
        assert client.get("/stats/dashboard").status_code == 200

    def test_invalid_token_rejected(self, client):
        r = client.post("/sources", headers={"Authorization": "Bearer bad.token"},
                        json={"name": "x", "type": "rss", "url": "https://a.com"})
        assert r.status_code == 401

    def test_ssrf_blocked_on_trigger(self, client, auth_headers, db):
        s = Source(name="内网源", type="html", url="http://169.254.169.254/meta", frequency_minutes=60)
        db.add(s)
        db.commit()
        r = client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        assert r.status_code == 200 and "SSRF" in r.json()["error"]


class TestSourceCrud:
    def test_create_source(self, client, auth_headers):
        r = client.post("/sources", headers=auth_headers, json={
            "name": "MIT AI News", "type": "rss",
            "url": "https://news.mit.edu/rss/topic/artificial-intelligence2",
            "country": "美国", "language": "en", "frequency_minutes": 15})
        assert r.status_code == 201, r.text

    def test_frequency_validation(self, client, auth_headers):
        r = client.post("/sources", headers=auth_headers, json={
            "name": "too fast", "type": "rss", "url": "https://a.com", "frequency_minutes": 1})
        assert r.status_code == 422

    def test_invalid_type_rejected(self, client, auth_headers):
        r = client.post("/sources", headers=auth_headers, json={
            "name": "x", "type": "nope", "url": "https://a.com"})
        assert r.status_code == 422


class TestCrawlPipeline:
    def test_full_crawl_creates_event(self, client, auth_headers, db, monkeypatch):
        s = Source(name="WAIC 源", type="rss", url="https://feed.example.com/rss",
                   country="中国", frequency_minutes=60)
        db.add(s)
        db.commit()
        _mock_rss(monkeypatch, [("2026 世界人工智能大会启动报名", "https://example.com/waic", EVENT_ARTICLE)])
        r = client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "success", r.text
        assert r.json()["new_events"] == 1
        ev = db.query(Event).first()
        assert ev and "人工智能大会" in ev.name
        assert ev.start_time == datetime(2026, 11, 3)
        assert ev.city == "上海" and ev.country == "中国"
        assert ev.is_free is True
        assert ev.confidence_level in ("A", "B", "C", "待核实")

    def test_repeated_crawl_no_duplicate_events(self, client, auth_headers, db, monkeypatch):
        s = Source(name="重复测试源", type="rss", url="https://feed.example.com/rss2",
                   country="中国", frequency_minutes=60)
        db.add(s)
        db.commit()
        _mock_rss(monkeypatch, [("2026 世界人工智能大会启动报名", "https://example.com/waic", EVENT_ARTICLE)])
        client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        r2 = client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        data = r2.json()
        assert data["new_events"] == 0  # same content-hash → skipped, no new events
        count = db.query(Event).filter(Event.name.like("%人工智能大会%")).count()
        assert count == 1  # never re-created
        # multi-source preserved
        ev = db.query(Event).filter(Event.name.like("%人工智能大会%")).first()
        assert db.query(EventSource).filter_by(event_id=ev.id).count() >= 1

    def test_raw_doc_content_hash_dedup(self, db, monkeypatch, source):
        from app.services.pipeline import crawl_source
        _mock_rss(monkeypatch, [("AI 峰会新闻", "https://example.com/n1", "AI 峰会将于2026年12月1日在北京举行")])
        crawl_source(db, source)
        crawl_source(db, source)
        from app.models import RawDocument
        assert db.query(RawDocument).filter_by(source_id=source.id).count() == 1

    def test_failed_crawl_recorded(self, client, auth_headers, db, monkeypatch):
        s = Source(name="坏源", type="rss", url="https://broken.example.com/rss", frequency_minutes=5)
        db.add(s)
        db.commit()
        monkeypatch.setattr("app.collectors.base.validate_public_url", lambda url: url)
        def fake_get(url, **kw):
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))
        monkeypatch.setattr("app.collectors.base.httpx.get", fake_get)
        r = client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        assert r.json()["status"] == "failed" and "请求失败" in r.json()["error"]
        logs = client.get("/crawl-logs", params={"status": "failed"}).json()
        assert logs["total"] >= 1
        assert any("请求失败" in (l["error"] or "") for l in logs["items"])

    def test_non_ai_content_ignored(self, client, auth_headers, db, monkeypatch):
        s = Source(name="非AI源", type="rss", url="https://feed.example.com/rss3", frequency_minutes=60)
        db.add(s)
        db.commit()
        _mock_rss(monkeypatch, [("猪肉价格走势", "https://example.com/pork", "菜市场猪肉价格上涨 5%")])
        r = client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        assert r.json()["new_events"] == 0


class TestDedupLayers:
    def test_fuzzy_name_match_merges(self, db):
        from app.services.dedup import find_duplicate
        ev = Event(name="2026全球人工智能大会", start_time=datetime(2026, 11, 3),
                   city="上海", country="中国")
        db.add(ev)
        db.flush()
        fields = {"name": "2026全球人工智能大会!", "start_time": datetime(2026, 11, 3),
                  "city": "上海"}
        dup = find_duplicate(db, fields, "https://other-media.com/waic-2026")
        assert dup is not None and dup.id == ev.id

    def test_different_event_not_merged(self, db):
        from app.services.dedup import find_duplicate
        db.add(Event(name="2026全球人工智能大会", start_time=datetime(2026, 11, 3), city="上海"))
        db.flush()
        fields = {"name": "国际机器人展览会 2026", "start_time": datetime(2027, 3, 1),
                  "city": "深圳"}
        assert find_duplicate(db, fields, "https://x.com/robot-expo") is None

    def test_merge_preserves_history_and_sources(self, db):
        from app.services.pipeline import merge_event
        ev = Event(name="AI 大会", city="未知")
        db.add(ev)
        db.flush()
        db.add(EventSource(event_id=ev.id, source_id=1, url="https://a.com/1"))
        db.commit()
        fields = {"city": "北京", "organizer": "XX 协会"}
        _, changed = merge_event(db, ev, fields, "https://b.com/2", "t", 2)
        assert any("city" in c for c in changed)
        assert ev.city == "北京"
        assert ev.last_confirmed_at is not None

    def test_confirmed_data_not_overwritten_by_unknown(self, db):
        from app.services.pipeline import merge_event
        ev = Event(name="AI 大会", city="上海")
        db.add(ev)
        db.commit()
        merge_event(db, ev, {"city": "未知", "organizer": "主办方 X"}, "https://c.com/3", "t", 1)
        assert ev.city == "上海"  # unknown never overwrites confirmed value


class TestEventStatus:
    def test_finished_event_auto_detected(self, client, db):
        old = Event(name="过期 AI 沙龙", start_time=datetime.utcnow() - timedelta(days=30),
                    end_time=datetime.utcnow() - timedelta(days=29))
        db.add(old)
        db.commit()
        from app.services.pipeline import refresh_event_status
        refresh_event_status(db, old)
        db.commit()
        assert old.is_finished and old.status == "finished"
        r = client.get("/events", params={"finished": "true"}).json()
        assert any(e["id"] == old.id for e in r["items"])


class TestEventAPI:
    def test_filters(self, client, db):
        db.add(Event(name="免费 AI 工作坊", is_free=True, country="中国", city="深圳",
                     confidence_level="C", type="培训/公开课"))
        db.commit()
        r = client.get("/events", params={"free": "true", "city": "深圳"}).json()
        assert any("工作坊" in e["name"] for e in r["items"])
        r = client.get("/events", params={"keyword": "工作坊"}).json()
        assert r["total"] >= 1

    def test_detail_has_sources_and_history(self, client, db):
        ev = Event(name="详情测试大会", city="杭州")
        db.add(ev)
        db.flush()
        db.add(EventSource(event_id=ev.id, source_id=1, url="https://news.example.com/detail"))
        db.commit()
        r = client.get(f"/events/{ev.id}").json()
        assert r["name"] == "详情测试大会"
        assert len(r["sources"]) == 1 and r["sources"][0]["url"].startswith("https://")

    def test_calendar(self, client, db):
        future = Event(name="日历测试峰会", start_time=datetime.utcnow() + timedelta(days=2))
        db.add(future)
        db.commit()
        r = client.get("/calendar").json()
        assert any(e["id"] == future.id for e in r["items"])


class TestNotifications:
    def test_rule_filter_and_send_with_dedup(self, db):
        from app.models import NotificationRule
        from app.services.notifier import dispatch_new_event
        rule = NotificationRule(name="免费活动通知", channel="webhook",
                                config={"url": "https://hook.example.com/notify"},
                                filters={"free_only": True}, enabled=True)
        db.add(rule)
        db.commit()
        ev = Event(name="免费 AI 大会", is_free=True, city="上海")
        db.add(ev)
        db.commit()
        calls = []
        def fake_post(url, **kw):
            calls.append(url)
            return httpx.Response(200, content=b"ok", request=httpx.Request("POST", url))
        with patch("app.services.notifier.httpx.post", fake_post):
            n1 = dispatch_new_event(db, ev)
            n2 = dispatch_new_event(db, ev)  # same event again — dedup must block
        assert n1 == 1 and n2 == 0
        assert len(calls) == 1

    def test_filter_rejects_nonfree(self, db):
        from app.models import NotificationRule
        from app.services.notifier import dispatch_new_event, event_matches_filters
        rule = NotificationRule(name="免费", channel="webhook", filters={"free_only": True})
        ev = Event(name="收费大会", is_free=False)
        assert event_matches_filters(ev, rule.filters) is False

    def test_missing_credentials_recorded_not_crash(self, db):
        from app.models import NotificationRule
        from app.services.notifier import dispatch_new_event
        rule = NotificationRule(name="邮件通知", channel="email", config={}, enabled=True)
        db.add(rule)
        db.commit()
        ev = Event(name="测试邮件大会")
        db.add(ev)
        db.commit()
        n = dispatch_new_event(db, ev)
        assert n == 0
        from app.models import NotificationLog
        log = db.query(NotificationLog).filter_by(rule_id=rule.id).first()
        assert log.status == "skipped_no_credentials"

    def test_rule_api_and_test_endpoint(self, client, auth_headers):
        r = client.post("/notification-rules", headers=auth_headers, json={
            "name": "测试规则", "channel": "telegram",
            "config": {"bot_token": "123:abc", "chat_id": "1"}, "filters": {}})
        assert r.status_code == 201
        rules = client.get("/notification-rules").json()["rules"]
        mine = next(r for r in rules if r["name"] == "测试规则")
        assert mine["config"]["bot_token"] == "***"  # credentials masked in API
        t = client.post(f"/notification-rules/{mine['id']}/test").json()
        assert t["ok"] is False  # real telegram would fail in test env — reported honestly


class TestStats:
    def test_dashboard_and_analysis_use_real_data(self, client, db):
        db.add(Event(name="统计测试大会"))
        db.commit()
        d = client.get("/stats/dashboard").json()
        assert d["new_events_today"] >= 1
        assert d["system"]["scheduler_running"] is False  # scheduler disabled in tests — honest state
        a = client.get("/stats/analysis").json()
        assert a["total_events"] >= 1 and "by_country" in a


class TestSettingsSecurity:
    def test_sensitive_key_blocked(self, client, auth_headers):
        r = client.put("/settings/system_setting", params={"key": "jwt_secret"},
                       headers=auth_headers, json={"v": "hack"})
        assert r.status_code == 400

    def test_settings_endpoint_no_secrets(self, client):
        body = json.dumps(client.get("/settings").json())
        for secret in ("jwt_secret", "smtp_password", "ai_api_key"):
            assert secret not in body
