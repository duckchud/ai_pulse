# Time-Sliced Keyword Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--backfill 180` collect a complete 6-month window per keyword by slicing the range into ≤7-day half-open intervals and recursively halving any slice that hits Algolia's ~1,000-result pagination cap, instead of silently truncating.

**Architecture:** `search_keyword` becomes a half-open-interval fetcher that detects the Algolia cap (`nbPages >= ALGOLIA_MAX_PAGES`) and recursively splits itself; a new pure function `backfill_slices` produces the 7-day slicing; a new `collect_backfill` walks keywords × slices, and only after every request succeeds does one `merge_hits` + `upsert_stories` write happen (all-or-nothing). The incremental path keeps its 2-hour-overlap watermark semantics and reuses the same `search_keyword`.

**Tech Stack:** Python 3.12, requests (mocked in tests), SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-16-time-sliced-keyword-backfill-design.md`

## Global Constraints

- Four-space indentation, English identifiers, concise Korean comments (existing collector.py style).
- The worktree sits on a Windows mount and edits tend to come out CRLF while the git index holds LF. After editing each file, run `sed -i 's/\r$//' <file>` and confirm `git diff --stat` shows only your real change before staging. Stage ONLY the files each task names; never stage `ai_monitor.db`.
- `--backfill DAYS` must never change the watermark; the incremental path must keep `OVERLAP_SECONDS = 7_200` rewind behavior unchanged (`effective_since` is not touched by this plan).
- Backfill must be all-or-nothing: no DB write may happen until every keyword × slice request has succeeded.
- All tests run offline with fake sessions — no network, no keys.
- Run tests with `python3 -m pytest` (plain `pytest`/`python` are not on PATH in this environment).

## File Structure

- `config.py` — two new constants (`BACKFILL_SLICE_DAYS`, `ALGOLIA_MAX_RESULTS`).
- `collector.py` — `backfill_slices` (new pure function), `search_keyword` (rewritten to half-open range + cap recursion), `collect` (gains `until_ts` parameter), `collect_backfill` (new), `main` (backfill branch rewired).
- `tests/test_collector.py` — new tests per task; three existing `collect` tests updated for the new parameter.

---

### Task 1: Slice math and config constants

**Files:**
- Modify: `config.py` (append after `OVERLAP_SECONDS`)
- Modify: `collector.py` (new function after `effective_since`)
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `config.BACKFILL_SLICE_DAYS: int = 7`, `config.ALGOLIA_MAX_RESULTS: int = 1_000`.
- Produces: `collector.backfill_slices(since_ts: int, until_ts: int, slice_seconds: int) -> list[tuple[int, int]]` — consecutive half-open `[start, end)` slices covering `[since_ts, until_ts)` exactly, each at most `slice_seconds` long; `[]` when `since_ts >= until_ts`. Task 3 consumes this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_collector.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_collector.py -k backfill_slices -q`
Expected: FAIL — `AttributeError: module 'collector' has no attribute 'backfill_slices'`.

- [ ] **Step 3: Add the config constants**

In `config.py`, insert directly after the `OVERLAP_SECONDS = 7_200` line:

```python
BACKFILL_SLICE_DAYS = 7
# Algolia는 페이지네이션 결과를 쿼리당 약 1,000건으로 제한한다(paginateLimitedTo).
ALGOLIA_MAX_RESULTS = 1_000
```

- [ ] **Step 4: Implement `backfill_slices`**

In `collector.py`, add after `effective_since`:

