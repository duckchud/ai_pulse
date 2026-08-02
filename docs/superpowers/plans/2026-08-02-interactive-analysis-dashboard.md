# Interactive Analysis Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace static Matplotlib SVG charts with an offline Plotly.js dashboard while preserving existing Gold analysis and freshness semantics.

**Architecture:** `build_report_data()` remains the analytical source. `build_report.py` serializes its result as HTML-safe JSON, injects a pinned local Plotly bundle and dashboard module, then renders stable chart containers plus accessible tables. Browser JavaScript only transforms supplied rows into traces and handles display interactions; it does not recompute metrics.

**Tech Stack:** Python 3.11+, existing pandas/SQLite functions, vanilla browser JavaScript, pinned Plotly.js, standalone HTML/CSS, pytest.

## Global Constraints

- Use Python 3.11+, four-space indentation, `snake_case` functions, and `UPPER_CASE` constants.
- Preserve source text, evidence quotes, unknown values, and existing Gold output semantics.
- `ai_monitor.db` is local runtime data and must never be committed.
- The generated report must work from disk without network access.
- Keep existing tables as accessible and printable companion representations.
- Do not add a server, login, live refresh, or new model inference.
- Use `apply_patch` for manual source edits and verify before claiming completion.

---

### Task 1: Add the pinned Plotly bundle and JSON contract

**Files:**
- Create: `vendor/plotly-2.35.2.min.js`
- Modify: `build_report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- `_serialize_report_data(report: dict) -> str` returns JSON safe for an inert script element.
- `_load_plotly_bundle() -> str` reads the pinned local bundle.
- HTML contains one `id="report-data"` element and one inline Plotly bundle marker.

- [ ] **Step 1: Write the failing tests**

```python
def test_render_report_embeds_html_safe_report_data(sample_report):
    rendered = render_report(sample_report)
    assert '<script id="report-data" type="application/json">' in rendered
    assert '"timeseries"' in rendered


def test_render_report_has_no_external_plotly_script(sample_report):
    rendered = render_report(sample_report)
    assert 'src="https://' not in rendered
    assert "plotly-2.35.2" in rendered
```

- [ ] **Step 2: Verify the tests fail**

Run `pytest -q tests/test_report.py -k 'report_data or external_plotly'`. Expected: FAIL because the current renderer emits neither element.

- [ ] **Step 3: Implement the minimal contract**

Add `json` import and implement:

```python
def _serialize_report_data(report: dict) -> str:
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    return payload.replace("</", "<\\/")


def _load_plotly_bundle() -> str:
    return (Path(__file__).parent / "vendor" / "plotly-2.35.2.min.js").read_text(
        encoding="utf-8"
    )
```

Inject the inert JSON script and inline bundle from `render_report()`. Keep visible values HTML-escaped.

- [ ] **Step 4: Pin the bundle**

```bash
mkdir -p vendor
curl -fsSL https://cdn.plot.ly/plotly-2.35.2.min.js -o vendor/plotly-2.35.2.min.js
test -s vendor/plotly-2.35.2.min.js
rg -q 'Plotly\.newPlot' vendor/plotly-2.35.2.min.js
```

The CDN is only used to obtain the checked-in artifact; generated HTML must not reference it.

- [ ] **Step 5: Verify and commit**

Run `pytest -q tests/test_report.py -k 'report_data or external_plotly'`. Expected: PASS.

```bash
git add build_report.py tests/test_report.py vendor/plotly-2.35.2.min.js
git commit -m "feat: embed plotly report data contract"
```

### Task 2: Build the dashboard shell and KPI layout

**Files:**
- Modify: `build_report.py`
- Test: `tests/test_report.py`

**Interfaces:** `render_report(report: dict) -> str` emits stable IDs `kpi-stories`, `kpi-candidates`, `kpi-extractions`, `kpi-as-of`, `insight-summary`, `chart-timeseries`, `chart-emerging`, `chart-lineup`, `chart-cooccurrence`, and `chart-framing`.

- [ ] **Step 1: Write failing layout tests**

Assert all IDs, KPI values, and the stale warning occur for a stale fixture. Assert the five chart containers are not inside the old `.chart-grid` wrapper.

- [ ] **Step 2: Verify failure**

Run `pytest -q tests/test_report.py -k 'dashboard or chart_container'`. Expected: FAIL because the current report has SVG figures and no stable dashboard IDs.

- [ ] **Step 3: Implement the shell**

Replace figure markup with semantic sections and stable containers. Keep each current table beneath its related chart. Add responsive CSS: full-width evidence charts, compact KPI cards, one-column mobile layout, mobile overflow, and print rules. Put findings in adjacent text blocks.

- [ ] **Step 4: Verify and commit**

Run `pytest -q tests/test_report.py -k 'dashboard or chart_container'`. Expected: PASS.

```bash
git add build_report.py tests/test_report.py
git commit -m "feat: add interactive report dashboard shell"
```

### Task 3: Implement browser-side Plotly renderers

**Files:**
- Create: `report_dashboard.js`
- Modify: `build_report.py`
- Test: `tests/test_report.py`

**Interfaces:** `window.renderAiPulseDashboard()` reads `#report-data`; renderer functions `renderTimeseries`, `renderEmerging`, `renderLineup`, `renderCooccurrence`, and `renderFraming` accept rows, a DOM element, and options. Each returns safely for empty or malformed rows.

