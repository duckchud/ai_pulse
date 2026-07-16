# Rule-Based Candidate Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, catalog-driven candidate-selection path that creates reproducible model-mention candidates, unmatched-story review samples, and candidate-based trend/co-occurrence Gold outputs without calling an AI model.

**Architecture:** `candidate_selection.py` reads the imported alias catalog and raw Bronze stories, performs boundary-safe matching, and persists traceable candidate records through `db.py`. Candidate Gold functions load those persisted model IDs directly; existing session-based Silver and sentiment functions remain unchanged.

**Tech Stack:** Python 3.11, SQLite, pandas, pytest, and the local model catalog.

## Global Constraints

- Use Python 3.11+, four-space indentation, English identifiers, and concise Korean comments/user-facing text.
- Do not call an external or local LLM; candidate rules come exclusively from `model_aliases` joined with `model_catalog`.
- Preserve raw story text and the exact quote that matched every alias.
- Preserve unknown stories through a deterministic unmatched sample; do not fabricate model identities.
- Do not stage `ai_monitor.db`. The worktree has unrelated unstaged changes, so use `git add -p` for shared files and inspect the staged diff before every commit.

---

### Task 1: Persist catalog-versioned candidates

**Files:**
- Modify: `db.py:12-98` and `db.py:144-172`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces `catalog_version(conn: sqlite3.Connection) -> str`.
- Produces `upsert_story_candidates(conn: sqlite3.Connection, candidates: list[dict[str, str]]) -> None`.
- Creates `story_candidates(story_id, catalog_version, candidate_reason, matched_model_ids, evidence_json, selected_at)` keyed by `(story_id, catalog_version)`.

- [ ] **Step 1: Write the failing tests**

Add these test cases to `tests/test_db.py`.

    import json
    import pytest
    from db import catalog_version, upsert_story_candidates

    def test_candidates_upsert_per_catalog_version(temporary_db):
        temporary_db.execute("INSERT INTO stories (id, source, fetched_at) VALUES ('story-1', 'hackernews', '2026-07-16T00:00:00Z')")
        row = {"story_id": "story-1", "catalog_version": "v1", "candidate_reason": "catalog_alias_match", "matched_model_ids": json.dumps(["openai:gpt"]), "evidence_json": "[]", "selected_at": "2026-07-16T00:00:00Z"}
        upsert_story_candidates(temporary_db, [row])
        upsert_story_candidates(temporary_db, [{**row, "selected_at": "2026-07-16T01:00:00Z"}])
        assert temporary_db.execute("SELECT COUNT(*) FROM story_candidates").fetchone()[0] == 1
        assert temporary_db.execute("SELECT selected_at FROM story_candidates").fetchone()[0] == "2026-07-16T01:00:00Z"

    def test_catalog_version_requires_exactly_one_value(temporary_db):
        with pytest.raises(ValueError, match="exactly one catalog_version"):
            catalog_version(temporary_db)

- [ ] **Step 2: Run the focused test and verify failure**

Run `pytest tests/test_db.py -q`. Expected: import failure for the new helpers, then missing-table failure once helpers exist without the migration.

- [ ] **Step 3: Add the migration and helpers**

Append this DDL to `SCHEMA` in `db.py`.

    CREATE TABLE IF NOT EXISTS story_candidates (
        story_id TEXT NOT NULL,
        catalog_version TEXT NOT NULL,
        candidate_reason TEXT NOT NULL,
        matched_model_ids TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        selected_at TEXT NOT NULL,
        PRIMARY KEY (story_id, catalog_version),
        FOREIGN KEY (story_id) REFERENCES stories(id)
    );

Add `catalog_version` below `upsert_stories`: query sorted distinct `catalog_version` values from `model_catalog`; if the count is not exactly one, raise `ValueError("candidate selection requires exactly one catalog_version")`; otherwise return that value.

Add `upsert_story_candidates` below it: return for an empty list; otherwise execute an `INSERT INTO story_candidates (...) VALUES (...) ON CONFLICT(story_id, catalog_version) DO UPDATE` that updates `candidate_reason`, `matched_model_ids`, `evidence_json`, and `selected_at`, then commit.

- [ ] **Step 4: Verify and commit the persistence task**

Run `pytest tests/test_db.py -q`, then `pytest -q`; both must pass. Stage only candidate hunks with `git add -p db.py tests/test_db.py`; verify with `git diff --cached --check` and `git diff --cached`; then commit `feat: persist catalog-based story candidates`.

