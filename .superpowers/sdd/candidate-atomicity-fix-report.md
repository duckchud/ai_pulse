# Candidate atomicity fix report

## Scope

- `upsert_story_candidates` now wraps its database write in a SQLite savepoint.
  Any database error rolls the entire batch back to the savepoint before the
  exception is re-raised.
- Candidate validation requires non-empty string scalar fields, a non-empty
  evidence list, and catalog-backed evidence aliases. Each normalized evidence
  alias must map to the same evidence `model_id` in `model_aliases`.
- Candidate Gold test fixtures now persist evidence that satisfies the same
  catalog and source-text contract.
- All executable candidate commands in the candidate-selection plan use
  `python3`.

## Regression coverage

- A trigger rejects the second row of a valid-first batch; the test confirms
  that no candidate rows remain after the SQLite error.
- Empty evidence, missing `selected_at`, and fabricated aliases are rejected.

## Verification

```text
$ pytest -q tests/test_db.py tests/test_candidate_selection.py tests/test_analysis.py
51 passed in 2.90s

$ python3 -m compileall -q candidate_selection.py analysis.py db.py
exit 0

$ git diff --check
exit 0

$ pytest -q
83 passed in 4.10s
```
