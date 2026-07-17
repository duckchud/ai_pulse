"""
collector.py — HackerNews AI 담론 실시간 수집기 (Bronze)

Algolia HN Search API 기반 (인증 불필요, 넉넉한 rate limit).
AI 관련 키워드에 매칭되는 story를 SQLite에 증분 적재한다.
- 중복 제거: objectID(story id)를 PK로 사용
- 증분 수집: watermark(지난 실행의 최대 created_at_i)에서 OVERLAP_SECONDS만큼
  되감아 검색해, 경계에 걸친 story를 놓치지 않는다.
- watermark는 모든 키워드 요청과 DB 반영이 전부 성공한 뒤에만 전진한다.

사용:
    pip install requests
    python collector.py                 # 증분 수집 (첫 실행은 LOOKBACK_DAYS 만큼)
    python collector.py --backfill 7    # 최근 7일 강제 재수집 (watermark 변경 없음)
"""

import argparse
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
from db import connect, get_watermark, migrate, set_watermark, upsert_stories


# ── HTTP 세션 (재시도 포함) ──────────────────────────────────────
def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "ai-monitor-portfolio/1.0"})
    return session


# ── 오버랩 윈도우 ────────────────────────────────────────────────
def effective_since(watermark: int) -> int:
    """watermark를 OVERLAP_SECONDS만큼 되감아, 경계에 걸친 story 유실을 막는다."""
    return max(0, watermark - OVERLAP_SECONDS)


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


# ── 수집 ─────────────────────────────────────────────────────────
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


def merge_hits(hits_by_keyword: dict[str, list[dict]]) -> list[dict]:
    """objectID 기준으로 병합해 매칭 키워드를 합치고 upsert_stories용 dict로 변환."""
    merged: dict[str, dict] = {}
    for keyword, hits in hits_by_keyword.items():
        for hit in hits:
            object_id = hit.get("objectID")
            if not object_id:
                continue
            if object_id not in merged:
                merged[object_id] = {"hit": hit, "keywords": set()}
            merged[object_id]["keywords"].add(keyword)

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for object_id, bundle in merged.items():
        hit = bundle["hit"]
        rows.append(
            {
                "id": object_id,
                "source": "hackernews",
                "title": hit.get("title"),
                "url": hit.get("url"),
                "author": hit.get("author"),
                "points": hit.get("points"),
                "num_comments": hit.get("num_comments"),
                "created_at": hit.get("created_at"),
                "created_at_i": hit.get("created_at_i"),
                "text": hit.get("story_text"),
                "matched_keywords": ",".join(sorted(bundle["keywords"])),
                "fetched_at": now_iso,
                "collection_query_version": COLLECTION_QUERY_VERSION,
            }
        )
    return rows


def collect(
    conn: sqlite3.Connection,
    session: requests.Session,
    since_ts: int,
    until_ts: int,
    update_watermark: bool = True,
) -> tuple[int, int]:
    """키워드별로 수집 → merge_hits로 병합 → upsert.

    모든 키워드 HTTP 요청이 성공한 뒤에만 DB에 반영하고, DB 반영까지 성공한
    경우에만(update_watermark=True일 때) watermark를 전진시킨다.
    """
    hits_by_keyword: dict[str, list[dict]] = {}
    for keyword in KEYWORDS:
        hits_by_keyword[keyword] = search_keyword(session, keyword, since_ts, until_ts)
        print(f"  · '{keyword}' 검색 완료 ({len(hits_by_keyword[keyword])}건)")

    rows = merge_hits(hits_by_keyword)
    max_ts = since_ts
    for row in rows:
        max_ts = max(max_ts, row.get("created_at_i") or 0)

    upsert_stories(conn, rows)
    if update_watermark:
        set_watermark(conn, str(max_ts))

    return len(rows), max_ts


# ── main ─────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="HackerNews AI 담론 수집기")
    parser.add_argument(
        "--backfill",
        type=int,
        metavar="DAYS",
        help="watermark 무시하고 최근 N일을 강제 재수집 (watermark는 변경하지 않음)",
    )
    args = parser.parse_args()

    conn = connect(DB_PATH)
    migrate(conn)
    session = make_session()

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

    total = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    print(f"\n완료: 이번 실행 {n_rows}건 처리 / DB 총 {total}건")
    conn.close()


if __name__ == "__main__":
    main()
