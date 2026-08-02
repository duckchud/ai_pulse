# Task 2 Report: Interactive Dashboard Shell and KPI Layout

## Delivered

- Replaced the static inline-SVG figure placements in `render_report()` with five full-width semantic Plotly target containers: `chart-timeseries`, `chart-emerging`, `chart-lineup`, `chart-cooccurrence`, and `chart-framing`.
- Added stable KPI and summary IDs: `kpi-stories`, `kpi-candidates`, `kpi-extractions`, `kpi-as-of`, and `insight-summary`.
- Retained the inline `report-data` JSON and pinned Plotly bundle from Task 1 without modification.
- Kept the lineup, cooccurrence, and framing tables directly below their respective chart containers, and retained the stale extraction freshness warning.
- Added responsive dashboard CSS for four compact KPI cards on wide displays, a one-column mobile KPI layout, full-width chart targets, horizontal table/chart overflow, and print-specific sizing.

## Test-First Evidence

Added focused layout tests before production changes:

- `test_render_report_dashboard_has_stable_kpis_and_stale_freshness_warning`
- `test_render_report_chart_containers_are_not_inside_legacy_chart_grid`

Initial verification:

```text
pytest -q tests/test_report.py -k 'dashboard or chart_container'
2 failed, 34 deselected
```

The failures were the expected missing stable IDs and missing chart containers in the old SVG layout.

After implementation:

```text
pytest -q tests/test_report.py -k 'dashboard or chart_container'
2 passed, 34 deselected

pytest -q tests/test_report.py
36 passed, 12 warnings

pytest -q
146 passed, 12 warnings
```

The warnings are existing pandas `FutureWarning` messages from `analysis.py`; this task did not alter that module.

## Self Review

- Confirmed all nine required stable IDs are emitted once in the report document.
- Confirmed `.chart-grid` is removed and no chart target is nested in that retired wrapper.
- Confirmed stale framing text remains rendered before the framing chart and table.
- Confirmed the Task 1 JSON script and inline Plotly bundle remain present and self-contained.
- Ran `git diff --check` with no whitespace errors.