### Task 2: Implement deterministic alias matching and selection

**Files:**
- Create: `candidate_selection.py`
- Create: `tests/test_candidate_selection.py`

**Interfaces:**
- Consumes `db.catalog_version`, `db.upsert_story_candidates`, and `model_aliases JOIN model_catalog`.
- Produces `match_story_aliases(title: str | None, text: str | None, aliases: list[dict[str, str]]) -> list[dict[str, str]]`.
- Produces `select_candidates(conn: sqlite3.Connection, selected_at: str | None = None) -> int`.

- [ ] **Step 1: Write the failing matching tests**

Create `tests/test_candidate_selection.py` with these cases.

    import pytest
    from candidate_selection import match_story_aliases

    ALIASES = [
        {"model_id": "openai:gpt", "alias": "GPT"},
        {"model_id": "anthropic:claude", "alias": "Claude Code"},
    ]

    def test_matching_preserves_source_quotes_and_fields():
        assert match_story_aliases("GPT-5 review", "Claude Code is useful", ALIASES) == [
            {"model_id": "anthropic:claude", "alias": "Claude Code", "field": "text", "quote": "Claude Code"},
            {"model_id": "openai:gpt", "alias": "GPT", "field": "title", "quote": "GPT"},
        ]

    @pytest.mark.parametrize("title", ["gpt", "GPT-5", "GPT_5"])
    def test_matching_accepts_case_and_separators(title):
        assert match_story_aliases(title, "", ALIASES)[0]["model_id"] == "openai:gpt"

    def test_matching_rejects_alphanumeric_substrings():
        assert match_story_aliases("gptology", "claudecodebase", ALIASES) == []

Run `pytest tests/test_candidate_selection.py -q`. Expected: import failure because `candidate_selection.py` does not exist.

- [ ] **Step 2: Implement boundary-safe matching**

Create `candidate_selection.py`; import only standard-library modules, `config.DB_PATH`, `db.connect`, `db.migrate`, `db.catalog_version`, `db.upsert_story_candidates`, and `reference_data.normalize_alias`.

Implement the pattern and matcher exactly as follows.

    def _alias_pattern(alias: str) -> re.Pattern[str]:
        tokens = [re.escape(token) for token in normalize_alias(alias).split()]
        joined = r"[^A-Za-z0-9]+".join(tokens)
        return re.compile(rf"(?<![A-Za-z0-9])({joined})(?![A-Za-z0-9])", re.IGNORECASE)

    def match_story_aliases(title, text, aliases):
        evidence, seen = [], set()
        for alias_row in aliases:
            for field, source in (("title", title or ""), ("text", text or "")):
                for match in _alias_pattern(alias_row["alias"]).finditer(source):
                    item = {"model_id": alias_row["model_id"], "alias": alias_row["alias"], "field": field, "quote": match.group(1)}
                    key = tuple(item[key] for key in ("model_id", "alias", "field", "quote"))
                    if key not in seen:
                        seen.add(key)
                        evidence.append(item)
        return sorted(evidence, key=lambda item: (item["model_id"], item["field"], item["quote"], item["alias"]))

Implement `select_candidates`: get its version through `catalog_version(conn)`; load aliases using `SELECT ma.model_id, ma.alias_normalized AS alias FROM model_aliases ma JOIN model_catalog mc ON mc.model_id = ma.model_id ORDER BY ma.model_id, ma.alias_normalized`; load `id`, `title`, and `text` from `stories`; skip empty matches; persist `candidate_reason="catalog_alias_match"`, sorted unique IDs, and evidence using `json.dumps(..., ensure_ascii=False)`; use `datetime.now(timezone.utc).isoformat()` when `selected_at is None`; return the number of persisted rows.

- [ ] **Step 3: Add selector persistence coverage**

Add fixtures that insert a single-version two-model catalog, `model_aliases`, `GPT vs Claude Code`, and `gptology`. Call `select_candidates` twice with distinct timestamps and assert: exactly one row is stored, its sorted `matched_model_ids` are `["anthropic:claude", "openai:gpt"]`, it has two evidence records, and it contains the second timestamp.

- [ ] **Step 4: Verify and commit the selection task**

Run `pytest tests/test_candidate_selection.py -q`, then `pytest -q`; both must pass. Stage `candidate_selection.py` and `tests/test_candidate_selection.py`, inspect the staged diff, then commit `feat: select model candidates from catalog aliases`.

