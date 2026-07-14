import pytest

import collector
from collector import collect, effective_since, merge_hits
from db import get_watermark, set_watermark


def test_effective_since_rewinds_by_two_hours():
    assert effective_since(10_000) == 2_800


def test_merge_hits_unions_keywords_and_query_version():
    hit = {"objectID": "10", "title": "DeepSeek", "created_at_i": 100}
    rows = merge_hits({"LLM": [hit], "DeepSeek": [hit]})
    assert rows[0]["matched_keywords"] == "DeepSeek,LLM"
    assert rows[0]["collection_query_version"] == "v1"


# ── collect() 안전성 테스트 (오프라인, 네트워크/키 불필요) ──────────
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """Algolia 응답을 흉내내는 세션. 모든 키워드에 동일한 한 페이지를 돌려준다."""

    def __init__(self, hits):
        self._hits = hits

    def get(self, url, params=None, timeout=None):
        return _FakeResponse({"hits": self._hits, "nbPages": 1})


class _RaisingSession:
    """첫 HTTP 요청에서 실패를 던지는 세션."""

    def get(self, url, params=None, timeout=None):
        raise RuntimeError("simulated HTTP failure")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # 키워드별 REQUEST_PAUSE_SECONDS sleep을 제거해 테스트를 빠르게 유지한다.
    monkeypatch.setattr(collector.time, "sleep", lambda *_: None)


def test_collect_default_run_advances_watermark(temporary_db):
    hits = [
        {"objectID": "a", "created_at_i": 3_000},
        {"objectID": "b", "created_at_i": 5_000},
    ]
    session = _FakeSession(hits)

    processed, max_ts = collect(temporary_db, session, since_ts=0, update_watermark=True)

    assert processed == 2
    assert max_ts == 5_000
    assert get_watermark(temporary_db) == "5000"
    count = temporary_db.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    assert count == 2


def test_collect_backfill_does_not_change_watermark(temporary_db):
    set_watermark(temporary_db, "1000")
    hits = [{"objectID": "a", "created_at_i": 9_000}]
    session = _FakeSession(hits)

    collect(temporary_db, session, since_ts=0, update_watermark=False)

    assert get_watermark(temporary_db) == "1000"
    count = temporary_db.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    assert count == 1


def test_collect_http_failure_does_not_advance_watermark(temporary_db):
    set_watermark(temporary_db, "1000")
    session = _RaisingSession()

    with pytest.raises(RuntimeError):
        collect(temporary_db, session, since_ts=0, update_watermark=True)

    assert get_watermark(temporary_db) == "1000"
