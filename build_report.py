"""Build the stable data contract consumed by the refined analysis report."""

import sqlite3

import pandas as pd

from analysis import (
    candidate_emerging_models,
    candidate_mention_timeseries,
    candidate_model_cooccurrence,
    candidate_model_lineup,
    model_framing_sentiment,
)
from db import latest_successful_extractions


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
                     "bucket_days": 7, "half_life_days": 30.0},
        "summary": summary,
        "timeseries": _frame_records(timeseries, top_n * 6),
        "emerging": _frame_records(emerging, top_n),
        "lineup": _frame_records(lineup, top_n),
        "cooccurrence": _frame_records(cooccurrence, top_n),
        "framing": _frame_records(framing, top_n * 4),
    }
