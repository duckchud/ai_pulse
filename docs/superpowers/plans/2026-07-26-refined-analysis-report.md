# Refined Analysis Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible Python report builder that turns the current AI Pulse Gold outputs into a self-contained Korean `analysis_report.html` for stakeholder and technical readers.

**Architecture:** `build_report.py` will load the local SQLite database, call existing read-only Gold functions, normalize their outputs into a small report-data contract, render inline SVG charts with matplotlib, and compose one standalone HTML document. The notebook remains the reproducible analysis surface; the new HTML is a separate reader-facing surface.

**Tech Stack:** Python 3.11+, SQLite, pandas, existing `analysis.py` Gold functions, matplotlib inline SVG, HTML/CSS, pytest.

## Global Constraints

- Use the existing `analysis.py` functions; do not duplicate Gold aggregation logic in the report builder.
- Use `AS_OF = MAX(stories.created_at)` and UTC timestamps.
- Use a 180-day lookback for timeseries, co-occurrence, and framing; use the existing lineup function's full candidate history with a 30-day half-life; use a 24-hour versus prior 24-hour comparison for emerging models.
- Keep candidate-path results separate from extraction-path results and label their denominators in the report.
- Keep the final HTML self-contained: no Jupyter UI, external CDN, server, API call, or database access at browser-open time.
- Preserve unresolved/open-world labels and show empty results as an explicit no-observation state.
- Do not commit `ai_monitor.db`, caches, logs, or generated temporary files.
- Keep visible report copy in Korean while retaining exact technical identifiers in the appendix.

---

### Task 1: Define the report-data contract and metadata loader

