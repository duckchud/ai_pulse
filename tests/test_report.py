import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import build_report as report_module
from build_report import (
    _render_chart,
    _render_cooccurrence_chart,
    _render_emerging_chart,
    _render_framing_chart,
    _render_lineup_chart,
    _render_timeseries_chart,
    build_report,
    build_report_data,
    main,
    render_report,
)
from db import save_extraction, upsert_story_candidates
from reference_data import import_catalog


_REAL_LOAD_PLOTLY_BUNDLE = report_module._load_plotly_bundle
_DASHBOARD_SOURCE = Path(report_module.__file__).with_name("report_dashboard.js")


@pytest.fixture(autouse=True)
def stub_plotly_bundle(monkeypatch):
    """Keep HTML rendering tests independent of the 4.4 MB vendored bundle."""
    monkeypatch.setattr(
        report_module,
        "_load_plotly_bundle",
        lambda: "/* plotly-2.35.2: Plotly.newPlot test marker */",
    )


def _run_dashboard(report, reject_plotly=False):
    """Run the optional Node browser contract harness when Node.js is installed."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; skipping browser contract test")
    harness = """
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const report = JSON.parse(process.argv[2]);
const rejectPlotly = process.argv[3] === "1";
const calls = [];
const elements = {};
function element(id) {
  return elements[id] || (elements[id] = {
    id,
    children: [],
    textContent: "",
    replaceChildren() { this.children = []; this.textContent = ""; },
    appendChild(child) { this.children.push(child); this.textContent = child.textContent || ""; },
  });
}
global.window = {
  Plotly: {
    newPlot(target, traces, layout, config) {
      calls.push({ id: target.id, traces, layout, config });
      return rejectPlotly ? Promise.reject(new Error("plot failed")) : Promise.resolve();
    },
  },
};
global.document = {
  readyState: "complete",
  getElementById(id) {
    return id === "report-data" ? { textContent: JSON.stringify(report) } : element(id);
  },
  createElement() { return { className: "", textContent: "" }; },
  addEventListener() {},
};
eval(source);
setTimeout(() => process.stdout.write(JSON.stringify({ calls, elements })), 0);
"""
    result = subprocess.run(
        [node, "-e", harness, str(_DASHBOARD_SOURCE), json.dumps(report), "1" if reject_plotly else "0"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _insert_story(conn, story_id="story-1", created_at="2026-07-14T10:00:00Z"):
    conn.execute(
        "INSERT INTO stories (id, source, title, created_at, created_at_i, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (story_id, "hackernews", "GPT-5 announcement", created_at, 1784023200, created_at),
    )
    conn.commit()


def _import_catalog(conn, tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([{
        "model_id": "openai:gpt:5",
        "vendor": "OpenAI",
        "family": "GPT",
        "version": "5",
        "release_source_url": "https://openai.example/gpt-5",
        "catalog_version": "v1",
        "aliases": ["GPT-5"],
    }]))
    import_catalog(conn, path)


def _populate_cli_smoke_data(conn, tmp_path):
    catalog_path = tmp_path / "smoke-catalog.json"
    catalog_path.write_text(json.dumps([
        {
            "model_id": "openai:gpt:5",
            "vendor": "OpenAI",
            "family": "GPT",
            "version": "5",
            "release_source_url": "https://openai.example/gpt-5",
            "catalog_version": "v1",
            "aliases": ["GPT-5"],
        },
        {
            "model_id": "anthropic:claude:4",
            "vendor": "Anthropic",
            "family": "Claude",
            "version": "4",
            "release_source_url": "https://anthropic.example/claude-4",
            "catalog_version": "v1",
            "aliases": ["Claude-4"],
        },
    ]))
    import_catalog(conn, catalog_path)

    stories = [
        ("story-1", "2026-07-13T10:00:00Z", 1783936800),
        ("story-2", "2026-07-13T11:00:00Z", 1783940400),
        ("story-3", "2026-07-14T10:00:00Z", 1784023200),
    ]
    for story_id, created_at, created_at_i in stories:
        conn.execute(
            "INSERT INTO stories (id, source, title, created_at, created_at_i, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                story_id,
                "hackernews",
                "GPT-5 Claude-4 comparison",
                created_at,
                created_at_i,
                created_at,
            ),
        )
        upsert_story_candidates(conn, [{
            "story_id": story_id,
            "catalog_version": "v1",
            "candidate_reason": "catalog_alias_match",
            "matched_model_ids": json.dumps(["openai:gpt:5", "anthropic:claude:4"]),
            "evidence_json": json.dumps([
                {"model_id": "openai:gpt:5", "alias": "GPT-5", "field": "title", "quote": "GPT-5"},
                {"model_id": "anthropic:claude:4", "alias": "Claude-4", "field": "title", "quote": "Claude-4"},
            ]),
            "selected_at": "2026-07-14T10:02:00Z",
        }])
        payload = {
            "relevant": True,
            "observations": [
                {"surface": "GPT-5", "evidence_verified": True, "attributes": {"stance": "positive"}},
                {"surface": "Claude-4", "evidence_verified": True, "attributes": {"stance": "skeptical"}},
            ],
            "extensions": {},
        }
        save_extraction(conn, {
            "story_id": story_id,
            "prompt_version": "smoke-v1",
            "model": "test",
            "status": "succeeded",
            "raw_response": json.dumps(payload),
            "parsed_json": json.dumps(payload),
            "input_hash": f"smoke-{story_id}",
            "input_char_count": 1,
            "input_truncated": 0,
            "error_message": None,
            "enriched_at": "2026-07-14T10:02:00Z",
        })


@pytest.fixture
def sample_report():
    return {
        "metadata": {
            "as_of": "2026-07-17T11:15:09Z",
            "extraction_as_of": "2026-07-14T10:02:00Z",
            "lookback_days": 180,
            "bucket_days": 7,
            "half_life_days": 30.0,
        },
        "summary": {
            "stories": 1,
            "catalog_models": 1,
            "successful_extractions": 1,
        },
        "timeseries": [{
            "group_label": "OpenAI/GPT",
            "bucket_start": "2026-07-01",
            "story_count": 4,
        }],
        "emerging": [],
        "lineup": [],
        "cooccurrence": [],
        "framing": [],
    }


def test_render_report_contains_reader_sections_and_no_notebook_ui(sample_report):
    html = render_report(sample_report)

    assert "AI Pulse" in html
    assert "핵심 요약" in html
    assert "모델 담론 추이" in html
    assert "기술 부록" in html
    assert "Jupyter" not in html
    assert 'id="chart-timeseries"' in html
    assert "최근 180일" in html
    assert "후보 경로 분모" in html
    assert "추출 경로 분모" in html
    assert "관측 불충분" in html


def test_render_report_embeds_html_safe_report_data(sample_report):
    sample_report["metadata"]["as_of"] = "</script><script>alert(1)</script>"

    rendered = render_report(sample_report)

    assert '<script id="report-data" type="application/json">' in rendered
    assert '"timeseries"' in rendered
    assert "</script><script>alert(1)</script>" not in rendered
    assert "<\\/script><script>alert(1)<\\/script>" in rendered


def test_render_report_has_no_external_plotly_script(sample_report, monkeypatch):
    monkeypatch.setattr(report_module, "_load_plotly_bundle", _REAL_LOAD_PLOTLY_BUNDLE)

    rendered = render_report(sample_report)

    assert not re.search(r'<script[^>]+src=["\']https?://', rendered, flags=re.I)
    assert "plotly-2.35.2" in rendered


def test_render_report_inlines_plotly_renderers(sample_report):
    rendered = render_report(sample_report)

    for renderer_name in (
        "renderTimeseries",
        "renderEmerging",
        "renderLineup",
        "renderCooccurrence",
        "renderFraming",
    ):
        assert renderer_name in rendered
    assert "Plotly.newPlot" in rendered
    assert "responsive: true" in rendered


def test_external_plotly_bundle_is_pinned():
    bundle_path = Path(report_module.__file__).parent / "vendor" / "plotly-2.35.2.min.js"

    assert bundle_path.is_file()
    assert "Plotly.newPlot" in bundle_path.read_text(encoding="utf-8")


def test_render_report_separates_stale_extraction_freshness(sample_report):
    sample_report["framing"] = [{
        "group_label": "OpenAI/GPT",
        "stance": "positive",
        "story_count": 2,
    }]

    html = render_report(sample_report)

    assert "수집/후보 기준 시각" in html
    assert "추출 기준 시각" in html
    assert "이전 extraction 기반 참고 분석" in html


def test_render_report_dashboard_has_stable_kpis_and_stale_freshness_warning(sample_report):
    sample_report["metadata"]["as_of"] = "2026-07-17T11:15:09Z"
    sample_report["metadata"]["extraction_as_of"] = "2026-07-14T10:02:00Z"
    sample_report["summary"] = {
        "stories": 1_234,
        "catalog_models": 56,
        "successful_extractions": 789,
    }

    html = render_report(sample_report)

    assert 'id="kpi-stories"' in html
    assert 'id="kpi-candidates"' in html
    assert 'id="kpi-extractions"' in html
    assert 'id="kpi-as-of"' in html
    assert 'id="insight-summary"' in html
    assert ">1,234<" in html
    assert ">56<" in html
    assert ">789<" in html
    assert "2026-07-17T11:15:09Z" in html
    assert "이전 extraction 기반 참고 분석" in html


def test_render_report_chart_containers_are_not_inside_legacy_chart_grid(sample_report):
    html = render_report(sample_report)

    for chart_id in (
        "chart-timeseries",
        "chart-emerging",
        "chart-lineup",
        "chart-cooccurrence",
        "chart-framing",
    ):
        assert f'id="{chart_id}"' in html
    assert "chart-grid" not in html


def test_render_report_has_timeseries_and_emerging_companion_tables(sample_report):
    sample_report["emerging"] = [{"group_label": "OpenAI/GPT", "mention_delta": 3}]

    html = render_report(sample_report)

    assert 'id="table-timeseries"' in html
    assert 'id="table-emerging"' in html
    assert "bucket" in html
    assert "언급 증감" in html


def test_browser_timeseries_formats_numeric_buckets_and_fills_sparse_series():
    result = _run_dashboard({
        "timeseries": [
            {"group_label": "OpenAI/GPT", "bucket_start": 1784023200, "story_count": 4},
            {"group_label": "Anthropic/Claude", "bucket_start": 1784628000, "story_count": 2},
        ],
        "emerging": [], "lineup": [], "cooccurrence": [], "framing": [],
    })

    timeseries_calls = [call for call in result["calls"] if call["id"] == "chart-timeseries"]
    traces = {trace["name"]: trace for trace in timeseries_calls[0]["traces"]}
    assert traces["OpenAI/GPT"]["x"] == ["2026-07-14", "2026-07-21"]
    assert traces["OpenAI/GPT"]["y"] == [4, 0]
    assert traces["Anthropic/Claude"]["x"] == ["2026-07-14", "2026-07-21"]
    assert traces["Anthropic/Claude"]["y"] == [0, 2]


def test_browser_framing_uses_gold_cells_without_recomputing_totals():
    result = _run_dashboard({
        "timeseries": [], "emerging": [], "lineup": [], "cooccurrence": [],
        "framing": [
            {"group_label": "OpenAI/GPT", "stance": "positive", "story_count": 2},
            {"group_label": "OpenAI/GPT", "stance": "skeptical", "story_count": 3},
        ],
    })

    framing = [call for call in result["calls"] if call["id"] == "chart-framing"][0]
    assert [trace["x"] for trace in framing["traces"]] == [[2], [3]]


def test_browser_plotly_rejection_renders_local_error_state():
    result = _run_dashboard({
        "timeseries": [{"group_label": "OpenAI/GPT", "bucket_start": 1784023200, "story_count": 4}],
        "emerging": [], "lineup": [], "cooccurrence": [], "framing": [],
    }, reject_plotly=True)

    assert result["elements"]["chart-timeseries"]["textContent"] == (
        "차트를 표시할 수 없습니다. 아래 표를 확인하세요."
    )


def test_build_report_data_records_latest_extraction_time(temporary_db, tmp_path):
    _insert_story(temporary_db)
    _import_catalog(temporary_db, tmp_path)
    payload = {"relevant": True, "observations": [], "extensions": {}}
    save_extraction(temporary_db, {
        "story_id": "story-1",
        "prompt_version": "schema-free-v2",
        "model": "test",
        "status": "succeeded",
        "raw_response": json.dumps(payload),
        "parsed_json": json.dumps(payload),
        "input_hash": "freshness-test",
        "input_char_count": 1,
        "input_truncated": 0,
        "error_message": None,
        "enriched_at": "2026-07-14T10:02:00Z",
    })

    report = build_report_data(temporary_db)

    assert report["metadata"]["extraction_as_of"] == "2026-07-14T10:02:00Z"


def test_render_report_escapes_dynamic_text(sample_report):
    sample_report["timeseries"][0]["group_label"] = "<script>alert(1)</script>"
    sample_report["metadata"]["as_of"] = "<unsafe>"

    html = render_report(sample_report)
    visible_html = re.sub(
        r'<script id="report-data" type="application/json">.*?</script>',
        "",
        html,
        flags=re.DOTALL,
    )

    assert "<unsafe>" not in visible_html
    assert "&lt;unsafe&gt;" in visible_html
    assert "<script>alert(1)</script>" not in visible_html


def test_render_report_assigns_document_unique_dashboard_ids(sample_report):
    sample_report["emerging"] = [
        {"group_label": "Anthropic/Claude", "mention_delta": 3},
    ]

    html = render_report(sample_report)
    ids = re.findall(r'\bid="([^"]+)"', html)

    assert "chart-timeseries" in ids
    assert "chart-emerging" in ids
    assert len(ids) == len(set(ids))


def test_render_report_uses_korean_capable_font_stack(sample_report):
    html = render_report(sample_report)

    assert (
        'font-family: "Malgun Gothic", "Noto Sans KR", "Noto Sans CJK KR", '
        '"Apple SD Gothic Neo", sans-serif;'
    ) in html


def test_render_report_has_no_trailing_whitespace(sample_report):
    html = render_report(sample_report)

    assert not re.search(r"[ \t]+\n", html)


def test_render_report_selects_highest_cooccurrence_pair(sample_report):
    sample_report["timeseries"] = []
    sample_report["cooccurrence"] = [
        {"family_a": "Alpha", "family_b": "Beta", "story_count": 2},
        {"family_a": "Gamma", "family_b": "Delta", "story_count": 9},
    ]

    html = render_report(sample_report)

    assert "함께 언급된 상위 조합은 Gamma와 Delta이며" in html


def test_build_report_writes_self_contained_html(temporary_db, tmp_path):
    _insert_story(temporary_db)
    _import_catalog(temporary_db, tmp_path)
    output_path = tmp_path / "analysis_report.html"

    build_report(str(tmp_path / "test.db"), str(output_path))

    html = output_path.read_text(encoding="utf-8")
    assert "AI Pulse" in html
    assert "2026-07-14T10:00:00Z" in html
    assert "해당 기준에서 관측된 결과 없음" in html


def test_main_writes_self_contained_report_for_valid_database(
    temporary_db, tmp_path, monkeypatch
):
    _populate_cli_smoke_data(temporary_db, tmp_path)
    output_path = tmp_path / "from_cli.html"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_report.py", "--db", str(tmp_path / "test.db"), "--output", str(output_path)],
    )

    main()

    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    assert output_path.stat().st_size > 10_000
    assert 'id="chart-timeseries"' in html
    assert "2026-07-14T10:00:00Z" in html
    assert not re.search(
        r"<(?:embed|iframe|img|link|object|script)\\b[^>]*https://", html,
    )
    assert "Jupyter" not in html


def test_main_exits_nonzero_with_clear_error_for_missing_database(tmp_path, monkeypatch, capsys):
    missing_db = tmp_path / "missing.db"
    monkeypatch.setattr(sys, "argv", ["build_report.py", "--db", str(missing_db)])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "보고서 생성 실패" in capsys.readouterr().err
    assert not missing_db.exists()


def test_main_exits_nonzero_with_clear_error_for_database_without_stories(
    temporary_db, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["build_report.py", "--db", str(tmp_path / "test.db")])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "stories가 없습니다" in capsys.readouterr().err


def test_build_report_data_has_stable_shape(temporary_db, tmp_path):
    _insert_story(temporary_db)
    _import_catalog(temporary_db, tmp_path)

    report = build_report_data(temporary_db, lookback_days=180, top_n=10)

    assert set(report) == {
        "metadata", "summary", "timeseries", "emerging",
        "lineup", "cooccurrence", "framing",
    }
    assert report["metadata"]["as_of"] == "2026-07-14T10:00:00Z"
    assert report["metadata"]["lookback_days"] == 180
    assert isinstance(report["summary"]["stories"], int)
    assert isinstance(report["summary"]["catalog_models"], int)
    assert isinstance(report["summary"]["successful_extractions"], int)


def test_build_report_data_requires_story_as_of(temporary_db):
    with pytest.raises(ValueError, match="stories table has no created_at value"):
        build_report_data(temporary_db)


def test_build_report_data_preserves_windows_and_counts_latest_extractions(
    temporary_db, monkeypatch
):
    _insert_story(temporary_db)
    _insert_story(temporary_db, "story-2", "2026-07-13T10:00:00Z")
    payload = json.dumps({"relevant": True, "observations": [], "extensions": {}})
    for prompt_version, enriched_at in (("v1", "2026-07-14T10:00:00Z"), ("v2", "2026-07-15T10:00:00Z")):
        save_extraction(temporary_db, {
            "story_id": "story-1", "prompt_version": prompt_version, "model": "test",
            "status": "succeeded", "raw_response": payload, "parsed_json": payload,
            "input_hash": prompt_version, "input_char_count": 1,
            "input_truncated": 0, "error_message": None, "enriched_at": enriched_at,
        })

    calls = {}

    def capture(name):
        def wrapped(*args, **kwargs):
            calls[name] = kwargs
            return pd.DataFrame()
        return wrapped

    monkeypatch.setattr("build_report.candidate_model_cooccurrence", capture("cooccurrence"))
    monkeypatch.setattr("build_report.model_framing_sentiment", capture("framing"))
    report = build_report_data(temporary_db, lookback_days=180, top_n=10)

    assert calls["cooccurrence"]["lookback_days"] == 180
    assert calls["framing"]["lookback_days"] == 180
    assert report["metadata"]["as_of"] == "2026-07-14T10:00:00Z"
    assert report["metadata"]["half_life_days"] == 30.0
    assert report["summary"] == {
        "stories": 2,
        "catalog_models": 0,
        "successful_extractions": 1,
    }


def test_chart_renderer_returns_inline_svg_for_timeseries():
    svg = _render_timeseries_chart([
        {"group_label": "OpenAI/GPT", "bucket_start": "2026-07-01", "story_count": 4},
    ])

    assert svg.lstrip().startswith("<svg")
    assert "OpenAI/GPT" in svg


def test_timeseries_chart_formats_and_bounds_unix_bucket_labels():
    svg = _render_timeseries_chart([
        {
            "group_label": "OpenAI/GPT",
            "bucket_start": 1784023200 - index * 7 * 86400,
            "story_count": index + 1,
        }
        for index in range(12)
    ])

    assert "2026-07-14" in svg
    assert "1784023200" not in svg
    assert svg.count("2026-") <= 8


def test_render_report_formats_unix_bucket_in_summary(sample_report):
    sample_report["timeseries"][0]["bucket_start"] = 1784023200

    html = render_report(sample_report)

    assert "bucket(2026-07-14)" in html
    assert "bucket(1784023200)" not in html


@pytest.mark.parametrize(
    ("renderer", "rows"),
    [
        (_render_emerging_chart, [{"group_label": "OpenAI/GPT", "mention_delta": 3}]),
        (_render_lineup_chart, [{"vendor": "OpenAI", "family": "GPT", "version": "5", "weighted_count": 2.5}]),
        (_render_cooccurrence_chart, [{"family_a": "GPT", "family_b": "Claude", "story_count": 2}]),
        (_render_framing_chart, [{"group_label": "OpenAI/GPT", "stance": "positive", "story_count": 2}]),
    ],
)
def test_chart_renderers_return_inline_svg(renderer, rows):
    svg = renderer(rows)

    assert svg.lstrip().startswith("<svg")


@pytest.mark.parametrize(
    "renderer",
    [
        _render_timeseries_chart,
        _render_emerging_chart,
        _render_lineup_chart,
        _render_cooccurrence_chart,
        _render_framing_chart,
    ],
)
def test_chart_renderers_return_korean_empty_state(renderer):
    html = renderer([])

    assert "관측된 결과 없음" in html


def test_render_chart_dispatches_report_key():
    svg = _render_chart("emerging", [{"group_label": "OpenAI/GPT", "mention_delta": 1}])

    assert svg.lstrip().startswith("<svg")


@pytest.mark.parametrize("renderer", [_render_timeseries_chart, _render_framing_chart])
def test_chart_renderer_respects_explicit_label_limit(renderer):
    if renderer is _render_timeseries_chart:
        rows = [
            {"group_label": f"Family-{index}", "bucket_start": "2026-07-01", "story_count": index}
            for index in range(3, 0, -1)
        ]
    else:
        rows = [
            {"group_label": f"Family-{index}", "stance": "neutral", "story_count": index}
            for index in range(3, 0, -1)
        ]

    svg = renderer(rows, limit=2)

    assert "Family-3" in svg
    assert "Family-2" in svg
    assert "Family-1" not in svg


def test_chart_renderer_output_is_deterministic():
    rows = [
        {"group_label": "OpenAI/GPT", "bucket_start": "2026-07-01", "story_count": 4},
    ]

    assert _render_timeseries_chart(rows) == _render_timeseries_chart(rows)
