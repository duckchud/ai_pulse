# Repository Guidelines

## Project Structure & Module Organization

AI Pulse is a local Python pipeline for analyzing AI-model discussion in Hacker News.

- `collector.py`: Bronze ingestion from the Algolia HN API.
- `enrich.py`: the Silver envelope contract, input normalization, and evidence
  verification core. Calls no model API.
- `session_enrich.py`: Silver extraction driven by an agent session under the
  `.codex/skills/ai-pulse-session-enrichment` skill; validates and persists
  session-authored envelopes as `model = 'session-v1'` rows.
- `db.py` and `config.py`: SQLite persistence, migrations, and non-secret settings.
- `reference_data.py` and `data/`: sourced model catalog, aliases, and benchmark records.
- `analysis.py`: Gold pandas functions for model family/version trends, co-occurrence,
  story framing, and a fixed-seed `review_sample` for manual accuracy review.
- `data/manual_review_template.csv`: header-only CSV a human reviewer fills in for the
  30-story accuracy review driven by `review_sample`.
- `analysis.ipynb`: submission visualizations of Gold outputs and the fixed-seed
  manual-review sample; not executed by tests, run it manually against a populated
  `ai_monitor.db`.
- `tests/`: pytest files named `test_<module>.py`.

The authoritative design is
`docs/superpowers/specs/2026-07-14-ai-pulse-design.md`. Treat older briefs as historical
context, not implementation instructions.

## Build, Test, and Development Commands

```bash
pip install -r requirements.txt
python collector.py --backfill 3  # Recollect the recent three-day window
python collector.py               # Incremental run with the safety overlap
python session_enrich.py pending --limit 5                    # Fetch unextracted inputs
python session_enrich.py save --story-id ID --raw-file PATH   # Persist one result
pytest -q                         # Run offline tests
```

`ai_monitor.db` is local runtime data and must never be committed.

See `README.md` for the full local execution checklist and the latest smoke-test results.

## Coding Style & Data Rules

Use Python 3.11+, four-space indentation, `snake_case` functions, and `UPPER_CASE`
constants. Use English identifiers and concise Korean comments or user-facing text.
Keep functions focused and add type hints where they clarify an interface.

Preserve source text, raw LLM responses, unknown JSON keys, and evidence quotes. Silver
uses a stable envelope but open-world attributes. Resolve model family and version only
in Gold through the versioned alias catalog; retain unknown values as `unresolved`.
Never infer release dates or benchmark scores without a source URL and evaluation
conditions.

## Testing Guidelines

Use pytest with temporary SQLite databases and mocked HTTP clients. Cover overlap
watermarks, idempotent story upserts, envelope and evidence validation, session
extraction states, alias resolution, unresolved values, empty Gold results, fixed-time
windows, and fixed-seed review-sample reproducibility. Tests must not require network
access.

## Commit, PR, and Security Guidelines

Use focused Conventional Commit-style subjects, for example `feat: add model trend query`
or `docs: revise extraction contract`. PRs should state scope, validation commands,
source-data changes, and notebook screenshots when charts change. Never commit `.env`,
secrets, SQLite files, logs, caches, or unverified model-reference data.
