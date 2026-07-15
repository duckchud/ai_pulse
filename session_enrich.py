"""Persist externally produced session extraction results without calling an API."""

import argparse
import json
import sqlite3
from pathlib import Path

from config import DB_PATH, PROMPT_VERSION, SESSION_BATCH_LIMIT, SESSION_EXTRACTION_MODEL
from db import connect, migrate, save_extraction
from enrich import (
    build_record,
    normalize_story_text,
    parse_envelope,
    pending_story_ids,
    verify_evidence,
)


def pending_stories(conn: sqlite3.Connection, limit: int) -> list[dict[str, object]]:
    """Return normalized inputs for stories without a session extraction record."""
    story_ids = pending_story_ids(
        conn, PROMPT_VERSION, SESSION_EXTRACTION_MODEL, retry_failed=False
    )
    rows = []
    for story_id in story_ids[:limit]:
        story = conn.execute(
            "SELECT title, text FROM stories WHERE id = ?", (story_id,)
        ).fetchone()
        stable_input, _ = normalize_story_text(story["title"], story["text"])
        rows.append({"story_id": story_id, "input": stable_input})
    return rows


def save_session_result(conn: sqlite3.Connection, story_id: str, raw_response: str) -> str:
    """Validate and persist an externally generated response for one story."""
    story = conn.execute(
        "SELECT title, text FROM stories WHERE id = ?", (story_id,)
    ).fetchone()
    if story is None:
        raise ValueError("unknown story_id: " + story_id)

    stable_input, norm_text = normalize_story_text(story["title"], story["text"])
    try:
        verified = verify_evidence(parse_envelope(raw_response), stable_input)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        record = build_record(
            story_id,
            stable_input,
            norm_text,
            "invalid_json",
            raw_response,
            None,
            str(exc),
            model=SESSION_EXTRACTION_MODEL,
        )
        save_extraction(conn, record)
        return "invalid_json"

    record = build_record(
        story_id,
        stable_input,
        norm_text,
        "succeeded",
        raw_response,
        json.dumps(verified, ensure_ascii=False),
        None,
        model=SESSION_EXTRACTION_MODEL,
    )
    save_extraction(conn, record)
    return "succeeded"


def main() -> None:
    parser = argparse.ArgumentParser(description="offline session extraction persistence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pending_parser = subparsers.add_parser("pending", help="print pending story inputs")
    pending_parser.add_argument("--limit", type=int, default=SESSION_BATCH_LIMIT)

    save_parser = subparsers.add_parser("save", help="validate and save one result")
    save_parser.add_argument("--story-id", required=True)
    save_parser.add_argument("--raw-file", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "pending" and args.limit < 1:
        parser.error("--limit must be at least 1")

    conn = connect(DB_PATH)
    try:
        migrate(conn)
        if args.command == "pending":
            print(json.dumps(pending_stories(conn, args.limit), ensure_ascii=False))
        else:
            raw_response = args.raw_file.read_text(encoding="utf-8")
            print(save_session_result(conn, args.story_id, raw_response))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
