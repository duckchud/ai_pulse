"""Build report data and render a standalone Korean analysis report."""

import argparse
import html
import io
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from analysis import (
    candidate_emerging_models,
    candidate_mention_timeseries,
    candidate_model_cooccurrence,
    candidate_model_lineup,
    model_framing_sentiment,
)
from config import COLLECTION_QUERY_VERSION, PROMPT_VERSION
from db import connect, latest_successful_extractions


_EMPTY_CHART = '<p class="chart-empty">해당 기준에서 관측된 결과 없음</p>'
_KOREAN_FONT_CSS = (
    '"Malgun Gothic", "Noto Sans KR", "Noto Sans CJK KR", '
    '"Apple SD Gothic Neo", sans-serif'
)
_MATPLOTLIB_SANS_SERIF = [
    "Malgun Gothic",
    "Noto Sans KR",
    "Noto Sans CJK KR",
    "Apple SD Gothic Neo",
    "DejaVu Sans",
]
_CHART_RENDERERS = {
    "timeseries": lambda rows, limit=None: _render_timeseries_chart(rows, limit=limit),
    "emerging": lambda rows, limit=None: _render_emerging_chart(rows, limit=limit),
    "lineup": lambda rows, limit=None: _render_lineup_chart(rows, limit=limit),
    "cooccurrence": lambda rows, limit=None: _render_cooccurrence_chart(rows, limit=limit),
    "framing": lambda rows, limit=None: _render_framing_chart(rows, limit=limit),
}


def _as_of(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(created_at) AS as_of FROM stories").fetchone()
    if not row or row[0] is None:
        raise ValueError("stories table has no created_at value")
    return row[0]


def _frame_records(frame: pd.DataFrame, limit: int) -> list[dict]:
    if frame.empty:
        return []
    return frame.head(limit).where(pd.notna(frame), None).to_dict("records")


def _cooccurrence_pair_label(row: dict, suffix: str) -> str:
    return "/".join(
        str(row.get(f"{field}_{suffix}"))
        for field in ("vendor", "family", "version")
        if row.get(f"{field}_{suffix}") not in (None, "")
    )


def _cooccurrence_sort_key(row: dict) -> tuple:
    try:
        story_count = float(row.get("story_count", 0))
        if story_count != story_count:
            story_count = 0.0
    except (TypeError, ValueError):
        story_count = 0.0
    return (
        -story_count,
        _cooccurrence_pair_label(row, "a"),
        _cooccurrence_pair_label(row, "b"),
    )


def _sort_cooccurrence_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=_cooccurrence_sort_key)


def _load_summary_counts(conn: sqlite3.Connection) -> dict[str, int]:
    stories = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    catalog_models = conn.execute("SELECT COUNT(*) FROM model_catalog").fetchone()[0]
    successful_extractions = len(latest_successful_extractions(conn))
    return {
        "stories": int(stories),
        "catalog_models": int(catalog_models),
        "successful_extractions": int(successful_extractions),
    }


def _distinct_values(conn: sqlite3.Connection, query: str) -> list[str]:
    return [str(row[0]) for row in conn.execute(query) if row[0] is not None]


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
    cooccurrence_rows = _sort_cooccurrence_rows(
        _frame_records(cooccurrence, len(cooccurrence))
    )[:top_n]
    return {
        "metadata": {
            "as_of": as_of,
            "lookback_days": lookback_days,
            "bucket_days": 7,
            "half_life_days": 30.0,
            "top_n": top_n,
            "collection_query_versions": _distinct_values(
                conn,
                "SELECT DISTINCT collection_query_version FROM stories "
                "ORDER BY collection_query_version",
            ),
            "prompt_versions": _distinct_values(
                conn,
                "SELECT DISTINCT prompt_version FROM story_extractions "
                "ORDER BY prompt_version",
            ),
            "catalog_versions": _distinct_values(
                conn,
                "SELECT DISTINCT catalog_version FROM model_catalog "
                "ORDER BY catalog_version",
            ),
        },
        "summary": summary,
        "timeseries": _frame_records(timeseries, top_n * 6),
        "emerging": _frame_records(emerging, top_n),
        "lineup": _frame_records(lineup, top_n),
        "cooccurrence": cooccurrence_rows,
        "framing": _frame_records(framing, top_n * 4),
    }


def _empty_chart() -> str:
    return _EMPTY_CHART


