from db import latest_successful_extractions, save_extraction


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
