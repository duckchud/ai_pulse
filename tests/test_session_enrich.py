import json

import session_enrich


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
