"""Unit tests: SSRF guard, URL/name normalization, rule-based extraction, date parsing."""
import socket
from datetime import datetime

import pytest

from app.security import SSRFError, validate_public_url, sanitize_text
from app.services.extraction import extract_dates, extract_location, is_ai_related, detect_type
from app.services.dedup import normalize_url, normalize_name, title_similarity
from app.collectors.base import CollectedItem


class TestSSRF:
    def test_blocks_localhost(self):
        with pytest.raises(SSRFError):
            validate_public_url("http://localhost:8000/admin")

    def test_blocks_private_ip(self):
        with pytest.raises(SSRFError):
            validate_public_url("http://192.168.1.10/secret")

    def test_blocks_metadata_endpoint(self):
        with pytest.raises(SSRFError):
            validate_public_url("http://169.254.169.254/latest/meta-data")

    def test_blocks_non_http_scheme(self):
        with pytest.raises(SSRFError):
            validate_public_url("file:///etc/passwd")

    def test_blocks_dns_rebind_to_private(self):
        with pytest.raises(SSRFError):
            validate_public_url("http://127.0.0.1.nip.io/")

    def test_allows_public(self):
        # 公网域名是否放行取决于真实 DNS。沙箱 DNS 不可用时明确 SKIP（环境问题），
        # 若解析成功仍被策略拒绝则如实 FAIL——不伪造通过。
        try:
            socket.getaddrinfo("news.mit.edu", None)
        except OSError as e:
            pytest.skip(f"沙箱 DNS 不可用，无法验证公网域名放行: {e}")
        assert validate_public_url("https://news.mit.edu/rss") == "https://news.mit.edu/rss"


class TestSanitize:
    def test_strips_control_chars_and_caps_length(self):
        dirty = "ab\x00\x08cd" + "x" * 50000
        out = sanitize_text(dirty, 100)
        assert len(out) == 100 and "\x00" not in out and "\x08" not in out


class TestDedupHelpers:
    def test_normalize_url(self):
        assert normalize_url("https://WWW.Example.com/event/?utm=x#top") == "example.com/event"

    def test_normalize_name(self):
        assert normalize_name("2026 AI 大会 Conference!") == "2026ai大会conference"

    def test_similarity(self):
        assert title_similarity("2026全球人工智能大会", "2026全球人工智能大会 ") > 0.95
        assert title_similarity("AI Summit 2026", "AI Expo 2027") < 0.7


class TestExtraction:
    def test_ai_related_zh(self):
        assert is_ai_related("2026 上海人工智能大会将于明年举行")

    def test_ai_related_en(self):
        assert is_ai_related("Join us at the Machine Learning Conference in Berlin")

    def test_not_ai_related(self):
        assert not is_ai_related("全市猪肉价格上涨，菜市场人流如织")

    def test_date_range_cn(self):
        start, end = extract_dates("活动将于2026年11月3日至11月5日在上海举办")
        assert start == datetime(2026, 11, 3)
        assert end == datetime(2026, 11, 5)

    def test_date_single_iso(self):
        start, end = extract_dates("The summit starts 2026-12-01 in Tokyo")
        assert start == datetime(2026, 12, 1) and end is None

    def test_location_and_city(self):
        city, country, online = extract_location("本届大会在北京国家会议中心举办")
        assert city == "北京" and country == "中国" and online is False  # 城市明确 → 推断为线下

    def test_online_detection(self):
        _, _, online = extract_location("本次研讨会将以线上直播形式进行")
        assert online is True

    def test_event_type(self):
        assert detect_type("2026 AI EXPO 全球人工智能博览会") == "展会/博览会"
        assert detect_type("世界人工智能大会 summit") == "峰会/论坛"

    def test_rule_extract_rejects_non_event(self):
        from app.services.extraction import rule_extract
        item = CollectedItem(url="https://example.com/x", title="公司季度财报发布",
                             content="营收增长 10%，无任何活动信息")
        assert rule_extract(item)["is_event"] is False

    def test_ai_extract_never_fabricates(self):
        """LLM output without a real date must NOT be invented as datetime."""
        from app.services.extraction import ai_extract
        from unittest.mock import patch
        item = CollectedItem(url="https://x.com/a", title="AI Summit",
                             content="an AI conference will be held")
        fake = {"choices": [{"message": {"content": '{"is_event": true, "name": "AI Summit", "start_time": "未知", "city": "未知", "confidence": 0.9}'}}]}
        with patch("app.services.extraction.settings") as s, patch("httpx.post") as post:
            s.AI_BASE_URL, s.AI_API_KEY, s.AI_MODEL = "http://x", "k", "m"
            post.return_value.json.return_value = fake
            out = ai_extract(item)
        assert out["is_event"] is True
        assert out["start_time"] is None  # 未知 -> None, never fabricated
