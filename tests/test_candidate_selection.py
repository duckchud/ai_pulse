import json
import sqlite3

import pytest

import candidate_selection
from candidate_selection import match_story_aliases, select_candidates, unmatched_sample


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


def test_unmatched_sample_is_reproducible_and_excludes_candidates(
    catalog_with_candidate_stories,
):
    catalog_with_candidate_stories.execute(
        "INSERT INTO stories (id, source, title, text, fetched_at) VALUES (?, ?, ?, ?, ?)",
        ("unknown-b", "hackernews", "AI story B", None, "2026-07-16T00:00:00Z"),
    )
    catalog_with_candidate_stories.commit()
    select_candidates(catalog_with_candidate_stories, "2026-07-16T00:00:00Z")

    first = unmatched_sample(catalog_with_candidate_stories, sample_size=1, seed=7)

    assert first == unmatched_sample(catalog_with_candidate_stories, sample_size=1, seed=7)
    assert {row["story_id"] for row in first}.isdisjoint({"candidate"})


def test_unmatched_sample_rejects_zero_size(temporary_db):
    with pytest.raises(ValueError, match="sample_size must be at least 1"):
        unmatched_sample(temporary_db, 0, 7)


def test_unmatched_sample_cli_migrates_a_legacy_schema_before_querying(
    tmp_path, monkeypatch, capsys
):
    database_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(database_path)
    legacy.executescript(
        """
        CREATE TABLE stories (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, url TEXT,
            author TEXT, points INTEGER, num_comments INTEGER, created_at TEXT,
            created_at_i INTEGER, text TEXT, matched_keywords TEXT, fetched_at TEXT NOT NULL
        );
        CREATE TABLE model_catalog (
            model_id TEXT PRIMARY KEY, vendor TEXT NOT NULL, family TEXT NOT NULL,
            release_source_url TEXT NOT NULL, catalog_version TEXT NOT NULL
        );
        INSERT INTO stories VALUES ('story-1', 'hackernews', 'unmatched', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-07-16T00:00:00Z');
        INSERT INTO model_catalog VALUES ('openai:gpt', 'OpenAI', 'GPT', 'https://example.test/gpt', 'v1');
        """
    )
    legacy.close()
    monkeypatch.setattr(candidate_selection, "DB_PATH", database_path)
    monkeypatch.setattr(
        "sys.argv",
        ["candidate_selection.py", "unmatched-sample", "--sample-size", "1", "--seed", "7"],
    )

    assert candidate_selection.main() == 0
    assert json.loads(capsys.readouterr().out) == [
        {"story_id": "story-1", "title": "unmatched", "text": None}
    ]


@pytest.mark.parametrize("function", [select_candidates, unmatched_sample])
def test_selection_functions_reject_mixed_catalog_versions(temporary_db, function):
    temporary_db.executemany(
        "INSERT INTO model_catalog (model_id, vendor, family, release_source_url, catalog_version) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("openai:gpt", "OpenAI", "GPT", "https://example.test/gpt", "v1"),
            ("anthropic:claude", "Anthropic", "Claude", "https://example.test/claude", "v2"),
        ],
    )
    with pytest.raises(ValueError, match="exactly one catalog_version"):
        if function is select_candidates:
            function(temporary_db)
        else:
            function(temporary_db, sample_size=1, seed=7)


def test_selection_replaces_current_version_and_keeps_other_version_candidates(
    catalog_with_candidate_stories,
):
    conn = catalog_with_candidate_stories
    select_candidates(conn, "2026-07-16T01:00:00Z")
    conn.execute(
        "INSERT INTO story_candidates VALUES (?, ?, ?, ?, ?, ?)",
        (
            "candidate", "v0", "catalog_alias_match", json.dumps(["openai:gpt"]),
            json.dumps([]), "2026-07-15T00:00:00Z",
        ),
    )
    conn.execute("UPDATE stories SET title = 'unrelated story' WHERE id = 'candidate'")
    conn.commit()

    assert select_candidates(conn, "2026-07-16T02:00:00Z") == 0
    assert [tuple(row) for row in conn.execute(
        "SELECT catalog_version, story_id FROM story_candidates ORDER BY catalog_version"
    )] == [("v0", "candidate")]
