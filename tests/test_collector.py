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
    assert rows[0]["collection_query_version"] == "v2"


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


class _ScriptedSession:
    """요청 순서대로 미리 정해둔 응답을 돌려주고 각 요청의 params를 기록한다."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResponse(self._payloads.pop(0))


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

    processed, max_ts = collect(temporary_db, session, since_ts=0, until_ts=10_000, update_watermark=True)

    assert processed == 2
    assert max_ts == 5_000
    assert get_watermark(temporary_db) == "5000"
    count = temporary_db.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    assert count == 2


def test_collect_backfill_does_not_change_watermark(temporary_db):
    set_watermark(temporary_db, "1000")
    hits = [{"objectID": "a", "created_at_i": 9_000}]
    session = _FakeSession(hits)

    collect(temporary_db, session, since_ts=0, until_ts=10_000, update_watermark=False)

    assert get_watermark(temporary_db) == "1000"
    count = temporary_db.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    assert count == 1


def test_collect_http_failure_does_not_advance_watermark(temporary_db):
    set_watermark(temporary_db, "1000")
    session = _RaisingSession()

    with pytest.raises(RuntimeError):
        collect(temporary_db, session, since_ts=0, until_ts=10_000, update_watermark=True)

    assert get_watermark(temporary_db) == "1000"


# ── backfill 구간 분할 ───────────────────────────────────────────
def test_backfill_slices_cover_range_without_gaps_or_overlaps():
    week = 7 * 86_400
    slices = collector.backfill_slices(0, 180 * 86_400, week)
    assert slices[0][0] == 0
    assert slices[-1][1] == 180 * 86_400
    assert all(end - start <= week for start, end in slices)
    assert all(a_end == b_start for (_, a_end), (b_start, _) in zip(slices, slices[1:]))
    assert len(slices) == 26  # ceil(180 / 7)


def test_backfill_slices_last_slice_is_shorter_when_range_is_not_a_multiple():
    slices = collector.backfill_slices(0, 10, 7)
    assert slices == [(0, 7), (7, 10)]


def test_backfill_slices_empty_for_empty_range():
    assert collector.backfill_slices(100, 100, 7) == []
    assert collector.backfill_slices(200, 100, 7) == []


# ── search_keyword: 반열린 구간과 한계 분할 ──────────────────────
def test_search_keyword_sends_half_open_interval_filter():
    session = _ScriptedSession([{"hits": [], "nbPages": 1}])
    collector.search_keyword(session, "GPT", 100, 200)
    params = session.calls[0]
    assert params["numericFilters"] == "created_at_i>=100,created_at_i<200"
    assert params["tags"] == "story"


def test_search_keyword_splits_capped_slice_into_two_halves():
    capped = {"hits": [{"objectID": "junk", "created_at_i": 110}], "nbPages": collector.ALGOLIA_MAX_PAGES}
    left = {"hits": [{"objectID": "L", "created_at_i": 120}], "nbPages": 1}
    right = {"hits": [{"objectID": "R", "created_at_i": 170}], "nbPages": 1}
    session = _ScriptedSession([capped, left, right])

    hits = collector.search_keyword(session, "GPT", 100, 200)

    # 한계에 닿은 구간의 hits는 버리고, 두 하위 구간에서 다시 수집한다.
    assert [hit["objectID"] for hit in hits] == ["L", "R"]
    assert session.calls[1]["numericFilters"] == "created_at_i>=100,created_at_i<150"
    assert session.calls[2]["numericFilters"] == "created_at_i>=150,created_at_i<200"


def test_search_keyword_raises_when_one_second_slice_is_still_capped():
    session = _ScriptedSession(
        [{"hits": [], "nbPages": collector.ALGOLIA_MAX_PAGES}]
    )
    with pytest.raises(RuntimeError):
        collector.search_keyword(session, "GPT", 100, 101)


# ── collect_backfill: 전량 성공 후에만 upsert ────────────────────
def test_collect_backfill_upserts_and_keeps_watermark(temporary_db):
    set_watermark(temporary_db, "1000")
    session = _FakeSession([{"objectID": "a", "created_at_i": 9_000}])

    processed = collector.collect_backfill(temporary_db, session, 0, 14 * 86_400)

    assert processed == 1
    assert get_watermark(temporary_db) == "1000"
    assert temporary_db.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_collect_backfill_writes_nothing_when_a_late_request_fails(temporary_db):
    class _FailsAfter:
        """앞 몇 요청은 성공하고 그 뒤부터 실패하는 세션."""

        def __init__(self, fail_after):
            self.fail_after = fail_after
            self.count = 0

        def get(self, url, params=None, timeout=None):
            self.count += 1
            if self.count > self.fail_after:
                raise RuntimeError("simulated HTTP failure")
            return _FakeResponse(
                {"hits": [{"objectID": "a", "created_at_i": 100}], "nbPages": 1}
            )

    session = _FailsAfter(fail_after=3)

    with pytest.raises(RuntimeError):
        collector.collect_backfill(temporary_db, session, 0, 14 * 86_400)

    assert temporary_db.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
    assert get_watermark(temporary_db) is None


def test_collect_backfill_queries_every_slice_for_every_keyword(temporary_db):
    session = _ScriptedSession(
        [{"hits": [], "nbPages": 1}] * (len(collector.KEYWORDS) * 2)
    )

    collector.collect_backfill(temporary_db, session, 0, 14 * 86_400)

    # 14일 범위 → 7일 구간 2개 × 키워드 수만큼 요청한다.
    assert len(session.calls) == len(collector.KEYWORDS) * 2
    assert session.calls[0]["numericFilters"] == f"created_at_i>=0,created_at_i<{7 * 86_400}"
