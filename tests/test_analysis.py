import json

from analysis import (
    _load_candidate_mentions,
    candidate_emerging_models,
    candidate_model_cooccurrence,
    emerging_models,
    model_cooccurrence,
    model_framing_sentiment,
    review_sample,
)
from db import save_extraction, upsert_story_candidates
from reference_data import import_catalog


def _insert_story(conn, story_id, created_at_i, points=1, num_comments=1, keywords="LLM"):
    conn.execute(
        "INSERT INTO stories (id, source, title, author, points, num_comments, created_at, "
        "created_at_i, matched_keywords, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (story_id, "hackernews", "story", "user", points, num_comments, "2026-07-14T10:00:00Z",
         created_at_i, keywords, "2026-07-14T10:01:00Z"),
    )


def _save(conn, story_id, observations, prompt_version="schema-free-v1"):
    payload = {"relevant": True, "observations": observations, "extensions": {}}
    save_extraction(
        conn,
        {
            "story_id": story_id, "prompt_version": prompt_version, "model": "test-model",
            "status": "succeeded", "raw_response": json.dumps(payload), "parsed_json": json.dumps(payload),
            "input_hash": f"hash-{story_id}", "input_char_count": 10, "input_truncated": 0,
            "error_message": None, "enriched_at": "2026-07-14T10:02:00Z",
        },
    )


def _import_catalog(conn, tmp_path, records, name="catalog.json"):
    path = tmp_path / name
    path.write_text(json.dumps(records))
    import_catalog(conn, path)


def _candidate(conn, story_id, model_ids):
    upsert_story_candidates(
        conn,
        [{
            "story_id": story_id,
            "catalog_version": "v1",
            "candidate_reason": "catalog_alias_match",
            "matched_model_ids": json.dumps(model_ids),
            "evidence_json": json.dumps([]),
            "selected_at": "2026-07-14T10:02:00Z",
        }],
    )


def test_emerging_models_counts_distinct_stories_and_keeps_unresolved(temporary_db):
    temporary_db.execute(
        "INSERT INTO stories (id, source, title, author, points, num_comments, created_at, created_at_i, matched_keywords, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("story-1", "hackernews", "Qwen3 release", "user", 10, 4, "2026-07-14T10:00:00Z", 1784023200, "Qwen", "2026-07-14T10:01:00Z"),
    )
    payload = {"relevant": True, "observations": [{"surface": "Unknown Model", "evidence_verified": True, "attributes": {}}], "extensions": {}}
    save_extraction(temporary_db, {"story_id": "story-1", "prompt_version": "schema-free-v1", "model": "test-model", "status": "succeeded", "raw_response": json.dumps(payload), "parsed_json": json.dumps(payload), "input_hash": "hash", "input_char_count": 12, "input_truncated": 0, "error_message": None, "enriched_at": "2026-07-14T10:02:00Z"})
    frame = emerging_models(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family", min_recent_count=1)
    assert {"recent_story_count", "previous_story_count", "mention_delta", "points_sum", "comments_sum"} <= set(frame.columns)
    assert "unresolved" in set(frame["resolution_status"])


def test_emerging_models_counts_each_story_once_for_duplicate_group_mentions(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
        "released_on": "2026-01-01", "release_source_url": "https://openai.example/gpt5",
        "catalog_version": "v1", "aliases": ["GPT-5", "GPT 5 Pro"],
    }])
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [
        {"surface": "GPT-5", "evidence_verified": True, "attributes": {}},
        {"surface": "GPT 5 Pro", "evidence_verified": True, "attributes": {}},
    ])
    frame = emerging_models(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family", min_recent_count=1)
    assert len(frame) == 1
    assert frame.iloc[0]["recent_story_count"] == 1


def test_emerging_models_excludes_unverified_observations(temporary_db):
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [
        {"surface": "Ghost Model", "evidence_verified": False, "attributes": {}},
        {"surface": "No Flag Model", "attributes": {}},
    ])
    frame = emerging_models(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family", min_recent_count=1)
    assert frame.empty
    assert {"recent_story_count", "previous_story_count", "mention_delta", "points_sum", "comments_sum"} <= set(frame.columns)


def test_emerging_models_version_level_labels_missing_version(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "vendor:family:x", "vendor": "Vendor", "family": "Family", "version": None,
        "released_on": None, "release_source_url": "https://vendor.example/release",
        "catalog_version": "v1", "aliases": ["Family Model"],
    }])
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [{"surface": "Family Model", "evidence_verified": True, "attributes": {}}])
    frame = emerging_models(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="version", min_recent_count=1)
    assert frame.iloc[0]["version"] == "unresolved version"
    assert frame.iloc[0]["resolution_status"] == "resolved"