- [ ] **Step 1: Write the failing renderer test**

Assert generated HTML contains all five function names, `Plotly.newPlot`, and `responsive: true`.

- [ ] **Step 2: Verify failure**

Run `pytest -q tests/test_report.py -k plotly_renderer`. Expected: FAIL because no browser renderer exists.

- [ ] **Step 3: Implement `report_dashboard.js`**

Add `readReportData()`, shared responsive `baseLayout()`, and five renderers. Group and sort timeseries by family and bucket; use horizontal bars for emerging and lineup; use a heatmap only when cooccurrence has enough distinct nodes and otherwise use ranked bars; use stacked stance bars for framing. Add `renderEmptyState()` and local `renderErrorState()`.

Use `config: {responsive: true, displaylogo: false, modeBarButtonsToRemove: [...]}`. The module may use only the embedded `Plotly` global and `reportData`; it must not fetch URLs, access SQLite, or recalculate Gold metrics.

- [ ] **Step 4: Inject and verify**

Inline `report_dashboard.js` after report JSON and Plotly. Run `pytest -q tests/test_report.py -k plotly_renderer`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add build_report.py report_dashboard.js tests/test_report.py
git commit -m "feat: render report charts with plotly"
```

### Task 4: Add accessibility, empty states, and print behavior

**Files:**
- Modify: `report_dashboard.js`
- Modify: `build_report.py`
- Test: `tests/test_report.py`

**Interfaces:** Each chart container has `role="img"`, a Korean accessible label, and a linked table ID. Empty, malformed, and stale states work without hiding the companion table.

- [ ] **Step 1: Write failing fallback tests**

Render an empty report and assert every chart ID, Korean empty-state text, table headers, and stale warning remain. Assert no external `src=` or `href=` resource is present.

- [ ] **Step 2: Verify failure**

Run `pytest -q tests/test_report.py -k 'empty or accessibility or offline'`. Expected: FAIL until fallback and accessibility attributes exist.

- [ ] **Step 3: Implement fallback behavior**

Add `aria-label`, linked table references, local chart errors, no-JS empty text, and print CSS that hides modebars while preserving charts and tables. Keep all CSS, Plotly, dashboard JS, report JSON, and existing markup inline.

- [ ] **Step 4: Verify and commit**

Run `pytest -q tests/test_report.py -k 'empty or accessibility or offline'`. Expected: PASS.

```bash
git add build_report.py report_dashboard.js tests/test_report.py
git commit -m "feat: add accessible report chart fallbacks"
```

### Task 5: Rebuild, browser QA, and regression verification

**Files:**
- Modify: `analysis_report.html`
- Test: `tests/test_report.py`

- [ ] **Step 1: Rebuild**

Run `python3 build_report.py --db ai_monitor.db --output analysis_report.html`.

- [ ] **Step 2: Run all tests**

Run `pytest -q`. Expected: all tests pass.

- [ ] **Step 3: Run artifact checks**

```bash
git diff --check
! rg -n 'src="https?://|href="https?://' analysis_report.html
rg -n 'report-data|Plotly\.newPlot|chart-timeseries|chart-framing' analysis_report.html
```

- [ ] **Step 4: Perform browser checks**

Open `analysis_report.html` directly with network disabled. Verify desktop KPI/chart rendering, hover values, legend toggles, mobile no-overflow/no-overlap, empty-state tables, stale framing warning, and print modebar removal.

- [ ] **Step 5: Commit and push**

```bash
git add analysis_report.html
git commit -m "docs: publish interactive analysis report"
git push origin main
git status --short
git log -1 --oneline --decorate
```

Expected: `origin/main` points to the final report commit, `ai_monitor.db` is not staged, and the pre-existing untracked `analysis.html` remains untouched.
