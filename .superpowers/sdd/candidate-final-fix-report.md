# Candidate selection final-fix report

## Scope

- `unmatched-sample` now runs `migrate(conn)` before its query, including for a
  pre-feature database.
- Candidate selection replaces only the current catalog-version rows in one
  transaction, so stale current-version rows are deleted while other-version
  history remains.
- Candidate writes validate both JSON arrays, unique/current-catalog model IDs,
  evidence fields, evidence model IDs, and source-text quotes before any row is
  written. Invalid batches leave no partial writes.
- Candidate Gold now raises for a nonempty mixed-version catalog and corrupt
  persisted model ID payloads; only an empty catalog with no candidates returns
  the dedicated empty frame.
- Candidate commands in README use `python3`; the Task 5 smoke assertion now
  names the Claude and GPT groups.

## Verification

```text
$ pytest -q tests/test_db.py tests/test_candidate_selection.py tests/test_analysis.py
47 passed in 2.71s

$ python3 candidate_selection.py --help
usage: candidate_selection.py [-h] {select,unmatched-sample} ...

$ python3 candidate_selection.py unmatched-sample --help
usage: candidate_selection.py unmatched-sample [-h] --sample-size SAMPLE_SIZE --seed SEED

$ python3 -m compileall -q candidate_selection.py analysis.py db.py
exit 0

$ offline direct-function smoke
offline smoke: selected=1 unmatched=unmatched groups=Anthropic/Claude,OpenAI/GPT

$ pytest -q
79 passed in 4.12s

$ git diff --check
exit 0
```

## Commit

`fix: harden catalog candidate selection`
