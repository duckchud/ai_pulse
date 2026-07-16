"""Deterministic catalog-alias candidate selection for collected stories."""

import argparse
import json
import random
import re
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH
from db import catalog_version, connect, migrate, replace_story_candidates
from reference_data import normalize_alias


def _alias_pattern(alias: str) -> re.Pattern[str]:
    tokens = [re.escape(token) for token in normalize_alias(alias).split()]
    joined = r"[^A-Za-z0-9]+".join(tokens)
    return re.compile(rf"(?<![A-Za-z0-9])({joined})(?![A-Za-z0-9])", re.IGNORECASE)


def match_story_aliases(
    title: str | None, text: str | None, aliases: list[dict[str, str]]
) -> list[dict[str, str]]:
    evidence, seen = [], set()
    for alias_row in aliases:
        for field, source in (("title", title or ""), ("text", text or "")):
            for match in _alias_pattern(alias_row["alias"]).finditer(source):
                item = {
                    "model_id": alias_row["model_id"],
                    "alias": alias_row["alias"],
                    "field": field,
                    "quote": match.group(1),
                }
                key = tuple(item[key] for key in ("model_id", "alias", "field", "quote"))
                if key not in seen:
                    seen.add(key)
                    evidence.append(item)
    return sorted(
        evidence,
        key=lambda item: (item["model_id"], item["field"], item["quote"], item["alias"]),
    )


def select_candidates(conn: sqlite3.Connection, selected_at: str | None = None) -> int:
    version = catalog_version(conn)
    aliases = [
        dict(row)
        for row in conn.execute(
            "SELECT ma.model_id, ma.alias_normalized AS alias "
            "FROM model_aliases ma "
            "JOIN model_catalog mc ON mc.model_id = ma.model_id "
            "ORDER BY ma.model_id, ma.alias_normalized"
        )
    ]
    candidates = []
    for story in conn.execute("SELECT id, title, text FROM stories"):
        evidence = match_story_aliases(story["title"], story["text"], aliases)
        if not evidence:
            continue
        candidates.append(
            {
                "story_id": story["id"],
                "catalog_version": version,
                "candidate_reason": "catalog_alias_match",
                "matched_model_ids": json.dumps(
                    sorted({item["model_id"] for item in evidence}), ensure_ascii=False
                ),
                "evidence_json": json.dumps(evidence, ensure_ascii=False),
                "selected_at": selected_at
                if selected_at is not None
                else datetime.now(timezone.utc).isoformat(),
            }
        )
    replace_story_candidates(conn, version, candidates)
    return len(candidates)


def unmatched_sample(
    conn: sqlite3.Connection, sample_size: int, seed: int
) -> list[dict[str, object]]:
    if sample_size < 1:
        raise ValueError("sample_size must be at least 1")

    version = catalog_version(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT s.id AS story_id, s.title, s.text "
            "FROM stories s "
            "LEFT JOIN story_candidates sc "
            "ON sc.story_id = s.id AND sc.catalog_version = ? "
            "WHERE sc.story_id IS NULL "
            "ORDER BY s.id",
            (version,),
        )
    ]
    return random.Random(seed).sample(rows, k=min(sample_size, len(rows)))


def _positive_sample_size(value: str) -> int:
    sample_size = int(value)
    if sample_size < 1:
        raise argparse.ArgumentTypeError("sample_size must be at least 1")
    return sample_size


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Catalog-driven offline story candidate selection."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("select", help="select catalog alias candidates")
    unmatched_parser = subparsers.add_parser(
        "unmatched-sample", help="sample stories without catalog candidates"
    )
    unmatched_parser.add_argument("--sample-size", type=_positive_sample_size, required=True)
    unmatched_parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    conn = connect(DB_PATH)
    try:
        if args.command == "select":
            migrate(conn)
            count = select_candidates(conn)
            print(f"후보 선별 완료: {count}건")
        else:
            migrate(conn)
            print(
                json.dumps(
                    unmatched_sample(conn, args.sample_size, args.seed), ensure_ascii=False
                )
            )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    main()
