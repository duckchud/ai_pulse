import json

import pytest

from candidate_selection import match_story_aliases, select_candidates


ALIASES = [
    {"model_id": "openai:gpt", "alias": "GPT"},
    {"model_id": "anthropic:claude", "alias": "Claude Code"},
]


def test_matching_preserves_source_quotes_and_fields():
    assert match_story_aliases("GPT-5 review", "Claude Code is useful", ALIASES) == [
        {
            "model_id": "anthropic:claude",
            "alias": "Claude Code",
            "field": "text",
            "quote": "Claude Code",
        },
        {"model_id": "openai:gpt", "alias": "GPT", "field": "title", "quote": "GPT"},
    ]


@pytest.mark.parametrize("title", ["gpt", "GPT-5", "GPT_5"])
def test_matching_accepts_case_and_separators(title):
    assert match_story_aliases(title, "", ALIASES)[0]["model_id"] == "openai:gpt"


def test_matching_rejects_alphanumeric_substrings():
    assert match_story_aliases("gptology", "claudecodebase", ALIASES) == []


@pytest.fixture
def catalog_with_candidate_stories(temporary_db):
    temporary_db.executemany(
        "INSERT INTO model_catalog (model_id, vendor, family, release_source_url, catalog_version) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("openai:gpt", "OpenAI", "GPT", "https://example.com/gpt", "v1"),
            ("anthropic:claude", "Anthropic", "Claude", "https://example.com/claude", "v1"),
        ],
    )
    temporary_db.executemany(
        "INSERT INTO model_aliases (alias_normalized, model_id) VALUES (?, ?)",
        [("gpt", "openai:gpt"), ("claude code", "anthropic:claude")],
    )
    temporary_db.executemany(
        "INSERT INTO stories (id, source, title, text, fetched_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("candidate", "hackernews", "GPT vs Claude Code", None, "2026-07-16T00:00:00Z"),
            ("non-candidate", "hackernews", "gptology", None, "2026-07-16T00:00:00Z"),
        ],
    )
    temporary_db.commit()
    return temporary_db


def test_selector_persists_catalog_alias_matches_idempotently(catalog_with_candidate_stories):
    assert select_candidates(catalog_with_candidate_stories, "2026-07-16T01:00:00Z") == 1
    assert select_candidates(catalog_with_candidate_stories, "2026-07-16T02:00:00Z") == 1

    row = catalog_with_candidate_stories.execute(
        "SELECT matched_model_ids, evidence_json, selected_at FROM story_candidates"
    ).fetchone()
    assert catalog_with_candidate_stories.execute(
        "SELECT COUNT(*) FROM story_candidates"
    ).fetchone()[0] == 1
    assert json.loads(row["matched_model_ids"]) == ["anthropic:claude", "openai:gpt"]
    assert len(json.loads(row["evidence_json"])) == 2
    assert row["selected_at"] == "2026-07-16T02:00:00Z"
