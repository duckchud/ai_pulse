import json
import sqlite3
import sys

import pytest

import session_enrich
from db import save_extraction


def _insert_story(conn, story_id: str, title: str, text: str) -> None:
    conn.execute(
        "INSERT INTO stories (id, source, title, url, author, points, num_comments, "
        "created_at, created_at_i, text, matched_keywords, fetched_at) "
        "VALUES (?, 'hackernews', ?, NULL, 'author', 1, 0, "
        "'2026-07-14T00:00:00Z', ?, ?, 'LLM', '2026-07-14T00:00:00Z')",
        (story_id, title, int(story_id), text),
    )
    conn.commit()


def test_pending_stories_returns_normalized_bounded_inputs(temporary_db):
    _insert_story(temporary_db, "1", "<b>Qwen3</b> release", "<p>Body</p>")
    _insert_story(temporary_db, "2", "GPT-5", "second")

    rows = session_enrich.pending_stories(temporary_db, limit=1)

    assert rows == [{"story_id": "2", "input": {"title": "GPT-5", "text": "second"}}]


def test_pending_stories_excludes_story_with_successful_api_extraction(temporary_db):
    _insert_story(temporary_db, "1", "Qwen3 release", "body")
    save_extraction(
        temporary_db,
        {
            "story_id": "1",
            "prompt_version": session_enrich.PROMPT_VERSION,
            "model": "claude-test",
            "status": "succeeded",
            "raw_response": "{}",
            "parsed_json": "{}",
            "input_hash": "input",
            "input_char_count": 1,
            "input_truncated": 0,
            "error_message": None,
            "enriched_at": "2026-07-14T00:00:00Z",
        },
    )

    assert session_enrich.pending_stories(temporary_db, limit=1) == []


def test_pending_stories_rejects_limit_below_one(temporary_db):
    with pytest.raises(ValueError, match="limit must be at least 1"):
        session_enrich.pending_stories(temporary_db, limit=0)


def test_save_cli_records_failed_session_result_after_operational_error(
    monkeypatch, temporary_db, tmp_path, capsys
):
    _insert_story(temporary_db, "1", "Qwen3 release", "body")
    raw_file = tmp_path / "response.json"
    raw_file.write_text('{"available": "raw response"}', encoding="utf-8")
    db_path = temporary_db.execute("PRAGMA database_list").fetchone()[2]

    def raise_persistence_error(*args, **kwargs):
        raise sqlite3.OperationalError("database write failed")

    monkeypatch.setattr(session_enrich, "DB_PATH", db_path)
    monkeypatch.setattr(session_enrich, "save_session_result", raise_persistence_error)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "session_enrich.py",
            "save",
            "--story-id",
            "1",
            "--raw-file",
            str(raw_file),
        ],
    )

    session_enrich.main()

    row = temporary_db.execute(
        "SELECT status, error_message, raw_response FROM story_extractions "
        "WHERE story_id = '1' AND model = 'codex-session-v1'"
    ).fetchone()
    assert capsys.readouterr().out.strip() == "failed"
    assert row["status"] == "failed"
    assert row["error_message"] == "database write failed"
    assert row["raw_response"] == '{"available": "raw response"}'


def test_save_session_result_verifies_and_persists_success(temporary_db):
    _insert_story(temporary_db, "1", "Qwen3 release", "body")
    raw = json.dumps(
        {
            "relevant": True,
            "observations": [
                {
                    "surface": "Qwen3",
                    "evidence": {"field": "title", "quote": "Qwen3 release"},
                }
            ],
            "extensions": {},
        }
    )

    assert session_enrich.save_session_result(temporary_db, "1", raw) == "succeeded"
    row = temporary_db.execute(
        "SELECT model, status, raw_response, parsed_json FROM story_extractions"
    ).fetchone()
    assert row["model"] == "codex-session-v1"
    assert row["status"] == "succeeded"
    assert row["raw_response"] == raw
    assert json.loads(row["parsed_json"])["observations"][0]["evidence_verified"] is True


def test_save_session_result_preserves_open_world_fields_with_unverified_evidence(temporary_db):
    _insert_story(temporary_db, "1", "Qwen3 release", "body")
    raw = json.dumps(
        {
            "relevant": True,
            "observations": [
                {
                    "surface": "Qwen3",
                    "evidence": {"field": "title", "quote": "not in the story"},
                    "attributes": {"novel_attribute": "kept"},
                    "unknown_observation_key": {"also": "kept"},
                }
            ],
            "extensions": {"unknown_extension": {"source": "session"}},
            "unknown_top_level_key": ["kept"],
        }
    )

    assert session_enrich.save_session_result(temporary_db, "1", raw) == "succeeded"

    parsed = json.loads(
        temporary_db.execute(
            "SELECT parsed_json FROM story_extractions WHERE story_id = '1'"
        ).fetchone()["parsed_json"]
    )
    observation = parsed["observations"][0]
    assert observation["evidence_verified"] is False
    assert observation["attributes"]["novel_attribute"] == "kept"
    assert observation["unknown_observation_key"] == {"also": "kept"}
    assert parsed["extensions"] == {"unknown_extension": {"source": "session"}}
    assert parsed["unknown_top_level_key"] == ["kept"]


def test_save_session_result_marks_malformed_json_invalid(temporary_db):
    _insert_story(temporary_db, "1", "Qwen3 release", "body")

    assert session_enrich.save_session_result(temporary_db, "1", "not json") == "invalid_json"
    row = temporary_db.execute(
        "SELECT status, parsed_json FROM story_extractions"
    ).fetchone()
    assert row["status"] == "invalid_json"
    assert row["parsed_json"] is None


def test_session_adapter_does_not_require_anthropic_api_key(monkeypatch, temporary_db):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _insert_story(temporary_db, "1", "Qwen3 release", "body")

    assert session_enrich.pending_stories(temporary_db, limit=5)[0]["story_id"] == "1"
