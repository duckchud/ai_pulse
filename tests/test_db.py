from db import latest_successful_extractions, save_extraction


def test_migrate_creates_schema_free_extractions_table(temporary_db):
    columns = {row[1] for row in temporary_db.execute("PRAGMA table_info(story_extractions)")}
    assert {"story_id", "prompt_version", "model", "status", "parsed_json", "input_hash"} <= columns


def test_latest_successful_extraction_prefers_newest_success(temporary_db):
    temporary_db.execute("INSERT INTO stories VALUES ('1','hackernews','T',NULL,'a',1,0,'2026-07-14T00:00:00Z',1,NULL,'LLM','x')")
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v1", "model": "m", "status": "failed", "raw_response": "", "parsed_json": None, "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": "timeout", "enriched_at": "2026-07-14T00:00:00Z"})
    save_extraction(temporary_db, {"story_id": "1", "prompt_version": "v2", "model": "m", "status": "succeeded", "raw_response": "{}", "parsed_json": "{}", "input_hash": "a", "input_char_count": 1, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T01:00:00Z"})
    assert latest_successful_extractions(temporary_db).iloc[0]["prompt_version"] == "v2"