```python
# ── backfill 구간 분할 ───────────────────────────────────────────
def backfill_slices(
    since_ts: int, until_ts: int, slice_seconds: int
) -> list[tuple[int, int]]:
    """[since_ts, until_ts)를 최대 slice_seconds 길이의 연속 반열린 구간으로 나눈다."""
    slices = []
    start = since_ts
    while start < until_ts:
        end = min(start + slice_seconds, until_ts)
        slices.append((start, end))
        start = end
    return slices
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `python3 -m pytest tests/test_collector.py -q`
Expected: all pass (3 new + existing).

- [ ] **Step 6: Normalize line endings and commit**

```bash
sed -i 's/\r$//' config.py collector.py tests/test_collector.py
git diff --stat   # config.py +3, collector.py +~14, tests +~20 lines only
git add config.py collector.py tests/test_collector.py
git diff --cached --stat
git commit -m "feat: add backfill slice math and Algolia cap constants"
```

---

### Task 2: Half-open-interval search with recursive cap splitting

**Files:**
- Modify: `collector.py` (`search_keyword` rewritten; `collect` gains `until_ts`; imports)
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `config.ALGOLIA_MAX_RESULTS`, `config.HITS_PER_PAGE` (Task 1).
- Produces: `collector.ALGOLIA_MAX_PAGES: int` (module constant, `= ALGOLIA_MAX_RESULTS // HITS_PER_PAGE`).
- Produces: `collector.search_keyword(session, keyword: str, start_ts: int, end_ts: int) -> list[dict]` — fetches only `[start_ts, end_ts)`; on cap, recursively halves; raises `RuntimeError` if a 1-second slice is still capped. Task 3 consumes this.
- Produces: `collector.collect(conn, session, since_ts: int, until_ts: int, update_watermark: bool = True) -> tuple[int, int]` — same behavior as today but the search window is now `[since_ts, until_ts)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_collector.py`, add after the `_RaisingSession` class:

```python
class _ScriptedSession:
    """요청 순서대로 미리 정해둔 응답을 돌려주고 각 요청의 params를 기록한다."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResponse(self._payloads.pop(0))
```

Append these tests:

```python
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
```

Update the three existing `collect` tests to pass the new parameter — replace their call lines:

```python
    processed, max_ts = collect(temporary_db, session, since_ts=0, until_ts=10_000, update_watermark=True)
```

```python
    collect(temporary_db, session, since_ts=0, until_ts=10_000, update_watermark=False)
```

```python
        collect(temporary_db, session, since_ts=0, until_ts=10_000, update_watermark=True)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_collector.py -q`
Expected: the three new tests fail (`AttributeError: ... 'ALGOLIA_MAX_PAGES'`), and the three updated `collect` tests fail (`TypeError: collect() got an unexpected keyword argument 'until_ts'`).

- [ ] **Step 3: Rewrite `search_keyword` and thread `until_ts` through `collect`**

In `collector.py`, extend the config import block with the two new names:

```python
from config import (
    ALGOLIA_MAX_RESULTS,
    ALGOLIA_URL,
    BACKFILL_SLICE_DAYS,
    COLLECTION_QUERY_VERSION,
    DB_PATH,
    HITS_PER_PAGE,
    KEYWORDS,
    LOOKBACK_DAYS,
    OVERLAP_SECONDS,
    REQUEST_PAUSE_SECONDS,
)
```

Replace the whole `search_keyword` function with:

```python
# nbPages가 이 값에 닿으면 Algolia가 결과를 잘라낸 것으로 간주한다.
ALGOLIA_MAX_PAGES = ALGOLIA_MAX_RESULTS // HITS_PER_PAGE


def search_keyword(
    session: requests.Session, keyword: str, start_ts: int, end_ts: int
) -> list[dict]:
    """반열린 구간 [start_ts, end_ts)의 story를 모두 가져온다.

    Algolia는 쿼리당 페이지네이션 결과를 ALGOLIA_MAX_RESULTS건으로 제한하므로,
    한 구간이 한계에 닿으면 구간을 반으로 재귀 분할해 완전 수집을 보장한다.
    1초 구간에서도 한계에 닿으면 RuntimeError — 조용한 유실 대신 실패를 택한다.
    """
    hits, page, n_pages = [], 0, 1
    while page < n_pages:
        params = {
            "query": keyword,
            "tags": "story",
            "numericFilters": f"created_at_i>={start_ts},created_at_i<{end_ts}",
            "hitsPerPage": HITS_PER_PAGE,
            "page": page,
        }
        resp = session.get(ALGOLIA_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        n_pages = data.get("nbPages", 1)
        if n_pages >= ALGOLIA_MAX_PAGES:
            if end_ts - start_ts <= 1:
                raise RuntimeError(
                    f"'{keyword}' 검색이 1초 구간 [{start_ts}, {end_ts})에서도 "
                    "Algolia 결과 한계에 도달해 완전 수집이 불가능하다"
                )
            mid = (start_ts + end_ts) // 2
            return search_keyword(session, keyword, start_ts, mid) + search_keyword(
                session, keyword, mid, end_ts
            )
        hits.extend(data.get("hits", []))
        page += 1
        time.sleep(REQUEST_PAUSE_SECONDS)
    return hits
```

In `collect`, change the signature and the `search_keyword` call:

