"""db.py — SQLite connection, schema migration, and extraction lifecycle API.

Bronze/Silver 계층이 공유하는 SQLite 접근 지점. 스키마 마이그레이션과
story_extractions(추출 이력) 저장/조회를 제공한다.
"""

import json
import sqlite3
from pathlib import Path

import pandas as pd

from reference_data import normalize_alias

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

CREATE TABLE IF NOT EXISTS story_candidates (
    story_id TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    candidate_reason TEXT NOT NULL,
    matched_model_ids TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    PRIMARY KEY (story_id, catalog_version),
    FOREIGN KEY (story_id) REFERENCES stories(id)
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


def catalog_version(conn: sqlite3.Connection) -> str:
    versions = [
        row["catalog_version"]
        for row in conn.execute(
            "SELECT DISTINCT catalog_version FROM model_catalog ORDER BY catalog_version"
        )
    ]
    if len(versions) != 1:
        raise ValueError("candidate selection requires exactly one catalog_version")
    return versions[0]


def upsert_story_candidates(
    conn: sqlite3.Connection, candidates: list[dict[str, str]]
) -> None:
    if not candidates:
        return

    _validate_story_candidates(conn, candidates)
    conn.execute("SAVEPOINT upsert_story_candidates")
    try:
        _write_story_candidates(conn, candidates)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT upsert_story_candidates")
        conn.execute("RELEASE SAVEPOINT upsert_story_candidates")
        raise
    conn.execute("RELEASE SAVEPOINT upsert_story_candidates")
    conn.commit()


def replace_story_candidates(
    conn: sqlite3.Connection,
    catalog_version_value: str,
    candidates: list[dict[str, str]],
) -> None:
    """현재 catalog version의 후보를 검증 뒤 한 트랜잭션으로 교체한다."""
    if any(row["catalog_version"] != catalog_version_value for row in candidates):
        raise ValueError("candidate catalog_version does not match replacement version")
    _validate_story_candidates(conn, candidates)
    with conn:
        conn.execute(
            "DELETE FROM story_candidates WHERE catalog_version = ?",
            (catalog_version_value,),
        )
        if candidates:
            _write_story_candidates(conn, candidates)


def _json_array(value: str, field_name: str) -> list:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} must be a JSON array") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return parsed


def _validate_story_candidates(
    conn: sqlite3.Connection, candidates: list[dict[str, str]]
) -> None:
    catalog_model_ids = {
        row["model_id"] for row in conn.execute("SELECT model_id FROM model_catalog")
    }
    stories = {
        row["id"]: row
        for row in conn.execute("SELECT id, title, text FROM stories")
    }
    aliases = {
        row["alias_normalized"]: row["model_id"]
        for row in conn.execute("SELECT alias_normalized, model_id FROM model_aliases")
    }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        for field_name in (
            "story_id",
            "catalog_version",
            "candidate_reason",
            "selected_at",
        ):
            if not isinstance(candidate.get(field_name), str) or not candidate[field_name]:
                raise ValueError(f"candidate {field_name} must be a non-empty string")
        story = stories.get(candidate["story_id"])
        if story is None:
            raise ValueError("candidate story_id must refer to an existing story")
        model_ids = _json_array(candidate["matched_model_ids"], "matched_model_ids")
        if not all(isinstance(model_id, str) and model_id for model_id in model_ids):
            raise ValueError("matched_model_ids must contain non-empty model IDs")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("matched_model_ids must contain unique model IDs")
        unknown_model_ids = set(model_ids) - catalog_model_ids
        if unknown_model_ids:
            raise ValueError("matched_model_ids must exist in the current catalog")

        evidence = _json_array(candidate["evidence_json"], "evidence_json")
        if not evidence:
            raise ValueError("evidence_json must contain at least one evidence entry")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("evidence_json entries must be objects")
            model_id = item.get("model_id")
            alias = item.get("alias")
            field = item.get("field")
            quote = item.get("quote")
            if not isinstance(model_id, str) or model_id not in model_ids:
                raise ValueError("evidence model_id must be in matched_model_ids")
            if not isinstance(alias, str) or not alias:
                raise ValueError("evidence alias must be a non-empty string")
            if aliases.get(normalize_alias(alias)) != model_id:
                raise ValueError("evidence alias must resolve to its model_id")
            if field not in ("title", "text"):
                raise ValueError("evidence field must be title or text")
            if not isinstance(quote, str) or not quote:
                raise ValueError("evidence quote must be a non-empty string")
            if quote not in (story[field] or ""):
                raise ValueError("evidence quote must be a substring of the story field")


def _write_story_candidates(
    conn: sqlite3.Connection, candidates: list[dict[str, str]]
) -> None:
    conn.executemany(
        """
        INSERT INTO story_candidates (
            story_id, catalog_version, candidate_reason, matched_model_ids,
            evidence_json, selected_at
        ) VALUES (
            :story_id, :catalog_version, :candidate_reason, :matched_model_ids,
            :evidence_json, :selected_at
        )
        ON CONFLICT(story_id, catalog_version) DO UPDATE SET
            candidate_reason = excluded.candidate_reason,
            matched_model_ids = excluded.matched_model_ids,
            evidence_json = excluded.evidence_json,
            selected_at = excluded.selected_at
        """,
        candidates,
    )


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
    # story별로 최신 enriched_at, 동률이면 rowid DESC(가장 나중에 쓰인 레코드)를
    # 고른다. 첫 정렬 뒤 drop_duplicates(keep="first")로 story별 승자를 정한 다음,
    # 기존처럼 선택된 결과 전체는 최신순으로 다시 정렬한다.
    df = pd.read_sql_query(
        "SELECT rowid AS extraction_rowid, * FROM story_extractions "
        "WHERE status = 'succeeded' "
        "ORDER BY enriched_at DESC, rowid DESC",
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
