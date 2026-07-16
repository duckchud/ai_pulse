"""Deterministic catalog-alias candidate selection for collected stories."""

import json
import re
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH
from db import catalog_version, connect, migrate, upsert_story_candidates
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
    upsert_story_candidates(conn, candidates)
    return len(candidates)


def main() -> int:
    conn = connect(DB_PATH)
    try:
        migrate(conn)
        return select_candidates(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
