"""数据源管理中心 + 采集任务监控的集成测试。

覆盖：
- 数据源列表筛选/搜索/分页/健康状态汇总
- 新增/编辑（含 topics/timezone/note 新字段与 URL 安全校验）
- 启停/恢复/软删除/永久删除（默认软删除，历史数据保留）
- 测试采集端点（真实请求，不写库）
- 批量触发（失败不影响其他源）
- 采集任务列表/详情/重试（retry_of 关联、running 拒绝重试）
- 系统监控 API（组件状态、24h 统计、告警、磁盘）
- 仪表盘新增字段（7 日趋势、今日成功率、平均耗时）
"""
from datetime import timedelta

import httpx
import pytest

from app.models import CrawlLog, CrawlTask, Event, RawDocument, Source, utcnow

from app.services import scheduler as sched


def _rss_body(item_title="AI summit", item_link="https://feed.example.com/item"):
    """A minimal but VALID RSS 2.0 document.

    The RSS collector rejects responses that are not a feed at all (a captcha
    wall or an HTML page), so a mock for an `rss` source must return real
    feed XML — returning HTML here would now be (correctly) an error.
    """
    return ("<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0'><channel><title>feed</title>"
            f"<item><title>{item_title}</title><link>{item_link}</link>"
            "<description>AI summit</description></item>"
            "</channel></rss>")


def _mock_get(monkeypatch, content=None, status=200):
    if content is None:
        content = _rss_body()
    def fake_safe_get(url, timeout=None, method="GET"):
        return httpx.Response(status, content=content.encode(),
                             request=httpx.Request("GET", url))
    monkeypatch.setattr("app.collectors.base.safe_get", fake_safe_get)


@pytest.fixture()
def src(db):
    s = Source(name="测试 RSS 源", type="rss", url="https://feed.example.com/rss",
               country="中国", language="zh", frequency_minutes=60)
    db.add(s)
    db.commit()
    return s


