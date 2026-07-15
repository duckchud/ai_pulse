"""db.py — SQLite connection, schema migration, and extraction lifecycle API.

Bronze/Silver 계층이 공유하는 SQLite 접근 지점. 스키마 마이그레이션과
story_extractions(추출 이력) 저장/조회를 제공한다.
"""

import sqlite3
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id               TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    title            TEXT,
    url              TEXT,
    author           TEXT,
    points           INTEGER,
    num_comments     INTEGER,
    created_at       TEXT,
    created_at_i     INTEGER,
    text             TEXT,
    matched_keywords TEXT,
    fetched_at       TEXT NOT NULL,
    collection_query_version TEXT NOT NULL DEFAULT 'legacy'
);
CREATE INDEX IF NOT EXISTS idx_stories_created ON stories(created_at_i);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS story_extractions (
  story_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('succeeded','invalid_json','failed')),
  raw_response TEXT,
  parsed_json TEXT,
  input_hash TEXT NOT NULL,
  input_char_count INTEGER NOT NULL,
  input_truncated INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  enriched_at TEXT NOT NULL,
  PRIMARY KEY (story_id, prompt_version, model),
  FOREIGN KEY (story_id) REFERENCES stories(id)
);

CREATE TABLE IF NOT EXISTS model_catalog (
    model_id           TEXT PRIMARY KEY,
    vendor             TEXT NOT NULL,
    family             TEXT NOT NULL,
    version            TEXT,
    released_on        TEXT,
    release_source_url TEXT NOT NULL,
    catalog_version    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_aliases (
    alias_normalized TEXT PRIMARY KEY,
    model_id         TEXT NOT NULL,
    FOREIGN KEY (model_id) REFERENCES model_catalog(model_id)
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    model_id              TEXT NOT NULL,
    benchmark             TEXT NOT NULL,
    metric                TEXT NOT NULL,
    score                 REAL NOT NULL,
    evaluation_conditions TEXT NOT NULL,
    measured_at           TEXT,
    source_url            TEXT NOT NULL,
    PRIMARY KEY (model_id, benchmark, metric),
    FOREIGN KEY (model_id) REFERENCES model_catalog(model_id)
);
"""

WATERMARK_KEY = "watermark"


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(stories)")}
    if "collection_query_version" not in columns:
        conn.execute(
            "ALTER TABLE stories ADD COLUMN collection_query_version "
            "TEXT NOT NULL DEFAULT 'legacy'"
        )
    conn.commit()


def get_watermark(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (WATERMARK_KEY,)
    ).fetchone()
    return row["value"] if row else None


def set_watermark(conn: sqlite3.Connection, timestamp: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (WATERMARK_KEY, timestamp),
    )
    conn.commit()


def upsert_stories(conn: sqlite3.Connection, rows) -> None:
    conn.executemany(
        """
        INSERT INTO stories (
            id, source, title, url, author, points, num_comments,
            created_at, created_at_i, text, matched_keywords, fetched_at,
            collection_query_version
        ) VALUES (
            :id, :source, :title, :url, :author, :points, :num_comments,
            :created_at, :created_at_i, :text, :matched_keywords, :fetched_at,
            :collection_query_version
        )
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            author = excluded.author,
            points = excluded.points,
            num_comments = excluded.num_comments,
            created_at = excluded.created_at,
            created_at_i = excluded.created_at_i,
            text = excluded.text,
            matched_keywords = excluded.matched_keywords,
            fetched_at = excluded.fetched_at,
            collection_query_version = excluded.collection_query_version
        """,
        rows,
    )
    conn.commit()


def save_extraction(conn: sqlite3.Connection, record: dict) -> None:
    if record["status"] == "succeeded" and record.get("parsed_json") is None:
        raise ValueError("succeeded extraction requires parsed_json")

    conn.execute(
        """
        INSERT INTO story_extractions (
            story_id, prompt_version, model, status, raw_response, parsed_json,
            input_hash, input_char_count, input_truncated, error_message, enriched_at
        ) VALUES (
            :story_id, :prompt_version, :model, :status, :raw_response, :parsed_json,
            :input_hash, :input_char_count, :input_truncated, :error_message, :enriched_at
        )
        ON CONFLICT(story_id, prompt_version, model) DO UPDATE SET
            status = excluded.status,
            raw_response = excluded.raw_response,
            parsed_json = excluded.parsed_json,
            input_hash = excluded.input_hash,
            input_char_count = excluded.input_char_count,
            input_truncated = excluded.input_truncated,
            error_message = excluded.error_message,
            enriched_at = excluded.enriched_at
        """,
        record,
    )
    conn.commit()


def latest_successful_extractions(conn: sqlite3.Connection) -> pd.DataFrame:
    # API 추출을 세션 추출보다 우선하고, 같은 우선순위 안에서는 최신 enriched_at,
    # 동률이면 rowid DESC(가장 나중에 쓰인 레코드)로 결정한다.
    # 첫 정렬 뒤 drop_duplicates(keep="first")로 story별 승자를 정한 다음,
    # 기존처럼 선택된 결과 전체는 최신순으로 다시 정렬한다.
    df = pd.read_sql_query(
        "SELECT rowid AS extraction_rowid, * FROM story_extractions "
        "WHERE status = 'succeeded' "
        "ORDER BY CASE WHEN model = 'codex-session-v1' THEN 1 ELSE 0 END, "
        "enriched_at DESC, rowid DESC",
        conn,
    )
    if df.empty:
        return df
    winners = df.drop_duplicates(subset="story_id", keep="first")
    return (
        winners.sort_values(
            ["enriched_at", "extraction_rowid"], ascending=[False, False]
        )
        .drop(columns="extraction_rowid")
        .reset_index(drop=True)
    )