**Files:**
- Create: `build_report.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: `sqlite3.Connection`, `analysis.py` Gold functions, and the existing `config`/`db` modules.
- Produces: `build_report_data(conn, lookback_days=180, top_n=10) -> dict` with keys `metadata`, `summary`, `timeseries`, `emerging`, `lineup`, `cooccurrence`, and `framing`.

- [ ] **Step 1: Write failing tests for metadata and report-data shape**

Add `from build_report import build_report_data` to the test module. Use the existing `temporary_db` fixture. The tests should create one story, import a small catalog, and assert that the returned dictionary has all required keys, `metadata["as_of"]` equals the latest story timestamp, and `summary` contains integer counts for `stories`, `catalog_models`, and `successful_extractions`.

```python
def test_build_report_data_has_stable_shape(temporary_db, tmp_path):
    report = build_report_data(temporary_db, lookback_days=180, top_n=10)

    assert set(report) == {
        "metadata", "summary", "timeseries", "emerging",
        "lineup", "cooccurrence", "framing",
    }
    assert report["metadata"]["lookback_days"] == 180
    assert isinstance(report["summary"]["stories"], int)
    assert isinstance(report["summary"]["catalog_models"], int)
    assert isinstance(report["summary"]["successful_extractions"], int)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_report.py::test_build_report_data_has_stable_shape -q`

Expected: FAIL because `build_report.py` and `build_report_data` do not exist.

- [ ] **Step 3: Implement the loader and Gold calls**

Implement these internal helpers in `build_report.py`:

```python
def _as_of(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(created_at) AS as_of FROM stories").fetchone()
    if not row or row[0] is None:
        raise ValueError("stories table has no created_at value")
    return row[0]


def _frame_records(frame: pd.DataFrame, limit: int) -> list[dict]:
    if frame.empty:
        return []
    return frame.head(limit).where(pd.notna(frame), None).to_dict("records")


def build_report_data(
    conn: sqlite3.Connection,
    lookback_days: int = 180,
    top_n: int = 10,
) -> dict:
    as_of = _as_of(conn)
    timeseries = candidate_mention_timeseries(
        conn, as_of=as_of, group_level="family", bucket_days=7,
        lookback_days=lookback_days,
    )
    emerging = candidate_emerging_models(
        conn, as_of=as_of, group_level="family", top_n=top_n,
    )
    lineup = candidate_model_lineup(conn, as_of=as_of, half_life_days=30.0)
    cooccurrence = candidate_model_cooccurrence(
        conn, as_of=as_of, group_level="family",
        min_count=2, lookback_days=lookback_days,
    )
    framing = model_framing_sentiment(
        conn, as_of=as_of, group_level="family",
        lookback_days=lookback_days,
    )
    summary = _load_summary_counts(conn)
    return {
        "metadata": {"as_of": as_of, "lookback_days": lookback_days,
                     "bucket_days": 7, "half_life_days": 30.0},
        "summary": summary,
        "timeseries": _frame_records(timeseries, top_n * 6),
        "emerging": _frame_records(emerging, top_n),
        "lineup": _frame_records(lineup, top_n),
        "cooccurrence": _frame_records(cooccurrence, top_n),
        "framing": _frame_records(framing, top_n * 4),
    }
```

`_load_summary_counts` must use SQL counts only: `stories`, `model_catalog`, and distinct latest successful `story_extractions` through `db.latest_successful_extractions`. It must not count extraction rows as stories.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/test_report.py::test_build_report_data_has_stable_shape -q`

Expected: PASS.

- [ ] **Step 5: Add empty-database and window-contract tests**

Test that an empty `stories` table raises `ValueError`, that `candidate_model_cooccurrence` and `model_framing_sentiment` receive `lookback_days=180`, and that the returned `metadata` preserves `as_of` and `half_life_days`.

- [ ] **Step 6: Run the report-data test file**

Run: `pytest tests/test_report.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the data contract**

```bash
git add build_report.py tests/test_report.py
git commit -m "feat: add report data contract"
```

### Task 2: Add report chart renderers

**Files:**
- Modify: `build_report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: the `dict` returned by `build_report_data`.
- Produces: `_render_chart(report_key: str, rows: list[dict]) -> str`, returning inline SVG markup or a Korean empty-state paragraph.

- [ ] **Step 1: Write failing chart-render tests**

```python
def test_chart_renderer_returns_inline_svg_for_timeseries():
    svg = _render_timeseries_chart([
        {"group_label": "OpenAI/GPT", "bucket_start": "2026-07-01", "story_count": 4},
    ])
    assert svg.lstrip().startswith("<svg")
    assert "OpenAI/GPT" in svg


def test_chart_renderer_returns_empty_state():
    html = _render_timeseries_chart([])
    assert "관측된 결과 없음" in html
```

- [ ] **Step 2: Run the focused chart tests to verify they fail**

Run: `pytest tests/test_report.py -k chart -q`

Expected: FAIL because the chart helpers do not exist.

- [ ] **Step 3: Implement four focused matplotlib renderers**

Implement separate helpers with a shared figure setup and `io.BytesIO`/SVG output. Add these exact private interfaces:

```python
def _render_timeseries_chart(rows: list[dict]) -> str:
    """Return inline SVG or the Korean empty-state HTML."""

def _render_emerging_chart(rows: list[dict]) -> str:
    """Return inline SVG or the Korean empty-state HTML."""

def _render_lineup_chart(rows: list[dict]) -> str:
    """Return inline SVG or the Korean empty-state HTML."""

def _render_cooccurrence_chart(rows: list[dict]) -> str:
    """Return inline SVG or the Korean empty-state HTML."""

def _render_framing_chart(rows: list[dict]) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
```

Use line charts for weekly timeseries, zero-based horizontal bars for absolute counts, and stacked horizontal bars for stance. Limit labels to the report `top_n`. Put titles and date/denominator subtitles in HTML around the SVG so the chart itself remains legible. Use an explicit Korean empty state when rows are empty. Do not use networkx or a force-directed graph in the report; a ranked pair bar chart is easier to audit and works on mobile.

- [ ] **Step 4: Run chart tests and full existing tests**

Run: `pytest tests/test_report.py tests/test_analysis.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the chart layer**

```bash
git add build_report.py tests/test_report.py
git commit -m "feat: add report chart renderers"
```

### Task 3: Compose the standalone Korean HTML report

**Files:**
- Modify: `build_report.py`
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: report data and chart SVG strings.
- Produces: `render_report(report: dict) -> str` and `build_report(db_path: str, output_path: str) -> None`.

- [ ] **Step 1: Write failing HTML contract tests**

```python
@pytest.fixture
def sample_report():
    return {
        "metadata": {"as_of": "2026-07-17T11:15:09Z", "lookback_days": 180,
                     "bucket_days": 7, "half_life_days": 30.0},
        "summary": {"stories": 1, "catalog_models": 1,
                     "successful_extractions": 1},
        "timeseries": [{"group_label": "OpenAI/GPT", "bucket_start": "2026-07-01",
                        "story_count": 4}],
        "emerging": [], "lineup": [], "cooccurrence": [], "framing": [],
    }


def test_render_report_contains_reader_sections_and_no_notebook_ui(sample_report):
    html = render_report(sample_report)

    assert "AI Pulse" in html
    assert "핵심 요약" in html
    assert "모델 담론 추이" in html
    assert "기술 부록" in html
    assert "Jupyter" not in html
    assert "<svg" in html
    assert "최근 180일" in html
```

- [ ] **Step 2: Run the HTML test to verify it fails**

Run: `pytest tests/test_report.py::test_render_report_contains_reader_sections_and_no_notebook_ui -q`

Expected: FAIL because `render_report` does not exist.

- [ ] **Step 3: Implement the report layout and copy**

Create a self-contained document with:

1. Header: title, subtitle, `as_of`, lookback, and source scope.
2. KPI strip: story count, catalog model count, successful extraction count.
3. Executive summary: 3-4 generated observations based on non-empty results; otherwise “관측 불충분”.
4. Evidence sections: timeseries/emerging, lineup, co-occurrence, framing. Each section must include a finding-oriented heading, one explanatory paragraph, chart, and denominator note.
5. Limitations block: keyword coverage, candidate/extraction denominator split, catalog-bound recall, lexical matching, evidence verification, and engagement-vs-sentiment distinction.
6. Technical appendix: exact Gold function names, UTC/time windows, version metadata, regeneration command, and manual-review status.

Keep all CSS inline. Use semantic `main`, `section`, `figure`, `figcaption`, `table`, and `details` elements. Use responsive CSS with a single-column layout below 760px and horizontal scrolling for detail tables. Escape all dynamic text with `html.escape` before interpolation.

- [ ] **Step 4: Add the CLI entry point**

Implement:

```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="ai_monitor.db")
    parser.add_argument("--output", default="analysis_report.html")
    args = parser.parse_args()
    build_report(args.db, args.output)