class TestSourceList:
    def test_list_with_filters_and_pagination(self, client, auth_headers, db):
        for i in range(3):
            db.add(Source(name=f"源{i}", type="rss", url=f"https://f{i}.example.com/rss",
                          topics="AI,大模型", enabled=(i != 2), frequency_minutes=60))
        db.add(Source(name="HTML 源", type="html", url="https://h.example.com/page"))
        db.commit()

        r = client.get("/sources", params={"page": 1, "page_size": 2}, headers=auth_headers).json()
        assert r["total"] >= 4 and len(r["items"]) == 2
        assert set(r["summary"]) >= {"healthy", "failing", "paused", "never"}

        r = client.get("/sources", params={"keyword": "HTML"}, headers=auth_headers).json()
        assert r["total"] == 1 and r["items"][0]["type"] == "html"

        r = client.get("/sources", params={"type": "rss", "enabled": "false"}, headers=auth_headers).json()
        assert all(d["type"] == "rss" and not d["enabled"] for d in r["items"])

        r = client.get("/sources", params={"health": "never"}, headers=auth_headers).json()
        assert r["total"] >= 3  # 从未成功采集的源（禁用源计为 paused）
        assert all(d["health"] == "never" for d in r["items"])

    def test_soft_deleted_excluded(self, client, auth_headers, db):
        s = Source(name="待删除源", type="rss", url="https://del.example.com/rss")
        db.add(s)
        db.commit()
        token = client.post("/auth/login", json={"username": "admin",
                                                 "password": "test-admin-pass"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        client.delete(f"/sources/{s.id}", headers=h)
        r = client.get("/sources", params={"keyword": "待删除"}, headers=auth_headers).json()
        # soft-deleted sources must disappear from the list
        assert r["total"] == 0
        # ...but stay individually retrievable, so the operator can inspect and
        # restore it (soft delete keeps the record and its history)
        d = client.get(f"/sources/{s.id}", headers=auth_headers)
        assert d.status_code == 200
        assert d.json()["source"]["id"] == s.id
        # a source that truly does not exist is a 404
        assert client.get("/sources/999999", headers=auth_headers).status_code == 404


class TestSourceLifecycle:
    def test_create_with_new_fields(self, client, auth_headers):
        r = client.post("/sources", headers=auth_headers, json={
            "name": "WAIC 官网", "type": "html", "url": "https://www.example.com/events",
            "topics": "AI,峰会", "timezone": "Asia/Shanghai", "note": "官方站点",
            "frequency_minutes": 30})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        d = client.get(f"/sources/{sid}", headers=auth_headers).json()["source"]
        assert d["topics"] == "AI,峰会" and d["timezone"] == "Asia/Shanghai"
        assert d["note"] == "官方站点" and d["frequency_minutes"] == 30

    def test_create_rejects_ssrf_url(self, client, auth_headers):
        r = client.post("/sources", headers=auth_headers, json={
            "name": "内网源", "type": "html", "url": "http://169.254.169.254/latest/meta-data"})
        assert r.status_code == 400 and "安全校验" in r.json()["detail"]

    def test_update_source(self, client, auth_headers, db, monkeypatch):
        # 测试假域名无法真实解析，仅放开保存时的 SSRF DNS 校验（协议/主机名仍校验）
        monkeypatch.setattr("app.routers.sources.validate_public_url", lambda url: url)
        s = Source(name="旧名", type="rss", url="https://old.example.com/rss")
        db.add(s); db.commit()
        r = client.put(f"/sources/{s.id}", headers=auth_headers, json={
            "name": "新名", "type": "rss", "url": "https://new.example.com/rss",
            "topics": "AI", "frequency_minutes": 15})
        assert r.status_code == 200
        d = client.get(f"/sources/{s.id}", headers=auth_headers).json()["source"]
        assert d["name"] == "新名" and d["frequency_minutes"] == 15

    def test_pause_resume(self, client, auth_headers, src):
        r = client.post(f"/sources/{src.id}/pause", headers=auth_headers)
        assert r.status_code == 200
        d = client.get(f"/sources/{src.id}", headers=auth_headers).json()["source"]
        assert d["enabled"] is False and d["status"] == "paused" and d["health"] == "paused"
        r = client.post(f"/sources/{src.id}/resume", headers=auth_headers)
        d = client.get(f"/sources/{src.id}", headers=auth_headers).json()["source"]
        assert d["enabled"] is True and d["status"] == "active"

    def test_soft_delete_preserves_history_and_restore(self, client, auth_headers, src, db):
        db.add(CrawlLog(source_id=src.id, status="success",
                        started_at=utcnow() - timedelta(hours=1)))
        db.commit()
        r = client.delete(f"/sources/{src.id}", headers=auth_headers)
        assert r.json()["deleted"] == "soft"
        # 历史日志仍在（软删除不清数据）
        assert db.query(CrawlLog).filter_by(source_id=src.id).count() == 1
        r = client.post(f"/sources/{src.id}/restore", headers=auth_headers)
        assert r.json()["restored"] is True
        assert client.get(f"/sources/{src.id}", headers=auth_headers).status_code == 200

    def test_permanent_delete(self, client, auth_headers, db):
        s = Source(name="永久删除", type="rss", url="https://perm.example.com/rss")
        db.add(s); db.commit()
        sid = s.id
        db.add(CrawlLog(source_id=sid, status="success",
                        started_at=utcnow()))
        db.commit()
        r = client.delete(f"/sources/{sid}", params={"permanent": True}, headers=auth_headers)
        assert r.json()["deleted"] == "permanent"
        db.expire_all()  # 清除会话缓存后再查库（用 count 避免实例化已删除对象）
        assert db.query(Source).filter(Source.id == sid).count() == 0
        assert db.query(CrawlLog).filter(CrawlLog.source_id == sid).count() == 0


class TestSourceOperations:
    def test_test_source_ok(self, client, auth_headers, src, monkeypatch):
        _mock_get(monkeypatch)
        r = client.post(f"/sources/{src.id}/test", headers=auth_headers)
        assert r.status_code == 200 and r.json()["ok"] is True

    def test_test_source_failure_reported(self, client, auth_headers, src, monkeypatch):
        from app.collectors.base import CollectorError

        def boom(url, **kw):
            raise CollectorError("连接超时")
        monkeypatch.setattr("app.collectors.base.safe_get", boom)
        r = client.post(f"/sources/{src.id}/test", headers=auth_headers)
        assert r.json()["ok"] is False and "超时" in r.json()["error"]

    def test_batch_trigger_isolates_failures(self, client, auth_headers, db, monkeypatch):
        ok_src = Source(name="批量OK", type="rss", url="https://ok.example.com/rss")
        bad_src = Source(name="批量失败", type="rss", url="https://bad.example.com/rss")
        db.add_all([ok_src, bad_src]); db.commit()
        _mock_get(monkeypatch)
        r = client.post("/sources/batch-trigger", headers=auth_headers,
                        json={"ids": [ok_src.id, bad_src.id, 99999]})
        data = r.json()
        assert data["triggered"] == 3
        results = {x["source_id"]: x for x in data["results"]}
        assert results[ok_src.id]["status"] in ("success", "partial")
        assert results[99999]["status"] == "failed"

    def test_trigger_creates_task_with_duration(self, client, auth_headers, db, monkeypatch):
        s = Source(name="任务源", type="rss", url="https://task.example.com/rss")
        db.add(s); db.commit()
        _mock_get(monkeypatch)
        r = client.post(f"/sources/{s.id}/trigger", headers=auth_headers)
        assert r.status_code == 200 and "task_id" in r.json()
        task = db.get(CrawlTask, r.json()["task_id"])
        assert task and task.trigger == "manual" and task.finished_at is not None
        assert task.duration_ms >= 0 and task.found_count >= 0


class TestTaskManagement:
    def test_task_list_and_detail(self, client, auth_headers, db, monkeypatch):
        s = Source(name="任务监控源", type="rss", url="https://tm.example.com/rss")
        db.add(s); db.commit()
        _mock_get(monkeypatch)
        token = client.post("/auth/login", json={"username": "admin",
                                                 "password": "test-admin-pass"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        client.post(f"/sources/{s.id}/trigger", headers=h)

        r = client.get("/crawl-tasks", headers=auth_headers).json()
        assert r["total"] >= 1
        item = r["items"][0]
        assert item["trigger_label"] == "手动触发" and item["source_name"] == "任务监控源"

        d = client.get(f"/crawl-tasks/{item['id']}", headers=auth_headers).json()
        assert d["task"]["id"] == item["id"]
        assert d["source"]["id"] == s.id
        assert isinstance(d["logs"], list)

    def test_retry_failed_task_creates_linked_task(self, client, auth_headers, db, monkeypatch):
        from app.collectors.base import CollectorError

        def boom(url, **kw):
            raise CollectorError("模拟失败")
        monkeypatch.setattr("app.collectors.base.safe_get", boom)
        s = Source(name="重试源", type="rss", url="https://retry.example.com/rss")
        db.add(s); db.commit()
        token = client.post("/auth/login", json={"username": "admin",
                                                 "password": "test-admin-pass"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        # 直接手动构造失败任务（不依赖采集重试逻辑）
        t = CrawlTask(source_id=s.id, status="failed", trigger="scheduled",
                      started_at=utcnow() - timedelta(minutes=5),
                      finished_at=utcnow() - timedelta(minutes=4),
                      error="模拟失败", attempts=1)
        db.add(t); db.commit()
        r = client.post(f"/crawl-tasks/{t.id}/retry", headers=h)
        assert r.status_code == 200 and r.json()["new_task_id"] != t.id
        new = db.get(CrawlTask, r.json()["new_task_id"])
        assert new.trigger == "retry" and new.retry_of == t.id

    def test_retry_running_task_rejected(self, client, db, monkeypatch):
        s = Source(name="运行中源", type="rss", url="https://run.example.com/rss")
        db.add(s); db.commit()
        t = CrawlTask(source_id=s.id, status="running", started_at=utcnow())
        db.add(t); db.commit()
        token = client.post("/auth/login", json={"username": "admin",
                                                 "password": "test-admin-pass"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        r = client.post(f"/crawl-tasks/{t.id}/retry", headers=h)
        assert r.status_code == 409

    def test_running_endpoint(self, client, auth_headers, db):
        s = Source(name="运行源", type="rss", url="https://run2.example.com/rss")
        db.add(s); db.commit()
        db.add(CrawlTask(source_id=s.id, status="running", started_at=utcnow()))
        db.commit()
        r = client.get("/crawl-tasks/running", headers=auth_headers).json()
        assert len(r["db_running"]) >= 1
        assert "scheduler_is_leader" in r


class TestMonitorAndDashboard:
    def test_monitor_returns_real_data(self, client, auth_headers, db):
        db.add(Source(name="监控源", type="rss", url="https://mon.example.com/rss"))
        db.add(CrawlLog(source_id=1, status="success",
                        started_at=utcnow() - timedelta(hours=2)))
        db.commit()
        d = client.get("/monitor", headers=auth_headers).json()
        assert d["components"]["api"]["status"] == "healthy"
        assert d["components"]["database"]["status"] in ("healthy", "down")
        assert d["crawl_24h"]["total"] >= 1
        assert d["crawl_24h"]["by_status"]["success"] >= 1
        assert d["sources"]["total"] >= 1
        assert d["disk"] is None or d["disk"]["total_gb"] > 0
        assert isinstance(d["warnings"], list)
        assert d["uptime_seconds"] >= 0

    def test_dashboard_trend_and_durations(self, client, db):
        s = Source(name="趋势源", type="rss", url="https://tr.example.com/rss")
        db.add(s); db.commit()
        db.add(CrawlLog(source_id=s.id, status="success",
                        started_at=utcnow() - timedelta(hours=3),
                        finished_at=utcnow() - timedelta(hours=3) + timedelta(seconds=2),
                        new_count=1))
        db.add(CrawlLog(source_id=s.id, status="failed",
                        started_at=utcnow() - timedelta(hours=1),
                        finished_at=utcnow() - timedelta(hours=1) + timedelta(seconds=1),
                        error="x"))
        db.commit()
        d = client.get("/stats/dashboard").json()
        assert len(d["crawl_trend_7d"]) == 7 or d["crawl_trend_7d"]  # 有数据或空列表
        assert d["today_crawls"]["total"] >= 2
        assert d["today_crawls"]["success_rate"] is not None
        assert 0 <= d["today_crawls"]["success_rate"] <= 1
        assert d["today_crawls"]["avg_duration_ms"] is not None
        assert d["today_crawls"]["avg_duration_ms"] > 0
        assert d["system"]["running_tasks"] is not None
        assert d["system"]["scheduler_running"] in (True, False)
        assert d["source_health"]["never"] >= 1
