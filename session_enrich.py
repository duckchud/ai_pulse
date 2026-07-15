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
    verify_evidence,
)


def pending_stories(conn: sqlite3.Connection, limit: int) -> list[dict[str, object]]:
    """Return normalized inputs without a session row or successful API row."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    story_ids = [
        row["id"]
        for row in conn.execute(
            """
            SELECT s.id FROM stories s
            LEFT JOIN story_extractions session_extraction
              ON session_extraction.story_id = s.id
             AND session_extraction.prompt_version = ?
             AND session_extraction.model = ?
            WHERE session_extraction.story_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM story_extractions api_extraction
                  WHERE api_extraction.story_id = s.id
                    AND api_extraction.status = 'succeeded'
                    AND api_extraction.model != ?
              )
            ORDER BY s.created_at_i DESC
            """,
            (PROMPT_VERSION, SESSION_EXTRACTION_MODEL, SESSION_EXTRACTION_MODEL),
        ).fetchall()
    ]
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


def save_failed_session_result(
    conn: sqlite3.Connection,
    story_id: str,
    raw_response: str | None,
    error_message: str,
) -> None:
    """Persist a known story's operational failure with available session output."""
    story = conn.execute(
        "SELECT title, text FROM stories WHERE id = ?", (story_id,)
    ).fetchone()
    if story is None:
        raise ValueError("unknown story_id: " + story_id)

    stable_input, norm_text = normalize_story_text(story["title"], story["text"])
    save_extraction(
        conn,
        build_record(
            story_id,
            stable_input,
            norm_text,
            "failed",
            raw_response,
            None,
            error_message,
            model=SESSION_EXTRACTION_MODEL,
        ),
    )


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
            raw_response = None
            try:
                raw_response = args.raw_file.read_text(encoding="utf-8")
                status = save_session_result(conn, args.story_id, raw_response)
            except Exception as exc:
                status = "failed"
                try:
                    save_failed_session_result(conn, args.story_id, raw_response, str(exc))
                except Exception as persistence_exc:
                    print(
                        f"failed: {exc} "
                        f"(failure record not saved: {persistence_exc})"
                    )
                    return
            print(status)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
