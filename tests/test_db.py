import json
import sqlite3

import pytest

from db import (
    catalog_version,
    latest_successful_extractions,
    replace_story_candidates,
    save_extraction,
    upsert_story_candidates,
)


def test_migrate_creates_schema_free_extractions_table(temporary_db):
    columns = {row[1] for row in temporary_db.execute("PRAGMA table_info(story_extractions)")}
    assert {"story_id", "prompt_version", "model", "status", "parsed_json", "input_hash"} <= columns


def test_latest_successful_extraction_prefers_newest_success(temporary_db):
    temporary_db.execute(
        "INSERT INTO stories (id, source, title, url, author, points, num_comments, "
        "created_at, created_at_i, text, matched_keywords, fetched_at) "
        "VALUES ('1','hackernews','T',NULL,'a',1,0,'2026-07-14T00:00:00Z',1,NULL,'LLM','x')"
    )
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v1", "model": "m", "status": "failed", "raw_response": "", "parsed_json": None, "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": "timeout", "enriched_at": "2026-07-14T00:00:00Z"})
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v2", "model": "m", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T01:00:00Z"})
    assert latest_successful_extractions(temporary_db).iloc[0]["prompt_version"] == "v2"


def test_latest_successful_extraction_prefers_api_success_over_newer_session_success(temporary_db):
    temporary_db.execute(
        "INSERT INTO stories (id, source, title, url, author, points, num_comments, "
        "created_at, created_at_i, text, matched_keywords, fetched_at) "
        "VALUES ('1','hackernews','T',NULL,'a',1,0,'2026-07-14T00:00:00Z',1,NULL,'LLM','x')"
    )
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v1", "model": "claude-test", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T00:00:00Z"})
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v1", "model": "codex-session-v1", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T01:00:00Z"})

    result = latest_successful_extractions(temporary_db)

    assert len(result) == 1
    assert result.iloc[0]["model"] == "claude-test"


def test_latest_successful_extractions_keeps_selected_rows_newest_first(temporary_db):
    for story_id in ("1", "2"):
        temporary_db.execute(
            "INSERT INTO stories (id, source, title, url, author, points, num_comments, "
            "created_at, created_at_i, text, matched_keywords, fetched_at) "
            "VALUES (?, 'hackernews', 'T', NULL, 'a', 1, 0, "
            "'2026-07-14T00:00:00Z', ?, NULL, 'LLM', 'x')",
            (story_id, int(story_id)),
        )
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v1", "model": "codex-session-v1", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T02:00:00Z"})
    save_extraction(temporary_db, {"story_id": "2", "prompt_version": "v1", "model": "claude-test", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T01:00:00Z"})

    result = latest_successful_extractions(temporary_db)

    assert result["story_id"].tolist() == ["1", "2"]


def test_latest_successful_extraction_tie_break_is_deterministic(temporary_db):
    temporary_db.execute(
        "INSERT INTO stories (id, source, title, url, author, points, num_comments, "
        "created_at, created_at_i, text, matched_keywords, fetched_at) "
        "VALUES ('1','hackernews','T',NULL,'a',1,0,'2026-07-14T00:00:00Z',1,NULL,'LLM','x')"
    )
    same_time = "2026-07-14T00:00:00Z"
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v1", "model": "m", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": same_time})
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v2", "model": "m", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": same_time})
    # enriched_at이 동일하면 가장 나중에 쓰인 레코드(rowid DESC = v2)가 결정적으로 선택된다.
    result = latest_successful_extractions(temporary_db)
    assert len(result) == 1
    assert result.iloc[0]["prompt_version"] == "v2"


def test_candidates_upsert_per_catalog_version(temporary_db):
    temporary_db.execute(
        "INSERT INTO stories (id, source, title, fetched_at) "
        "VALUES ('story-1', 'hackernews', 'GPT announcement', '2026-07-16T00:00:00Z')"
    )
    temporary_db.execute(
        "INSERT INTO model_catalog (model_id, vendor, family, release_source_url, catalog_version) "
        "VALUES ('openai:gpt', 'OpenAI', 'GPT', 'https://example.test/gpt', 'v1')"
    )
    temporary_db.execute(
        "INSERT INTO model_aliases (alias_normalized, model_id) VALUES ('gpt', 'openai:gpt')"
    )
    row = {
        "story_id": "story-1",
        "catalog_version": "v1",
        "candidate_reason": "catalog_alias_match",
        "matched_model_ids": json.dumps(["openai:gpt"]),
        "evidence_json": json.dumps([
            {"model_id": "openai:gpt", "alias": "GPT", "field": "title", "quote": "GPT"}
        ]),
        "selected_at": "2026-07-16T00:00:00Z",
    }
    upsert_story_candidates(temporary_db, [row])
    upsert_story_candidates(
        temporary_db, [{**row, "selected_at": "2026-07-16T01:00:00Z"}]
    )
    assert temporary_db.execute(
        "SELECT COUNT(*) FROM story_candidates"
    ).fetchone()[0] == 1
    assert temporary_db.execute(
        "SELECT selected_at FROM story_candidates"
    ).fetchone()[0] == "2026-07-16T01:00:00Z"


