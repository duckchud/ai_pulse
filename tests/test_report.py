import json

import pandas as pd
import pytest

from build_report import (
    _render_chart,
    _render_cooccurrence_chart,
    _render_emerging_chart,
    _render_framing_chart,
    _render_lineup_chart,
    _render_timeseries_chart,
    build_report_data,
)
from db import save_extraction
from reference_data import import_catalog


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