### Task 3: Add the unmatched-sample CLI

**Files:**
- Modify: `candidate_selection.py`
- Modify: `tests/test_candidate_selection.py`
- Modify: `README.md`

**Interfaces:**
- Produces `unmatched_sample(conn: sqlite3.Connection, sample_size: int, seed: int) -> list[dict[str, object]]`.
- Produces `python candidate_selection.py select` and `python candidate_selection.py unmatched-sample --sample-size 30 --seed 20260716`.

- [ ] **Step 1: Write the failing unmatched-sample tests**

Add the following coverage using the Task 2 catalog/story fixture.

    def test_unmatched_sample_is_reproducible_and_excludes_candidates(temporary_db):
        _catalog(temporary_db)
        _story(temporary_db, "matched", "GPT")
        _story(temporary_db, "unknown-a", "AI story A")
        _story(temporary_db, "unknown-b", "AI story B")
        select_candidates(temporary_db, selected_at="2026-07-16T00:00:00Z")
        first = unmatched_sample(temporary_db, sample_size=1, seed=7)
        assert first == unmatched_sample(temporary_db, sample_size=1, seed=7)
        assert {row["story_id"] for row in first}.isdisjoint({"matched"})

    def test_unmatched_sample_rejects_zero_size(temporary_db):
        with pytest.raises(ValueError, match="sample_size must be at least 1"):
            unmatched_sample(temporary_db, 0, 7)

Run `pytest tests/test_candidate_selection.py -q`. Expected: `unmatched_sample` import failure.

- [ ] **Step 2: Implement sample and CLI**

Implement `unmatched_sample` to reject `sample_size < 1`, query all `stories` left-joined to `story_candidates` for the current catalog version where candidate is null, order by `s.id`, then return `random.Random(seed).sample(rows, k=min(sample_size, len(rows)))`. Each row must contain `story_id`, `title`, and `text`; it must not write to DB.

Implement `argparse` required subcommands. `select` calls `migrate`, `select_candidates`, then prints `후보 선별 완료: N건`. `unmatched-sample` accepts positive `--sample-size` and integer `--seed`, then prints `json.dumps(unmatched_sample(...), ensure_ascii=False)`. Both commands use `DB_PATH`, close connections in `finally`, and contain no network/model code.

Add README examples directly after the session-enrichment commands: `python candidate_selection.py select` and `python candidate_selection.py unmatched-sample --sample-size 30 --seed 20260716`. State that these commands are offline, catalog-driven, and intentionally omit sentiment/stance.

- [ ] **Step 3: Verify and commit the CLI task**

Run `pytest tests/test_candidate_selection.py -q`, `python candidate_selection.py --help`, `python candidate_selection.py unmatched-sample --help`, and `pytest -q`. All must pass and both help commands must exit 0. Stage only candidate CLI/tests/README hunks, inspect them, and commit `feat: add offline candidate selection workflow`.

### Task 4: Add candidate-based Gold trend and co-occurrence functions

**Files:**
- Modify: `analysis.py`
- Modify: `tests/test_analysis.py`
- Modify: `README.md`

**Interfaces:**
- Produces `candidate_emerging_models(conn: sqlite3.Connection, as_of, group_level: str, window_hours: int = 24, min_recent_count: int = 2, top_n: int = 20) -> pd.DataFrame`.
- Produces `candidate_model_cooccurrence(conn: sqlite3.Connection, as_of, group_level: str, min_count: int = 2) -> pd.DataFrame`.
- Candidate results expose the existing count metrics plus `candidate_reason` and `catalog_version`; they never expose `prompt_version`, sentiment, or stance.

- [ ] **Step 1: Write failing candidate Gold tests**

Import the two functions in `tests/test_analysis.py`. Add `_candidate(conn, story_id, model_ids)` that calls `upsert_story_candidates` with `catalog_version="v1"`, `candidate_reason="catalog_alias_match"`, and JSON model IDs/evidence. Add these cases after the existing co-occurrence tests.

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

    def test_candidate_gold_returns_empty_frames_without_candidates(temporary_db):
        assert candidate_emerging_models(temporary_db, "2026-07-14T12:00:00Z", "family").empty
        assert candidate_model_cooccurrence(temporary_db, "2026-07-14T12:00:00Z", "family").empty

Run `pytest tests/test_analysis.py -q`. Expected: import failure because candidate functions do not exist.