```

Open the database through `db.connect`, call `build_report_data`, write UTF-8 HTML, and close the connection in a `finally` block. Return a non-zero CLI failure with a clear message when the DB is missing or has no stories.

- [ ] **Step 5: Run HTML tests**

Run: `pytest tests/test_report.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the report renderer**

```bash
git add build_report.py tests/test_report.py
git commit -m "feat: render standalone Korean analysis report"
```

### Task 4: Generate, inspect, and document the final artifact

**Files:**
- Create: `analysis_report.html`
- Modify: `README.md`
- Modify: `tests/test_report.py` only if an uncovered regression is found during validation

**Interfaces:**
- Consumes: `build_report.py` CLI and current local `ai_monitor.db`.
- Produces: a validated static HTML snapshot and documented regeneration command.

- [ ] **Step 1: Add a CLI smoke test with a temporary output path**

Run the real builder against a temporary copy/path and assert the output exists, is larger than 10 KB, contains `<svg`, contains the current `AS_OF`, and contains no `https://` resource tags or Jupyter markers.

- [ ] **Step 2: Run the builder against the current database**

Run:

```bash
python3 build_report.py --db ai_monitor.db --output analysis_report.html
```

Expected: the command succeeds and writes `analysis_report.html` at the repository root.

- [ ] **Step 3: Inspect the rendered HTML at desktop and narrow widths**

Open the local file in a browser and check the first viewport, all chart labels, the technical appendix, and the responsive table behavior. Confirm that no chart is blank, clipped, or dependent on browser network access.

- [ ] **Step 4: Add README usage instructions**

Document the exact command above and explain that `analysis_report.html` is a static snapshot generated from the local DB, while `analysis.ipynb` remains the exploratory/reproducible notebook.

- [ ] **Step 5: Run the complete verification suite**

Run:

```bash
pytest -q
python3 build_report.py --db ai_monitor.db --output analysis_report.html
git diff --check
```

Expected: all tests pass, HTML generation succeeds, and `git diff --check` is clean.

- [ ] **Step 6: Commit the final artifact and documentation**

```bash
git add build_report.py analysis_report.html README.md tests/test_report.py
git commit -m "docs: publish refined Korean analysis report"
```
