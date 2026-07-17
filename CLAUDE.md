# AI Pulse

## Project goal

Analyze AI-model discourse on Hacker News: model-family and version trends,
co-occurrence, and story framing. This is both a data-analysis course project
and the first version of a personal AI-news monitoring tool.

## Architecture

The pipeline follows a Bronze / Silver / Gold structure.

- **Bronze — `collector.py`**: Collects Algolia HN stories and upserts them
  into the local SQLite database (`ai_monitor.db`). This MVP is manually run;
  Firebase and automated scheduling are out of scope.
- **Silver — `session_enrich.py`**: Stores a session-authored response as a
  validated, schema-light JSON extraction with source evidence in
  `story_extractions`. The pipeline calls no model API; an agent session
  analyzes each story directly under the `ai-pulse-session-enrichment` skill and
  rows are labeled `model = 'session-v1'`. `enrich.py` holds the shared envelope
  contract, input normalization, and evidence verification.
- **Reference data**: A versioned model catalog, aliases, and sourced
  benchmark records provide release and performance facts.
- **Gold — `analysis.py`**: Read-only pandas functions map raw observations to
  model family/version and calculate trends, co-occurrence, and framing.
- **Presentation — `analysis.ipynb`**: Visualizes Gold outputs and includes a
  manual sample-based review of LLM extraction accuracy.

The authoritative design is
`docs/superpowers/specs/2026-07-14-ai-pulse-design.md`. The older brief and
implementation-architecture documents are historical context only.

## Data model

- `stories`: Raw Hacker News stories; `id` is the source item ID.
- `story_extractions`: Versioned raw LLM responses and validated parsed JSON.
- `model_catalog`, `model_aliases`, `benchmark_results`: Sourced reference data.
- `meta`: Collector watermark and operational metadata.

## Commands

```bash
pip install -r requirements.txt
python3 collector.py --backfill 3
python3 session_enrich.py pending --limit 5
python3 session_enrich.py save --story-id ID --raw-file PATH
python3 candidate_selection.py select
pytest -q
```

Never commit local databases or generated caches.

The working tree has pervasive line-ending-only (CRLF/LF) diffs across many
unrelated files. Always stage exact paths (`git add <file> <file>`), and
check `git diff --cached --stat` before committing — never `git add -A`/`.`.

## Development conventions

- Use English for identifiers and filenames; write concise Korean comments and
  user-facing explanations.
- Preserve incremental, idempotent ingestion: source IDs are primary keys and
  re-runs must safely update changing score/comment fields and use the
  documented safety overlap.
- Keep Gold-layer functions independent of notebooks and MCP code so they can
  be reused.
- Preserve unknown extraction values as `unresolved`; do not force them into a
  closed entity-type list.
- Do not expand scope to Reddit, Supabase, a formal ontology, or a hosted MCP
  server unless explicitly requested; these are Phase 2 work.
- Prefer small, readable Python functions over unnecessary abstractions.
