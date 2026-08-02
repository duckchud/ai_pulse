# Task 1 Report: Pinned Plotly Bundle and JSON Contract

## Delivered

- Added the pinned Plotly 2.35.2 bundle at `vendor/plotly-2.35.2.min.js`.
- Added `_serialize_report_data()` to emit compact UTF-8 JSON and neutralize `</` for an inert script element.
- Added `_load_plotly_bundle()` and embedded both the `report-data` JSON script and an inline Plotly bundle marker in generated reports.
- Kept visible report values on the existing HTML-escaping path.

## Test Coverage

- Render behavior tests monkeypatch `build_report._load_plotly_bundle` to a small marker, preventing repeated reads and rendering of the 4.4 MB vendored artifact.
- A dedicated test verifies the real pinned bundle exists and contains `Plotly.newPlot`.
- JSON transport coverage verifies that a closing script tag is serialized as `<\\/script>`.
- The visible dynamic-text escaping test now excludes the inert JSON script, whose raw JSON text is deliberately not HTML-escaped.

## Verification

```text
timeout 90s pytest -q tests/test_report.py -k 'report_data or external_plotly'
7 passed, 27 deselected, 2 warnings in 1.49s

timeout 90s pytest -q tests/test_report.py
34 passed, 12 warnings in 4.33s
```

`git diff --check` passed. The existing pandas FutureWarnings in `analysis.py` remain unchanged and are outside this task's scope.

## Self Review

No Task 1 correctness issues found. The report remains self-contained and has no external Plotly script source; the vendored bundle is loaded only at report-render time.

## Review Follow-up

The external-script contract test now restores the real `_load_plotly_bundle()` implementation for its own render, while the autouse small-bundle stub remains in place for the rest of the render test suite. The assertion now checks only for an external HTML script tag:

```python
re.search(r'<script[^>]+src=["\\']https?://', rendered, flags=re.I)
```

This permits dormant URL strings within the vendored Plotly source while rejecting generated `<script src="https://...">` markup.

```text
timeout 90s pytest -q tests/test_report.py -k 'report_data or external_plotly'
7 passed, 27 deselected, 2 warnings in 1.74s

timeout 90s pytest -q tests/test_report.py
34 passed, 12 warnings in 4.24s
```
