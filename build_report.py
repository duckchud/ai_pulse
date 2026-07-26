"""Build report data and render its focused chart fragments."""

import io
import re
import sqlite3

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
from db import latest_successful_extractions


_EMPTY_CHART = '<p class="chart-empty">해당 기준에서 관측된 결과 없음</p>'
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


def _load_summary_counts(conn: sqlite3.Connection) -> dict[str, int]:
    stories = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    catalog_models = conn.execute("SELECT COUNT(*) FROM model_catalog").fetchone()[0]
    successful_extractions = len(latest_successful_extractions(conn))
    return {
        "stories": int(stories),
        "catalog_models": int(catalog_models),
        "successful_extractions": int(successful_extractions),
    }


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
                     "bucket_days": 7, "half_life_days": 30.0, "top_n": top_n},
        "summary": summary,
        "timeseries": _frame_records(timeseries, top_n * 6),
        "emerging": _frame_records(emerging, top_n),
        "lineup": _frame_records(lineup, top_n),
        "cooccurrence": _frame_records(cooccurrence, top_n),
        "framing": _frame_records(framing, top_n * 4),
    }


def _empty_chart() -> str:
    return _EMPTY_CHART


def _normalize_svg(svg: str) -> str:
    """Remove render-time SVG metadata and normalize generated references."""
    svg = re.sub(r"<dc:date>.*?</dc:date>\n", "", svg)
    ids = re.findall(r'id="([^"]+)"', svg)
    replacements = {
        old: f"svg_id_{index}" for index, old in enumerate(dict.fromkeys(ids))
    }
    for old, new in replacements.items():
        svg = svg.replace(f'id="{old}"', f'id="{new}"')
        svg = svg.replace(f'url(#{old})', f'url(#{new})')
        svg = svg.replace(f'href="#{old}"', f'href="#{new}"')
    return svg


def _svg_from_figure(fig) -> str:
    """Serialize a figure as standalone inline SVG without an XML preamble."""
    buffer = io.BytesIO()
    with matplotlib.rc_context({"svg.fonttype": "none"}):
        fig.savefig(buffer, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    svg = buffer.getvalue().decode("utf-8")
    return _normalize_svg(svg[svg.find("<svg") :])


def _figure(height: float = 3.8):
    return plt.subplots(figsize=(9.5, height), constrained_layout=True)


def _render_timeseries_chart(rows: list[dict], limit: int | None = None) -> str:
    """Return inline SVG or the Korean empty-state HTML."""
    if not rows:
        return _empty_chart()

    frame = pd.DataFrame(rows)
    labels = sorted(frame["bucket_start"].astype(str).unique())
    groups = list(dict.fromkeys(frame["group_label"].astype(str)))
    if limit is not None:
        groups = groups[:limit]
    fig, ax = _figure(max(3.8, 2.6 + len(groups) * 0.12))
    x = range(len(labels))
    for group in groups:
        group_frame = frame[frame["group_label"].astype(str) == group].copy()
        values = group_frame.assign(
            bucket_start=group_frame["bucket_start"].astype(str)
        ).set_index("bucket_start")["story_count"].reindex(labels, fill_value=0)
        ax.plot(x, values.tolist(), marker="o", linewidth=1.8, label=group)
    ax.set_xticks(list(x), labels, rotation=35, ha="right")
    ax.set_ylabel("Stories")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    return _svg_from_figure(fig)


def _render_horizontal_bars(labels, values, xlabel: str) -> str:
    fig, ax = _figure(max(3.2, 1.4 + len(labels) * 0.42))
    positions = list(range(len(labels)))
    ax.barh(positions, values, color="#386cb0")
    ax.set_yticks(positions, labels)
    ax.set_xlabel(xlabel)
    ax.invert_yaxis()
    if min(values, default=0) >= 0:
        ax.set_xlim(left=0)
    return _svg_from_figure(fig)


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
        frame["label"].tolist(), frame["story_count"].astype(float).tolist(), "Shared stories"
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
    return _svg_from_figure(fig)


def _render_chart(
    report_key: str, rows: list[dict], limit: int | None = None
) -> str:
    """Render a report section's chart from the stable report-data key."""
    try:
        renderer = _CHART_RENDERERS[report_key]
    except KeyError as exc:
        raise ValueError(f"unknown report chart key: {report_key!r}") from exc
    return renderer(rows, limit=limit)
