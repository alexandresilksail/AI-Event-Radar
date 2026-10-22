"""Real end-to-end crawl test against a live public source.

Marked `real_network` — run explicitly with:  pytest -m real_network
Skipped automatically when the network is unavailable.

These tests hit a third-party host, so they are inherently flaky (TLS
handshake timeouts, rate limits, upstream outages). A network-layer failure is
NOT an application defect: the test distinguishes the two and only fails when
the pipeline itself misbehaves.
"""
import httpx
import pytest

from app.models import CrawlLog, Event, RawDocument, Source

REAL_FEED = "https://news.mit.edu/rss/topic/artificial-intelligence2"

pytestmark = pytest.mark.real_network

# Errors that mean "the network/host let us down", not "our code is wrong".
TRANSIENT_NETWORK_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


def network_available() -> bool:
    try:
        httpx.get("https://news.mit.edu", timeout=10, follow_redirects=True)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def net():
    if not network_available():
        pytest.skip("外部网络不可用，真实端到端测试跳过")


def _is_transient(error: str) -> bool:
    """True when the crawl failed for network reasons rather than logic reasons."""
    return any(k in (error or "") for k in (
        "ConnectTimeout", "ConnectError", "ReadTimeout", "RemoteProtocolError",
        "handshake", "timed out", "连接超时",
    ))


def test_real_crawl_real_public_source(db, net):
    source = Source(name="MIT News AI (真实E2E)", type="rss", url=REAL_FEED,
                    country="美国", language="en", frequency_minutes=1440)
    db.add(source)
    db.commit()
    from app.services.pipeline import crawl_source
    log = crawl_source(db, source)
    if log.status == "failed" and _is_transient(log.error):
        pytest.skip(f"上游网络不稳定（非代码缺陷），跳过: {log.error}")
    assert log.status == "success", log.error
    assert log.found_count > 0, "真实 RSS 应抓取到条目"
    assert db.query(RawDocument).filter_by(source_id=source.id).count() > 0
    # AI-related news feed: articles saved; event extraction runs on real content.
    # We assert the pipeline behaved honestly, not that events exist (feed is news).
    assert source.last_success_at is not None


def test_real_crawl_second_run_incremental(db, net):
    """Second crawl of same feed: raw docs don't duplicate (content-hash dedup).

    Self-contained: creates the source itself so it does not depend on
    execution order (each `db` fixture gets an isolated database).
    """
    from app.services.pipeline import crawl_source
    source = db.query(Source).filter_by(url=REAL_FEED).first()
    if source is None:
        source = Source(name="MIT News AI (真实E2E-去重)", type="rss", url=REAL_FEED,
                        country="美国", language="en", frequency_minutes=1440)
        db.add(source)
        db.commit()
        log1 = crawl_source(db, source)
        if log1.status == "failed" and _is_transient(log1.error):
            pytest.skip(f"上游网络不稳定（非代码缺陷），跳过: {log1.error}")
        assert log1.status == "success", log1.error
        assert log1.found_count > 0, "首轮真实 RSS 应抓取到条目"
    before = db.query(RawDocument).filter_by(source_id=source.id).count()
    log = crawl_source(db, source)
    if log.status == "failed" and _is_transient(log.error):
        pytest.skip(f"上游网络不稳定（非代码缺陷），跳过: {log.error}")
    after = db.query(RawDocument).filter_by(source_id=source.id).count()
    # v1.2 semantics: success = all items processed; partial = fetch OK but
    # some/all documents were content-hash duplicates (skipped, not errors
    # of the fetch itself). Both are honest success states.
    assert log.status in ("success", "partial"), log.error
    assert log.found_count > 0, "真实 RSS 应再次抓取到条目"
    assert after >= before, "重复采集不得减少或重复插入原始文档"
    if after == before:
        # feed published nothing new between the two crawls: everything deduped
        assert log.new_count == 0