def test_catalog_version_requires_exactly_one_value(temporary_db):
    with pytest.raises(ValueError, match="exactly one catalog_version"):
        catalog_version(temporary_db)


def _candidate_row(story_id="story-1", **overrides):
    row = {
        "story_id": story_id,
        "catalog_version": "v1",
        "candidate_reason": "catalog_alias_match",
        "matched_model_ids": json.dumps(["openai:gpt"]),
        "evidence_json": json.dumps([
            {"model_id": "openai:gpt", "alias": "GPT", "field": "title", "quote": "GPT"}
        ]),
        "selected_at": "2026-07-16T00:00:00Z",
    }
    return {**row, **overrides}


@pytest.fixture
def candidate_validation_db(temporary_db):
    temporary_db.executemany(
        "INSERT INTO stories (id, source, title, text, fetched_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("story-1", "hackernews", "GPT announcement", "Claude comparison", "2026-07-16T00:00:00Z"),
            ("story-2", "hackernews", "GPT follow-up", None, "2026-07-16T00:00:00Z"),
        ],
    )
    temporary_db.execute(
        "INSERT INTO model_catalog (model_id, vendor, family, release_source_url, catalog_version) "
        "VALUES ('openai:gpt', 'OpenAI', 'GPT', 'https://example.test/gpt', 'v1')"
    )
    temporary_db.execute(
        "INSERT INTO model_aliases (alias_normalized, model_id) VALUES ('gpt', 'openai:gpt')"
    )
    temporary_db.commit()
    return temporary_db


@pytest.mark.parametrize(
    "row, message",
    [
        (_candidate_row(matched_model_ids="not-json"), "matched_model_ids must be a JSON array"),
        (_candidate_row(evidence_json="not-json"), "evidence_json must be a JSON array"),
        (_candidate_row(evidence_json=json.dumps([])), "at least one evidence"),
        (_candidate_row(selected_at=None), "selected_at must be a non-empty string"),
        (_candidate_row(matched_model_ids=json.dumps(["openai:gpt", "openai:gpt"])), "unique"),
        (_candidate_row(matched_model_ids=json.dumps(["unknown:model"])), "current catalog"),
        (_candidate_row(evidence_json=json.dumps([
            {"model_id": "openai:gpt", "alias": "GPT", "field": "url", "quote": "GPT"}
        ])), "field"),
        (_candidate_row(evidence_json=json.dumps([
            {"model_id": "openai:gpt", "alias": "GPT", "field": "title", "quote": "missing"}
        ])), "substring"),
        (_candidate_row(evidence_json=json.dumps([
            {"model_id": "unknown:model", "alias": "Unknown", "field": "title", "quote": "GPT"}
        ])), "matched_model_ids"),
        (_candidate_row(evidence_json=json.dumps([
            {"model_id": "openai:gpt", "alias": "Fabricated", "field": "title", "quote": "GPT"}
        ])), "resolve to its model_id"),
    ],
)
def test_candidate_persistence_rejects_invalid_payloads(candidate_validation_db, row, message):
    with pytest.raises(ValueError, match=message):
        upsert_story_candidates(candidate_validation_db, [row])
    assert candidate_validation_db.execute("SELECT COUNT(*) FROM story_candidates").fetchone()[0] == 0


def test_candidate_persistence_rolls_back_an_entire_invalid_batch(candidate_validation_db):
    valid = _candidate_row()
    invalid = _candidate_row("story-2", matched_model_ids=json.dumps(["unknown:model"]))

    with pytest.raises(ValueError, match="current catalog"):
        upsert_story_candidates(candidate_validation_db, [valid, invalid])
    assert candidate_validation_db.execute("SELECT COUNT(*) FROM story_candidates").fetchone()[0] == 0


def test_candidate_persistence_rolls_back_on_database_error(candidate_validation_db):
    candidate_validation_db.execute(
        "CREATE TRIGGER reject_second_candidate BEFORE INSERT ON story_candidates "
        "WHEN NEW.selected_at = 'reject' BEGIN SELECT RAISE(ABORT, 'reject candidate'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="reject candidate"):
        upsert_story_candidates(candidate_validation_db, [
            _candidate_row(selected_at="2026-07-16T00:00:00Z"),
            _candidate_row("story-2", selected_at="reject"),
        ])

    assert candidate_validation_db.execute("SELECT COUNT(*) FROM story_candidates").fetchone()[0] == 0


def test_candidate_replacement_keeps_existing_rows_when_validation_fails(
    candidate_validation_db,
):
    existing = _candidate_row(selected_at="2026-07-16T00:00:00Z")
    upsert_story_candidates(candidate_validation_db, [existing])
    invalid = _candidate_row(matched_model_ids=json.dumps(["unknown:model"]))

    with pytest.raises(ValueError, match="current catalog"):
        replace_story_candidates(candidate_validation_db, "v1", [invalid])
    assert dict(candidate_validation_db.execute(
        "SELECT matched_model_ids, selected_at FROM story_candidates"
    ).fetchone()) == {
        "matched_model_ids": json.dumps(["openai:gpt"]),
        "selected_at": "2026-07-16T00:00:00Z",
    }