def test_model_cooccurrence_counts_distinct_pairs_once_per_story(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [
        {"model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
         "released_on": "2026-01-01", "release_source_url": "https://openai.example/gpt5",
         "catalog_version": "v1", "aliases": ["GPT-5"]},
        {"model_id": "anthropic:claude:opus-4-7", "vendor": "Anthropic", "family": "Claude", "version": "Opus 4.7",
         "released_on": "2026-01-01", "release_source_url": "https://anthropic.example/opus",
         "catalog_version": "v1", "aliases": ["Claude Opus 4.7"]},
    ])
    for i in range(2):
        story_id = f"story-{i}"
        _insert_story(temporary_db, story_id, created_at_i=1784023200 + i)
        _save(temporary_db, story_id, [
            {"surface": "GPT-5", "evidence_verified": True, "attributes": {}},
            {"surface": "Claude Opus 4.7", "evidence_verified": True, "attributes": {}},
            {"surface": "GPT-5", "evidence_verified": True, "attributes": {}},
        ])
    frame = model_cooccurrence(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family", min_count=2)
    assert len(frame) == 1
    assert frame.iloc[0]["story_count"] == 2
    assert {frame.iloc[0]["vendor_a"], frame.iloc[0]["vendor_b"]} == {"OpenAI", "Anthropic"}


def test_candidate_emerging_models_counts_one_story_once_per_family(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
        "released_on": None, "release_source_url": "https://example.test/gpt",
        "catalog_version": "v1", "aliases": ["GPT-5"],
    }])
    _insert_story(temporary_db, "story-1", 1784023200)
    _candidate(temporary_db, "story-1", ["openai:gpt:5"])
    frame = candidate_emerging_models(temporary_db, "2026-07-14T12:00:00Z", "family", min_recent_count=1)
    assert frame.iloc[0]["group_label"] == "OpenAI/GPT"
    assert frame.iloc[0]["recent_story_count"] == 1
    assert frame.iloc[0]["candidate_reason"] == "catalog_alias_match"
    assert not {"prompt_version", "sentiment", "stance"} & set(frame.columns)


def test_candidate_cooccurrence_counts_each_pair_once_per_story(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [
        {"model_id": "openai:gpt", "vendor": "OpenAI", "family": "GPT", "version": None, "released_on": None, "release_source_url": "https://example.test/gpt", "catalog_version": "v1", "aliases": ["GPT"]},
        {"model_id": "anthropic:claude", "vendor": "Anthropic", "family": "Claude", "version": None, "released_on": None, "release_source_url": "https://example.test/claude", "catalog_version": "v1", "aliases": ["Claude"]},
    ])
    for story_id in ("story-1", "story-2"):
        _insert_story(temporary_db, story_id, 1784023200)
        _candidate(temporary_db, story_id, ["openai:gpt", "anthropic:claude"])
    frame = candidate_model_cooccurrence(temporary_db, "2026-07-14T12:00:00Z", "family", min_count=2)
    assert len(frame) == 1
    assert frame.iloc[0]["story_count"] == 2
    assert not {"prompt_version", "sentiment", "stance"} & set(frame.columns)


def test_candidate_gold_returns_empty_frames_without_candidates(temporary_db):
    assert candidate_emerging_models(temporary_db, "2026-07-14T12:00:00Z", "family").empty
    assert candidate_model_cooccurrence(temporary_db, "2026-07-14T12:00:00Z", "family").empty


def test_candidate_loader_returns_one_row_per_story_and_model_id(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
        "released_on": None, "release_source_url": "https://example.test/gpt",
        "catalog_version": "v1", "aliases": ["GPT-5"],
    }])
    _insert_story(temporary_db, "story-1", 1784023200)
    _candidate(temporary_db, "story-1", ["openai:gpt:5", "openai:gpt:5"])
    mentions = _load_candidate_mentions(temporary_db)
    assert len(mentions) == 1