```python
def collect(
    conn: sqlite3.Connection,
    session: requests.Session,
    since_ts: int,
    until_ts: int,
    update_watermark: bool = True,
) -> tuple[int, int]:
```

```python
    for keyword in KEYWORDS:
        hits_by_keyword[keyword] = search_keyword(session, keyword, since_ts, until_ts)
```

In `main`, compute `until_ts` once before the branch and pass it to both paths (the backfill branch is fully rewired in Task 3; for now just keep it compiling):

```python
    now = datetime.now(timezone.utc)
    until_ts = int(now.timestamp()) + 1

    if args.backfill is not None:
        since_ts = int((now - timedelta(days=args.backfill)).timestamp())
        print(f"[backfill] 최근 {args.backfill}일 재수집 (since {since_ts})")
        n_rows, max_ts = collect(conn, session, since_ts, until_ts, update_watermark=False)
    else:
        watermark = get_watermark(conn)
        if watermark is None:
            since_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
        else:
            since_ts = effective_since(int(watermark))
        print(f"[증분] watermark 이후 수집 (since {since_ts})")
        n_rows, max_ts = collect(conn, session, since_ts, until_ts, update_watermark=True)
```

- [ ] **Step 4: Run the full suite and verify it passes**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Normalize line endings and commit**

```bash
sed -i 's/\r$//' collector.py tests/test_collector.py
git diff --stat   # only the two files, tens of lines
git add collector.py tests/test_collector.py
git diff --cached --stat
git commit -m "feat: search half-open intervals and split capped slices"
```

---

### Task 3: All-or-nothing `collect_backfill` and CLI wiring

**Files:**
- Modify: `collector.py` (new `collect_backfill` after `collect`; `main` backfill branch)
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `backfill_slices` (Task 1), `search_keyword` (Task 2), existing `merge_hits` / `upsert_stories`.
- Produces: `collector.collect_backfill(conn, session, since_ts: int, until_ts: int) -> int` — walks every keyword × slice, prints one per-keyword count line, upserts once after all requests succeed, never touches the watermark, returns the number of upserted rows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_collector.py`:

```python
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
```

`collector.KEYWORDS` is already imported at collector module level; the test refers to it through the module (`collector.KEYWORDS`), no test-side import change needed.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/test_collector.py -k collect_backfill -q`
Expected: FAIL — `AttributeError: module 'collector' has no attribute 'collect_backfill'`.

- [ ] **Step 3: Implement `collect_backfill` and rewire `main`**

In `collector.py`, add after `collect`:

```python
def collect_backfill(
    conn: sqlite3.Connection,
    session: requests.Session,
    since_ts: int,
    until_ts: int,
) -> int:
    """모든 검색어×구간 요청이 성공한 뒤에만 한 번에 upsert한다.

    watermark는 절대 건드리지 않는다. 요청 하나라도 실패하면 예외가 전파되어
    부분 수집 결과가 DB에 남지 않는다(upsert가 idempotent하므로 재실행이 안전하다).
    """
    slices = backfill_slices(since_ts, until_ts, BACKFILL_SLICE_DAYS * 86_400)
    hits_by_keyword: dict[str, list[dict]] = {}
    for keyword in KEYWORDS:
        keyword_hits: list[dict] = []
        for start_ts, end_ts in slices:
            keyword_hits.extend(search_keyword(session, keyword, start_ts, end_ts))
        hits_by_keyword[keyword] = keyword_hits
        print(f"  · '{keyword}' 검색 완료 ({len(keyword_hits)}건)")

    rows = merge_hits(hits_by_keyword)
    upsert_stories(conn, rows)
    return len(rows)
```

In `main`, replace the backfill branch body (from Task 2 it currently calls `collect(..., update_watermark=False)`):

```python
    if args.backfill is not None:
        since_ts = int((now - timedelta(days=args.backfill)).timestamp())
        print(f"[backfill] 최근 {args.backfill}일 재수집 (since {since_ts})")
        n_rows = collect_backfill(conn, session, since_ts, until_ts)
```

The incremental branch keeps `n_rows, max_ts = collect(...)`; the final `print` uses only `n_rows` and the DB total, so both branches feed it correctly.

- [ ] **Step 4: Run the full suite and verify it passes**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Normalize line endings and commit**

```bash
sed -i 's/\r$//' collector.py tests/test_collector.py
git diff --stat
git add collector.py tests/test_collector.py
git diff --cached --stat
git commit -m "feat: collect backfills all-or-nothing across time slices"
```