def _normalize_svg(svg: str, chart_key: str) -> str:
    """Remove render-time SVG metadata and normalize generated references."""
    svg = re.sub(r"<dc:date>.*?</dc:date>\n", "", svg)
    ids = re.findall(r'id="([^"]+)"', svg)
    replacements = {
        old: f"{chart_key}_svg_id_{index}"
        for index, old in enumerate(dict.fromkeys(ids))
    }
    for old, new in replacements.items():
        svg = svg.replace(f'id="{old}"', f'id="{new}"')
        svg = svg.replace(f'url(#{old})', f'url(#{new})')
        svg = svg.replace(f'href="#{old}"', f'href="#{new}"')
    return re.sub(r"[ \t]+\n", "\n", svg)


def _svg_from_figure(fig, chart_key: str) -> str:
    """Serialize a figure as standalone inline SVG without an XML preamble."""
    buffer = io.BytesIO()
    with matplotlib.rc_context({
        "svg.fonttype": "none",
        "font.family": "sans-serif",
        "font.sans-serif": _MATPLOTLIB_SANS_SERIF,
    }):
        fig.savefig(buffer, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    svg = buffer.getvalue().decode("utf-8")
    return _normalize_svg(svg[svg.find("<svg") :], chart_key)


def _figure(height: float = 3.8):
    return plt.subplots(figsize=(9.5, height), constrained_layout=True)


def _bucket_label(value) -> str:
    """Format persisted Unix bucket starts as readable UTC dates."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return str(value)
    if timestamp < 1_000_000_000:
        return str(value)
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def _render_timeseries_chart(rows: list[dict], limit: int | None = None) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
    if not rows:
        return _empty_chart()

    frame = pd.DataFrame(rows).copy()
    frame["bucket_label"] = frame["bucket_start"].map(_bucket_label)
    labels = sorted(frame["bucket_label"].unique())
    groups = list(dict.fromkeys(frame["group_label"].astype(str)))
    if limit is not None:
        groups = groups[:limit]
    fig, ax = _figure(max(3.8, 2.6 + len(groups) * 0.12))
    x = range(len(labels))
    for group in groups:
        group_frame = frame[frame["group_label"].astype(str) == group].copy()
        values = group_frame.assign(
            bucket_label=group_frame["bucket_label"].astype(str)
        ).set_index("bucket_label")["story_count"].reindex(labels, fill_value=0)
        ax.plot(x, values.tolist(), marker="o", linewidth=1.8, label=group)
    tick_count = min(8, len(labels))
    tick_positions = [
        round(index * (len(labels) - 1) / max(1, tick_count - 1))
        for index in range(tick_count)
    ]
    ax.set_xticks(tick_positions, [labels[index] for index in tick_positions], rotation=35, ha="right")
    ax.set_ylabel("Stories")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    return _svg_from_figure(fig, "timeseries")


def _render_horizontal_bars(labels, values, xlabel: str, chart_key: str) -> str:
    fig, ax = _figure(max(3.2, 1.4 + len(labels) * 0.42))
    positions = list(range(len(labels)))
    ax.barh(positions, values, color="#386cb0")
    ax.set_yticks(positions, labels)
    ax.set_xlabel(xlabel)
    ax.invert_yaxis()
    if min(values, default=0) >= 0:
        ax.set_xlim(left=0)
    return _svg_from_figure(fig, chart_key)


def _render_emerging_chart(rows: list[dict], limit: int | None = None) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
    if not rows:
        return _empty_chart()
    frame = pd.DataFrame(rows)
    if limit is not None:
        frame = frame.head(limit)
    return _render_horizontal_bars(
        frame["group_label"].astype(str).tolist(),
        frame["mention_delta"].astype(float).tolist(),
        "Story delta",
        "emerging",
    )


def _render_lineup_chart(rows: list[dict], limit: int | None = None) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
    if not rows:
        return _empty_chart()
    frame = pd.DataFrame(rows).copy()
    if limit is not None:
        frame = frame.head(limit)
    frame["label"] = frame.apply(
        lambda row: "/".join(
            str(value)
            for value in (row.get("vendor"), row.get("family"), row.get("version"))
            if pd.notna(value) and value not in (None, "")
        ),
        axis=1,
    )
    return _render_horizontal_bars(
        frame["label"].tolist(),
        frame["weighted_count"].astype(float).tolist(),
        "Weighted stories",
        "lineup",
    )


def _render_cooccurrence_chart(rows: list[dict], limit: int | None = None) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
    if not rows:
        return _empty_chart()
    frame = pd.DataFrame(rows).copy()
    if limit is not None:
        frame = frame.head(limit)
    frame["label"] = frame.apply(
        lambda row: " + ".join(
            "/".join(
                str(value)
                for value in (
                    row.get(f"vendor_{suffix}"),
                    row.get(f"family_{suffix}"),
                    row.get(f"version_{suffix}"),
                )
                if pd.notna(value) and value not in (None, "")
            )
            for suffix in ("a", "b")
        ),
        axis=1,
    )
    return _render_horizontal_bars(
        frame["label"].tolist(),
        frame["story_count"].astype(float).tolist(),
        "Shared stories",
        "cooccurrence",
    )


def _render_framing_chart(rows: list[dict], limit: int | None = None) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
    if not rows:
        return _empty_chart()
    frame = pd.DataFrame(rows).copy()
    totals = frame.groupby("group_label")["story_count"].sum().sort_values(ascending=False)
    groups = totals.index.tolist()
    if limit is not None:
        groups = groups[:limit]
    stances = list(dict.fromkeys(frame["stance"].astype(str)))
    fig, ax = _figure(max(3.8, 2.6 + len(groups) * 0.34))
    positions = list(range(len(groups)))
    left = [0.0] * len(groups)
    colors = ["#386cb0", "#f28e2b", "#59a14f", "#b07aa1", "#79706e"]
    for index, stance in enumerate(stances):
        values = [
            float(
                frame[
                    (frame["group_label"] == group)
                    & (frame["stance"].astype(str) == stance)
                ]["story_count"].sum()
            )
            for group in groups
        ]
        ax.barh(positions, values, left=left, label=stance, color=colors[index % len(colors)])
        left = [current + value for current, value in zip(left, values)]
    ax.set_yticks(positions, groups)
    ax.set_xlabel("Stories")
    ax.set_xlim(left=0)
    ax.invert_yaxis()
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    return _svg_from_figure(fig, "framing")


def _render_chart(
    report_key: str, rows: list[dict], limit: int | None = None
) -> str:
    """Render a report section's chart from the stable report-data key."""
    try:
        renderer = _CHART_RENDERERS[report_key]
    except KeyError as exc:
        raise ValueError(f"unknown report chart key: {report_key!r}") from exc
    return renderer(rows, limit=limit)


def _text(value, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return html.escape(str(value))


def _number(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _decimal(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _metadata_values(metadata: dict, key: str, fallback: str) -> str:
    values = metadata.get(key)
    if not values:
        return html.escape(fallback)
    if not isinstance(values, list):
        values = [values]
    return ", ".join(_text(value) for value in values)


def _report_table(headers: list[str], rows: list[list[str]]) -> str:
    header_cells = "".join(f"<th scope=\"col\">{html.escape(label)}</th>" for label in headers)
    if not rows:
        return '<p class="table-empty">세부 표에 표시할 관측값이 없습니다.</p>'
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<div class="table-scroll"><table><thead><tr>'
        f"{header_cells}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _lineup_table(rows: list[dict]) -> str:
    return _report_table(
        ["공급사", "family", "version", "누적 story", "최신성 가중치"],
        [[
            _text(row.get("vendor")),
            _text(row.get("family")),
            _text(row.get("version")),
            _number(row.get("story_count")),
            _decimal(row.get("weighted_count")),
        ] for row in rows],
    )


def _cooccurrence_table(rows: list[dict]) -> str:
    return _report_table(
        ["family A", "family B", "함께 언급된 story"],
        [[
            _text(row.get("family_a")),
            _text(row.get("family_b")),
            _number(row.get("story_count")),
        ] for row in rows],
    )


def _framing_table(rows: list[dict]) -> str:
    return _report_table(
        ["모델 family", "stance 원문 label", "story"],
        [[
            _text(row.get("group_label")),
            _text(row.get("stance")),
            _number(row.get("story_count")),
        ] for row in rows],
    )


def _summary_observations(report: dict) -> list[str]:
    observations = []
    timeseries = report.get("timeseries", [])
    if timeseries:
        latest_bucket = max(str(row.get("bucket_start", "")) for row in timeseries)
        latest_rows = [
            row for row in timeseries if str(row.get("bucket_start", "")) == latest_bucket
        ]
        leading = max(latest_rows, key=lambda row: row.get("story_count", 0))
        observations.append(
            f"가장 최근 주간 bucket({_text(_bucket_label(latest_bucket))})에서 "
            f"{_text(leading.get('group_label'))} family가 "
            f"{_number(leading.get('story_count'))}건으로 가장 많이 관측되었습니다."
        )
    emerging = report.get("emerging", [])
    if emerging:
        leading = emerging[0]
        observations.append(
            f"24시간 비교에서 {_text(leading.get('group_label'))}의 언급 증감은 "
            f"{_number(leading.get('mention_delta'))}건입니다. 이는 단기 변화이며 지속 추세를 뜻하지 않습니다."
        )
    lineup = report.get("lineup", [])
    if lineup:
        leading = lineup[0]
        label = "/".join(
            _text(leading.get(key))
            for key in ("vendor", "family", "version")
            if leading.get(key) not in (None, "")
        ) or "-"
        observations.append(
            f"최신성 가중 라인업에서는 {label}의 가중 story 합계가 "
            f"{_decimal(leading.get('weighted_count'))}입니다."
        )
    cooccurrence = _sort_cooccurrence_rows(report.get("cooccurrence", []))
    if cooccurrence:
        leading = cooccurrence[0]
        observations.append(
            f"함께 언급된 상위 조합은 {_text(leading.get('family_a'))}와 "
            f"{_text(leading.get('family_b'))}이며, 같은 story에 "
            f"{_number(leading.get('story_count'))}번 나타났습니다."
        )
    framing = report.get("framing", [])
    if framing:
        leading = framing[0]
        observations.append(
            f"evidence-verified 추출 표본에서 {_text(leading.get('group_label'))}의 "
            f"{_text(leading.get('stance'))} framing이 "
            f"{_number(leading.get('story_count'))}건 관측되었습니다."
        )
    if not observations:
        return ["관측 불충분: 현재 결과로는 모델 담론의 방향이나 비교를 요약할 수 없습니다."]
    if len(observations) < 3:
        observations.append(
            "관측 불충분: 서로 다른 분석 경로에서 나온 독립 관측이 세 개 미만이므로 "
            "방향성이나 비교 결론을 확대 해석하지 않습니다."
        )
    return observations[:4]


def _report_figure(chart: str, caption: str) -> str:
    return f"<figure>{chart}<figcaption>{caption}</figcaption></figure>"


def render_report(report: dict) -> str:
    """Render report data and inline SVG charts as a self-contained HTML document."""
    metadata = report.get("metadata", {})
    summary = report.get("summary", {})
    lookback_days = _number(metadata.get("lookback_days"))
    bucket_days = _number(metadata.get("bucket_days"))
    half_life_days = _decimal(metadata.get("half_life_days"))
    as_of = _text(metadata.get("as_of"), "기록 없음")
    timeseries = report.get("timeseries", [])
    emerging = report.get("emerging", [])
    lineup = report.get("lineup", [])
    cooccurrence = report.get("cooccurrence", [])
    framing = report.get("framing", [])
    observations = "".join(f"<li>{item}</li>" for item in _summary_observations(report))
    collection_versions = _metadata_values(
        metadata, "collection_query_versions", COLLECTION_QUERY_VERSION
    )
    prompt_versions = _metadata_values(metadata, "prompt_versions", PROMPT_VERSION)
    catalog_versions = _metadata_values(metadata, "catalog_versions", "기록 없음")

    trend_chart = _report_figure(
        _render_chart("timeseries", timeseries, limit=10),
        f"최근 {lookback_days}일, {bucket_days}일 bucket의 family별 고유 story 수.",
    )
    emerging_chart = _report_figure(
        _render_chart("emerging", emerging, limit=10),
        "최근 24시간과 직전 24시간의 고유 story 수 차이.",
    )
    lineup_chart = _report_figure(
        _render_chart("lineup", lineup, limit=10),
        f"전체 candidate 이력에 {half_life_days}일 half-life를 적용한 가중 story 합계.",
    )
    cooccurrence_chart = _report_figure(
        _render_chart("cooccurrence", cooccurrence, limit=10),
        f"최근 {lookback_days}일 같은 story에 함께 나타난 resolved family pair 수.",
    )
    framing_chart = _report_figure(
        _render_chart("framing", framing, limit=10),
        f"최근 {lookback_days}일 evidence-verified extraction의 family별 stance 분포.",
    )

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Pulse 정제 분석 보고서</title>
<style>
:root {{ color: #202124; background: #ffffff; font-family: {_KOREAN_FONT_CSS}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f6f7f8; line-height: 1.55; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 28px 24px 56px; }}
header, section {{ border-bottom: 1px solid #d8dde3; padding: 28px 0; }}
header {{ padding-top: 8px; }}
h1, h2, h3 {{ color: #111827; margin: 0 0 12px; letter-spacing: 0; overflow-wrap: anywhere; }}
h1 {{ font-size: 2.1rem; }} h2 {{ font-size: 1.35rem; }} h3 {{ font-size: 1rem; }}
p, li {{ margin: 0 0 12px; overflow-wrap: anywhere; }} .subtitle {{ color: #4b5563; font-size: 1.05rem; }}
.meta, .denominator, figcaption, .muted {{ color: #59636e; font-size: .9rem; }}
.kpis {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 22px 0 0; }}
.kpis div {{ border-left: 4px solid #2563eb; background: #fff; padding: 14px 16px; }}
.kpis dt {{ color: #59636e; font-size: .86rem; }} .kpis dd {{ margin: 4px 0 0; color: #111827; font-size: 1.55rem; font-weight: 700; }}
.chart-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
figure {{ margin: 16px 0; min-width: 0; }} figure svg {{ display: block; max-width: 100%; height: auto; }}
figcaption {{ margin-top: 6px; }} .chart-empty, .table-empty {{ padding: 18px; background: #fff; border-left: 3px solid #9ca3af; }}
.table-scroll {{ overflow-x: auto; }} table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: .92rem; }}
th, td {{ border: 1px solid #d8dde3; padding: 9px 10px; text-align: left; vertical-align: top; white-space: nowrap; }}
th {{ background: #edf2f7; color: #1f2937; }}
.limitations {{ background: #fff7ed; border-left: 4px solid #ea580c; padding: 18px; }}
.limitations ul, .summary-list {{ margin: 0; padding-left: 20px; }}
details {{ background: #fff; border: 1px solid #d8dde3; padding: 14px; }} summary {{ cursor: pointer; font-weight: 700; }}
code {{ background: #edf2f7; padding: 1px 4px; }}
@media (max-width: 760px) {{ main {{ padding: 20px 14px 40px; }} h1 {{ font-size: 1.75rem; }} .kpis, .chart-grid {{ grid-template-columns: 1fr; }} section {{ padding: 22px 0; }} }}
</style>
</head>
<body>
<main>
  <header>
    <h1>AI Pulse</h1>
    <p class="subtitle">Hacker News의 AI 모델 담론을 후보 매칭과 검증 추출로 분리해 읽는 정제 분석 보고서</p>
    <p class="meta">기준 시각(UTC): {as_of} | 분석 기간: 최근 {lookback_days}일 | 범위: 수집된 Hacker News story의 모델 담론</p>
    <dl class="kpis">
      <div><dt>수집 story</dt><dd>{_number(summary.get('stories'))}</dd></div>
      <div><dt>모델 catalog</dt><dd>{_number(summary.get('catalog_models'))}</dd></div>
      <div><dt>성공 extraction</dt><dd>{_number(summary.get('successful_extractions'))}</dd></div>
    </dl>
  </header>
  <section aria-labelledby="summary-heading">
    <h2 id="summary-heading">핵심 요약</h2>
    <ul class="summary-list">{observations}</ul>
  </section>
  <section aria-labelledby="trend-heading">
    <h2 id="trend-heading">모델 담론 추이: 지속 변화와 단기 증가를 분리해 확인</h2>
    <p>주간 추이는 지속적인 언급 흐름을, 24시간 증감은 일시적 spike를 보여줍니다. 참여도는 이 보고서의 순위 근거가 아닙니다.</p>
    <div class="chart-grid">{trend_chart}{emerging_chart}</div>
    <p class="denominator">후보 경로 분모: 현재 catalog alias로 매칭된 수집 story입니다. 최근 {lookback_days}일 결과는 이 분모에 한정됩니다.</p>
  </section>
  <section aria-labelledby="lineup-heading">
    <h2 id="lineup-heading">최근 모델 라인업: 누적 언급과 최신성 가중량의 차이</h2>
    <p>전체 candidate 이력에 시간 감쇠를 적용해 최근에 관측된 모델을 함께 보되, 이는 모델 성능이나 시장 점유율 순위가 아닙니다.</p>
    {lineup_chart}
    {_lineup_table(lineup)}
    <p class="denominator">후보 경로 분모: 현재 catalog alias로 매칭된 수집 story 전체 이력입니다.</p>
  </section>
  <section aria-labelledby="cooccurrence-heading">
    <h2 id="cooccurrence-heading">함께 언급되는 모델 조합: 같은 story 안의 논의 맥락</h2>
    <p>같은 story에서 함께 등장한 family 조합을 집계했습니다. 이 수치는 성능 비교, 의존관계, 또는 인과관계를 의미하지 않습니다.</p>
    {cooccurrence_chart}
    {_cooccurrence_table(cooccurrence)}
    <p class="denominator">후보 경로 분모: 최근 {lookback_days}일의 resolved candidate story이며, story별 pair는 한 번만 계산합니다.</p>
  </section>
  <section aria-labelledby="framing-heading">
    <h2 id="framing-heading">Story framing: 검증된 추출 표본에서의 stance 분포</h2>
    <p>stance는 story가 모델을 어떻게 framing했는지를 나타내는 open-world label입니다. 빈 label과 unresolved label은 임의로 합치지 않습니다.</p>
    {framing_chart}
    {_framing_table(framing)}
    <p class="denominator">추출 경로 분모: 최신 성공 extraction 중 evidence-verified observation입니다. 전체 수집 story 수와 같지 않습니다.</p>
  </section>
  <section aria-labelledby="limitations-heading">
    <h2 id="limitations-heading">신뢰도와 한계</h2>
    <div class="limitations"><ul>
      <li>수집 범위는 collector keyword와 수동 실행 시점에 영향을 받습니다.</li>
      <li>candidate 전체 수집본과 extraction 표본은 서로 다른 분모이므로 직접 비율 비교에 사용하면 안 됩니다.</li>
      <li>catalog에 없는 모델은 candidate 경로에서 보이지 않을 수 있습니다.</li>
      <li>lexical alias matching에는 문맥에 따른 false positive 가능성이 남아 있습니다.</li>
      <li>framing은 evidence-verified extraction만 사용하며, 검증되지 않은 추출은 포함하지 않습니다.</li>
      <li>story의 참여도와 stance는 댓글 sentiment나 모델에 대한 사용자 선호를 뜻하지 않습니다.</li>
    </ul></div>
  </section>
  <section aria-labelledby="appendix-heading">
    <h2 id="appendix-heading">기술 부록</h2>
    <details open>
      <summary>계산, 버전, 재생성 기준</summary>
      <p>Gold 함수: <code>candidate_mention_timeseries</code>, <code>candidate_emerging_models</code>, <code>candidate_model_lineup</code>, <code>candidate_model_cooccurrence</code>, <code>model_framing_sentiment</code>.</p>
      <p>시간 기준: <code>AS_OF = MAX(stories.created_at)</code>이며 UTC를 사용합니다. 시계열·co-occurrence·framing은 최근 {lookback_days}일, 시계열 bucket은 {bucket_days}일, lineup은 전체 이력과 {half_life_days}일 half-life를 사용합니다. emerging은 최근 24시간과 직전 24시간을 비교합니다.</p>
      <p>버전 메타데이터: collection={collection_versions}; prompt={prompt_versions}; catalog={catalog_versions}.</p>
      <p>빈 Gold 결과는 차트와 표에서 "해당 기준에서 관측된 결과 없음"으로 표시합니다. 재생성: <code>python3 build_report.py --db ai_monitor.db --output analysis_report.html</code>.</p>
      <p>수동 검수 상태: <code>data/manual_review_template.csv</code> 기반의 고정 시드 30-story 검수는 별도 사람이 채우는 절차이며, 이 정적 보고서에는 precision/recall 값을 자동 주장하지 않습니다.</p>
    </details>
  </section>
</main>
</body>
</html>
"""


def build_report(db_path: str, output_path: str) -> None:
    """Build one UTF-8 HTML report from a local SQLite database."""
    database_path = Path(db_path)
    if not database_path.is_file():
        raise FileNotFoundError(f"데이터베이스 파일을 찾을 수 없습니다: {database_path}")

    conn = None
    try:
        conn = connect(database_path)
        try:
            report = build_report_data(conn)
        except ValueError as exc:
            if str(exc) == "stories table has no created_at value":
                raise ValueError("보고서를 생성할 stories가 없습니다.") from exc
            raise
        Path(output_path).write_text(render_report(report), encoding="utf-8")
    finally:
        if conn is not None:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="ai_monitor.db")
    parser.add_argument("--output", default="analysis_report.html")
    args = parser.parse_args()
    try:
        build_report(args.db, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"보고서 생성 실패: {exc}\n")


if __name__ == "__main__":
    main()