def test_model_framing_sentiment_preserves_original_stance_labels(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
        "released_on": "2026-01-01", "release_source_url": "https://openai.example/gpt5",
        "catalog_version": "v1", "aliases": ["GPT-5"],
    }])
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [
        {"surface": "GPT-5", "evidence_verified": True, "attributes": {"stance": "cautiously-optimistic"}},
        {"surface": "GPT-5", "evidence_verified": True, "attributes": {}},
    ])
    frame = model_framing_sentiment(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family")
    assert frame.iloc[0]["stance"] == "cautiously-optimistic"
    assert frame.iloc[0]["story_count"] == 1


def test_model_framing_sentiment_filters_by_model_id(temporary_db, tmp_path):
    _import_catalog(temporary_db, tmp_path, [
        {"model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
         "released_on": "2026-01-01", "release_source_url": "https://openai.example/gpt5",
         "catalog_version": "v1", "aliases": ["GPT-5"]},
        {"model_id": "anthropic:claude:opus-4-7", "vendor": "Anthropic", "family": "Claude", "version": "Opus 4.7",
         "released_on": "2026-01-01", "release_source_url": "https://anthropic.example/opus",
         "catalog_version": "v1", "aliases": ["Claude Opus 4.7"]},
    ])
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [
        {"surface": "GPT-5", "evidence_verified": True, "attributes": {"stance": "excited"}},
        {"surface": "Claude Opus 4.7", "evidence_verified": True, "attributes": {"stance": "skeptical"}},
    ])
    frame = model_framing_sentiment(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family", model_id="openai:gpt:5")
    assert len(frame) == 1
    assert frame.iloc[0]["stance"] == "excited"


def test_model_framing_sentiment_preserves_distinct_stances_for_same_group(temporary_db, tmp_path):
    # 한 story가 같은 (vendor/family) 그룹에 대해 서로 다른 stance 두 개를 담으면
    # 두 stance 행이 각각 살아남아야 한다(story-count 1씩) — 합쳐지지 않는다.
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
        "released_on": "2026-01-01", "release_source_url": "https://openai.example/gpt5",
        "catalog_version": "v1", "aliases": ["GPT-5", "GPT 5 Pro"],
    }])
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [
        {"surface": "GPT-5", "evidence_verified": True, "attributes": {"stance": "impressed-by-latency"}},
        {"surface": "GPT 5 Pro", "evidence_verified": True, "attributes": {"stance": "worried-about-cost"}},
    ])
    frame = model_framing_sentiment(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family")
    assert set(frame["stance"]) == {"impressed-by-latency", "worried-about-cost"}
    assert frame["story_count"].tolist() == [1, 1]


def test_model_framing_sentiment_collapses_duplicate_stance_for_same_group(temporary_db, tmp_path):
    # 같은 story·같은 그룹·같은 stance인 observation 두 개는 한 행으로 접히고
    # story-count는 1이어야 한다(중복 이중집계 방지).
    _import_catalog(temporary_db, tmp_path, [{
        "model_id": "openai:gpt:5", "vendor": "OpenAI", "family": "GPT", "version": "5",
        "released_on": "2026-01-01", "release_source_url": "https://openai.example/gpt5",
        "catalog_version": "v1", "aliases": ["GPT-5", "GPT 5 Pro"],
    }])
    _insert_story(temporary_db, "story-1", created_at_i=1784023200)
    _save(temporary_db, "story-1", [
        {"surface": "GPT-5", "evidence_verified": True, "attributes": {"stance": "excited"}},
        {"surface": "GPT 5 Pro", "evidence_verified": True, "attributes": {"stance": "excited"}},
    ])
    frame = model_framing_sentiment(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family")
    assert len(frame) == 1
    assert frame.iloc[0]["stance"] == "excited"
    assert frame.iloc[0]["story_count"] == 1


def test_all_gold_functions_return_empty_dataframe_with_columns_when_no_data(temporary_db):
    emerging = emerging_models(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family")
    cooc = model_cooccurrence(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family")
    framing = model_framing_sentiment(temporary_db, as_of="2026-07-14T12:00:00Z", group_level="family")

    assert emerging.empty
    assert {"recent_story_count", "previous_story_count", "mention_delta", "points_sum",
            "comments_sum", "as_of", "collection_query_version", "prompt_version",
            "catalog_version"} <= set(emerging.columns)

    assert cooc.empty
    assert {"story_count", "as_of", "collection_query_version", "prompt_version",
            "catalog_version"} <= set(cooc.columns)

    assert framing.empty
    assert {"stance", "story_count", "as_of", "collection_query_version", "prompt_version",
            "catalog_version"} <= set(framing.columns)


def test_review_sample_is_reproducible(temporary_db):
    first = review_sample(temporary_db, sample_size=30, seed=20260714)
    second = review_sample(temporary_db, sample_size=30, seed=20260714)
    assert first["story_id"].tolist() == second["story_id"].tolist()


def test_review_sample_handles_fewer_rows_than_sample_size(temporary_db):
    _insert_story(temporary_db, "story-1", created_at_i=1784023200, keywords="title-only")
    _save(temporary_db, "story-1", [{"surface": "GPT-5", "evidence_verified": True, "attributes": {}}])
    first = review_sample(temporary_db, sample_size=30, seed=20260714)
    second = review_sample(temporary_db, sample_size=30, seed=20260714)
    assert first["story_id"].tolist() == ["story-1"]
    assert first["story_id"].tolist() == second["story_id"].tolist()
