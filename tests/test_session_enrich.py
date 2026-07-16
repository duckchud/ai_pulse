import json
import sqlite3
import sys

import pytest

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


def test_pending_stories_rejects_limit_below_one(temporary_db):
    with pytest.raises(ValueError, match="limit must be at least 1"):
        session_enrich.pending_stories(temporary_db, limit=0)


def _seed_candidate_fixture(conn, story_ids: list[str]) -> None:
    conn.execute(
        "INSERT INTO model_catalog (model_id, vendor, family, release_source_url, catalog_version) "
        "VALUES ('openai-gpt', 'OpenAI', 'GPT', 'https://example.test/gpt', 'v1')"
    )
    for story_id in story_ids:
        conn.execute(
            "INSERT INTO story_candidates (story_id, catalog_version, candidate_reason, "
            "matched_model_ids, evidence_json, selected_at) "
            "VALUES (?, 'v1', 'catalog_alias_match', ?, ?, '2026-07-16T00:00:00Z')",
            (story_id, json.dumps(["openai-gpt"]), json.dumps([])),
        )
    conn.commit()


def test_pending_stories_from_candidates_excludes_non_candidate_stories(temporary_db):
    for story_id in ("1", "2", "3"):
        _insert_story(temporary_db, story_id, f"GPT story {story_id}", "body")
    _seed_candidate_fixture(temporary_db, ["1", "3"])

    rows = session_enrich.pending_stories(
        temporary_db, limit=10, from_candidates=True, seed=20260716
    )

    assert sorted(row["story_id"] for row in rows) == ["1", "3"]


def test_pending_stories_seeded_order_is_reproducible_and_accumulates(temporary_db):
    story_ids = [str(n) for n in range(1, 9)]
    for story_id in story_ids:
        _insert_story(temporary_db, story_id, f"GPT story {story_id}", "body")
    _seed_candidate_fixture(temporary_db, story_ids)

    first = session_enrich.pending_stories(
        temporary_db, limit=3, from_candidates=True, seed=20260716
    )
    again = session_enrich.pending_stories(
        temporary_db, limit=3, from_candidates=True, seed=20260716
    )
    # 같은 시드 + 같은 상태 => 같은 결과(결정적).
    assert [row["story_id"] for row in again] == [row["story_id"] for row in first]

    # 앞 배치를 저장하면 다음 배치는 그 뒤를 이어받되 순서는 유지된다.
    raw = json.dumps({"relevant": False, "observations": [], "extensions": {}})
    for row in first:
        session_enrich.save_session_result(temporary_db, row["story_id"], raw)
    following = session_enrich.pending_stories(
        temporary_db, limit=3, from_candidates=True, seed=20260716
    )

    assert not set(r["story_id"] for r in following) & set(r["story_id"] for r in first)
    expected = session_enrich.pending_stories(
        temporary_db, limit=8, from_candidates=True, seed=20260716
    )
    assert [row["story_id"] for row in following] == [
        row["story_id"] for row in expected[:3]
    ]


def test_pending_stories_different_seeds_give_different_order(temporary_db):
    story_ids = [str(n) for n in range(1, 21)]
    for story_id in story_ids:
        _insert_story(temporary_db, story_id, f"GPT story {story_id}", "body")
    _seed_candidate_fixture(temporary_db, story_ids)

    a = session_enrich.pending_stories(temporary_db, limit=20, from_candidates=True, seed=1)
    b = session_enrich.pending_stories(temporary_db, limit=20, from_candidates=True, seed=2)

    assert [r["story_id"] for r in a] != [r["story_id"] for r in b]


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
        "WHERE story_id = '1' AND model = 'session-v1'"
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
    assert row["model"] == "session-v1"
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


def test_pending_stories_excludes_already_extracted_story(temporary_db):
    # 이미 세션 추출 행이 있는 story는 다시 pending으로 잡히지 않아야 한다(중복 추출 방지).
    _insert_story(temporary_db, "1", "Qwen3 release", "body")
    _insert_story(temporary_db, "2", "GPT-5", "second")
    raw = json.dumps({"relevant": False, "observations": [], "extensions": {}})
    session_enrich.save_session_result(temporary_db, "2", raw)

    rows = session_enrich.pending_stories(temporary_db, limit=5)

    assert [row["story_id"] for row in rows] == ["1"]