- [ ] **Step 2: Implement an isolated candidate loader**

In `analysis.py`, import `catalog_version` from `db` and add `_load_candidate_mentions(conn)`. It must call `catalog_version(conn)`, query current-version `story_candidates` joined to `stories`, parse `matched_model_ids`, and join each ID to `model_catalog`. It returns one row per `(story_id, model_id)` with `vendor`, `family`, `version`, `created_at_i`, `points`, `num_comments`, and `collection_query_version`. Do not read or write `story_extractions` in this loader.

Define `_CANDIDATE_EMERGING_COLUMNS` as the trend count/group columns plus `as_of`, `collection_query_version`, `catalog_version`, and `candidate_reason`. Define `_CANDIDATE_COOCCURRENCE_COLUMNS` as the co-occurrence pair columns plus the same metadata. Return empty frames with exactly these columns.

- [ ] **Step 3: Implement candidate aggregations**

Implement `candidate_emerging_models` by applying the exact window boundaries and ranking from `emerging_models`: validate `group_level`, load candidates, call `_add_group_columns`, de-duplicate `(story_id, group_key)`, compute recent and previous distinct-story counts, and rank by `mention_delta DESC`, `recent_story_count DESC`. Populate metadata with the actual joined collection-query version, the one catalog version, and `candidate_reason="catalog_alias_match"`.

Implement `candidate_model_cooccurrence` by applying the exact pair-generation rules from `model_cooccurrence` to unique `(story_id, group_key)` candidate rows. It must count each pair at most once per story and honour `min_count`. Do not alter existing Silver functions.

- [ ] **Step 4: Verify, document, and commit Gold work**

Run `pytest tests/test_analysis.py -q` and `pytest -q`; both must pass. Add a README note naming the two candidate Gold functions and stating they intentionally omit sentiment/stance. Stage only candidate hunks in `analysis.py`, `tests/test_analysis.py`, and `README.md`; inspect them; then commit `feat: analyze catalog-based model candidates`.

### Task 5: Verify the end-to-end offline workflow

**Files:**
- Modify: `README.md` only if a verified command differs from its documentation.
- Test: `tests/test_candidate_selection.py`, `tests/test_analysis.py`, all tests.

**Interfaces:**
- Consumes Tasks 1-4 and a disposable SQLite database.
- Produces a verified offline workflow; no database, cache, or logs are committed.

- [ ] **Step 1: Create a disposable DB and seed deterministic records**

Run the following using a temporary path outside the repository; use a short `python -c` command to call `connect`, `migrate`, `import_catalog`, and insert two stories: one titled `GPT and Claude`, one titled `unrelated story`. The former must be candidate-eligible; the latter must remain unmatched. Do not write to `ai_monitor.db`.

- [ ] **Step 2: Exercise the public functions without a model call**

Using `connect(temp_db)`, call `select_candidates(conn)` and `unmatched_sample(conn, sample_size=1, seed=7)`. Assert selection returns 1 and the sample returns only the unrelated story. Then call `candidate_emerging_models(conn, as_of, "family", min_recent_count=1)` and assert it has the named `OpenAI/GPT` and `Anthropic/Claude` candidate groups. This direct-function smoke test avoids adding environment-variable configuration to `config.py` solely for verification.

- [ ] **Step 3: Run complete offline verification**

Run `python -m compileall candidate_selection.py analysis.py db.py`, `pytest -q`, and `git diff --check`. Expected: compilation and all tests pass with no network access and no whitespace errors.

- [ ] **Step 4: Clean up and commit only documentation correction if needed**

Delete the disposable DB, run `git status --short`, and confirm neither it nor `ai_monitor.db` is staged. If verification required a README correction, stage only that hunk, inspect it, and commit `docs: clarify candidate selection workflow`; otherwise make no commit in this task.

## Plan self-review

- Spec coverage: Task 1 provides catalog-versioned persistence; Task 2 implements catalog-only boundary-safe selection and exact evidence; Task 3 provides the reproducible unmatched review sample and offline CLI; Task 4 provides only trend/co-occurrence Gold outputs; Task 5 validates the no-model workflow.
- Placeholder scan: each task names files, interfaces, test assertions, implementation behavior, commands, and success criteria. No deferred work or ambiguous error handling remains.
- Type consistency: Task 1 creates the DB helpers used by Tasks 2-4; Task 2 writes JSON model IDs that Task 4 reads; Task 3 samples the candidates selected by Task 2.
