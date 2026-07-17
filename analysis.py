"""analysis.py — Gold 계층: 검증된 observation을 family/version에 매핑해 집계.

Silver(story_extractions)의 최신 성공 extraction만 읽고, evidence_verified가
True인 observation만 별칭 사전(model_aliases)으로 해석한다. 해석되지 않은
surface는 버리지 않고 `resolution_status="unresolved"` 그룹으로 남긴다.
쓰기·부수효과가 전혀 없는 읽기 전용 pandas 함수만 제공하며, notebook/MCP
코드에 의존하지 않는다.
"""

import itertools
import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from config import PROMPT_VERSION
from db import catalog_version, latest_successful_extractions
from enrich import normalize_story_text
from reference_data import resolve_model

_EMERGING_COLUMNS = [
    "vendor", "family", "version", "resolution_status", "group_label",
    "recent_story_count", "previous_story_count", "mention_delta", "mention_growth",
    "points_sum", "comments_sum",
    "as_of", "collection_query_version", "prompt_version", "catalog_version",
]

_COOCCURRENCE_COLUMNS = [
    "vendor_a", "family_a", "version_a", "vendor_b", "family_b", "version_b",
    "story_count",
    "as_of", "collection_query_version", "prompt_version", "catalog_version",
]

_CANDIDATE_EMERGING_COLUMNS = [
    "vendor", "family", "version", "resolution_status", "group_label",
    "recent_story_count", "previous_story_count", "mention_delta", "mention_growth",
    "points_sum", "comments_sum",
    "as_of", "collection_query_version", "catalog_version", "candidate_reason",
]

_CANDIDATE_COOCCURRENCE_COLUMNS = [
    "vendor_a", "family_a", "version_a", "vendor_b", "family_b", "version_b",
    "story_count",
    "as_of", "collection_query_version", "catalog_version", "candidate_reason",
]

_FRAMING_COLUMNS = [
    "vendor", "family", "version", "resolution_status", "group_label",
    "stance", "story_count",
    "as_of", "collection_query_version", "prompt_version", "catalog_version",
]

_OBSERVATION_COLUMNS = [
    "story_id", "surface", "resolution_status", "vendor", "family", "version",
    "model_id", "attributes", "prompt_version", "created_at_i", "points",
    "num_comments", "collection_query_version",
]

_CANDIDATE_MENTION_COLUMNS = [
    "story_id", "model_id", "vendor", "family", "version", "resolution_status",
    "candidate_reason", "catalog_version", "created_at_i", "points", "num_comments",
    "collection_query_version",
]

_REVIEW_COLUMNS = [
    "story_id", "title", "normalized_text", "parsed_json",
    "is_relevant", "expected_mentions", "extracted_mentions", "evidence_valid",
    "family_version_mapping_valid", "stance_valid", "error_type", "reviewer_notes",
]

_REVIEWER_FILL_COLUMNS = [
    "is_relevant", "expected_mentions", "extracted_mentions", "evidence_valid",
    "family_version_mapping_valid", "stance_valid", "error_type", "reviewer_notes",
]


def _validate_group_level(group_level: str) -> None:
    if group_level not in ("family", "version"):
        raise ValueError(f"group_level must be 'family' or 'version', got {group_level!r}")


def _parse_as_of(as_of) -> int:
    """as_of를 UTC 기준 unix timestamp(정수)로 변환한다.

    ISO 문자열은 'Z'를 UTC 오프셋으로 취급한다(tz 정보가 없으면 UTC로 간주).
    이미 숫자(unix timestamp)면 그대로 정수화한다.
    """
    if isinstance(as_of, (int, float)):
        return int(as_of)
    if isinstance(as_of, datetime):
        dt = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _single_or_joined(values) -> str | None:
    uniq = sorted({v for v in values if v not in (None, "")})
    if not uniq:
        return None
    if len(uniq) == 1:
        return uniq[0]
    return ",".join(uniq)


def _metadata(conn: sqlite3.Connection, obs_df: pd.DataFrame, as_of) -> dict:
    """모든 Gold 결과에 공통으로 붙는 재현성 메타데이터.

    collection_query_version/prompt_version은 이번 호출에서 실제 로드된
    observation 데이터에서 관측된 값(들)을 사용한다(다수면 콤마로 join).
    prompt_version이 데이터에 하나도 없으면(빈 결과) config.PROMPT_VERSION으로
    폴백한다. catalog_version은 observation이 아니라 model_catalog 테이블
    자체에서 조회한다 — catalog가 비어 있으면(프로덕션 초기 상태) None.
    """
    catalog_versions = pd.read_sql_query(
        "SELECT DISTINCT catalog_version FROM model_catalog", conn
    )["catalog_version"].tolist()

    if obs_df.empty:
        prompt_versions, collection_versions = [], []
    else:
        prompt_versions = obs_df["prompt_version"].dropna().unique().tolist()
        collection_versions = obs_df["collection_query_version"].dropna().unique().tolist()

    return {
        "as_of": as_of,
        "collection_query_version": _single_or_joined(collection_versions),
        "prompt_version": _single_or_joined(prompt_versions) or PROMPT_VERSION,
        "catalog_version": _single_or_joined(catalog_versions),
    }


def _load_verified_observations(conn: sqlite3.Connection) -> pd.DataFrame:
    """story별 최신 성공 extraction에서 evidence_verified=True인 observation만
    꺼내 surface를 별칭 사전으로 해석하고 story 메타데이터를 붙인다.

    해석 실패(unresolved)도 행으로 남긴다 — 절대 버리지 않는다.
    """
    extractions = latest_successful_extractions(conn)
    if extractions.empty:
        return pd.DataFrame(columns=_OBSERVATION_COLUMNS)

    stories = pd.read_sql_query(
        "SELECT id AS story_id, created_at_i, points, num_comments, "
        "collection_query_version FROM stories",
        conn,
    )
    stories["points"] = stories["points"].fillna(0).astype(int)
    stories["num_comments"] = stories["num_comments"].fillna(0).astype(int)
    stories_by_id = stories.set_index("story_id")

    rows = []
    for _, ext in extractions.iterrows():
        raw = ext["parsed_json"]
        if not raw:
            continue
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(envelope, dict):
            continue
        for obs in envelope.get("observations", []):
            if obs.get("evidence_verified") is not True:
                continue
            surface = obs.get("surface")
            if not surface:
                continue
            resolved = resolve_model(conn, surface)
            row = {
                "story_id": ext["story_id"],
                "surface": surface,
                "attributes": obs.get("attributes") or {},
                "prompt_version": ext["prompt_version"],
            }
            if resolved is None:
                row.update(
                    {"resolution_status": "unresolved", "vendor": None, "family": None,
                     "version": None, "model_id": None}
                )
            else:
                row.update(
                    {
                        "resolution_status": "resolved",
                        "vendor": resolved["vendor"],
                        "family": resolved["family"],
                        "version": resolved.get("version"),
                        "model_id": resolved["model_id"],
                    }
                )
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=_OBSERVATION_COLUMNS)

    obs_df = pd.DataFrame(rows)
    obs_df = obs_df.join(stories_by_id, on="story_id")
    # FK 제약상 발생하지 않아야 하지만, 방어적으로 story 메타데이터가 없는 행은 제외.
    obs_df = obs_df.dropna(subset=["created_at_i"])
    obs_df["created_at_i"] = obs_df["created_at_i"].astype(int)
    return obs_df[_OBSERVATION_COLUMNS].reset_index(drop=True)


def _load_candidate_mentions(conn: sqlite3.Connection) -> pd.DataFrame:
    """현재 catalog 버전의 후보 model ID를 story·catalog 메타데이터에 연결한다.

    후보는 catalog alias 매칭 결과이므로, Silver extraction이나 모델 응답을 읽지
    않는다. catalog가 아직 비어 있고 후보도 없으면 초기 상태의 빈 frame을 준다.
    """
    has_catalog = conn.execute("SELECT 1 FROM model_catalog LIMIT 1").fetchone()
    has_candidates = conn.execute("SELECT 1 FROM story_candidates LIMIT 1").fetchone()
    if has_catalog is None and has_candidates is None:
        return pd.DataFrame(columns=_CANDIDATE_MENTION_COLUMNS)

    current_catalog_version = catalog_version(conn)
    candidates = pd.read_sql_query(
        """
        SELECT
            sc.story_id,
            sc.candidate_reason,
            sc.matched_model_ids,
            sc.catalog_version,
            s.created_at_i,
            s.points,
            s.num_comments,
            s.collection_query_version
        FROM story_candidates sc
        JOIN stories s ON s.id = sc.story_id
        WHERE sc.catalog_version = ?
        """,
        conn,
        params=(current_catalog_version,),
    )
    if candidates.empty:
        return pd.DataFrame(columns=_CANDIDATE_MENTION_COLUMNS)

    catalog = pd.read_sql_query(
        """
        SELECT model_id, vendor, family, version
        FROM model_catalog
        WHERE catalog_version = ?
        """,
        conn,
        params=(current_catalog_version,),
    ).set_index("model_id")

    rows = []
    for _, candidate in candidates.iterrows():
        try:
            model_ids = json.loads(candidate["matched_model_ids"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("matched_model_ids must be a JSON array") from exc
        if not isinstance(model_ids, list):
            raise ValueError("matched_model_ids must be a JSON array")
        seen_model_ids = set()
        for model_id in model_ids:
            if not isinstance(model_id, str) or not model_id:
                raise ValueError("matched_model_ids must contain non-empty model IDs")
            if model_id in seen_model_ids:
                raise ValueError("matched_model_ids must contain unique model IDs")
            seen_model_ids.add(model_id)
            if model_id not in catalog.index:
                raise ValueError("matched_model_ids must exist in the current catalog")
            model = catalog.loc[model_id]
            rows.append(
                {
                    "story_id": candidate["story_id"],
                    "model_id": model_id,
                    "vendor": model["vendor"],
                    "family": model["family"],
                    "version": model["version"],
                    "resolution_status": "resolved",
                    "candidate_reason": candidate["candidate_reason"],
                    "catalog_version": candidate["catalog_version"],
                    "created_at_i": candidate["created_at_i"],
                    "points": candidate["points"],
                    "num_comments": candidate["num_comments"],
                    "collection_query_version": candidate["collection_query_version"],
                }
            )

    if not rows:
        return pd.DataFrame(columns=_CANDIDATE_MENTION_COLUMNS)

    mentions = pd.DataFrame(rows)
    mentions["created_at_i"] = mentions["created_at_i"].astype(int)
    mentions["points"] = mentions["points"].fillna(0).astype(int)
    mentions["num_comments"] = mentions["num_comments"].fillna(0).astype(int)
    return mentions[_CANDIDATE_MENTION_COLUMNS].reset_index(drop=True)


def _add_group_columns(df: pd.DataFrame, group_level: str) -> pd.DataFrame:
    """group_level에 따라 (vendor, family[, version]) 그룹 키를 부여한다.

    unresolved surface는 group_level과 무관하게 surface 자체가 그룹이 된다.
    version 그룹에서 버전이 없으면 "unresolved version"으로 표시한다.
    """

    def compute(row):
        if row["resolution_status"] == "unresolved":
            return pd.Series(
                {
                    "group_key": ("unresolved", row["surface"]),
                    "vendor": None,
                    "family": None,
                    "version": None,
                    "group_label": row["surface"],
                }
            )
        vendor, family = row["vendor"], row["family"]
        if group_level == "version":
            version_label = row["version"] if row["version"] else "unresolved version"
            return pd.Series(
                {
                    "group_key": ("version", vendor, family, version_label),
                    "vendor": vendor,
                    "family": family,
                    "version": version_label,
                    "group_label": f"{vendor}/{family}/{version_label}",
                }
            )
        return pd.Series(
            {
                "group_key": ("family", vendor, family),
                "vendor": vendor,
                "family": family,
                "version": None,
                "group_label": f"{vendor}/{family}",
            }
        )

    group_cols = df.apply(compute, axis=1)
    base = df.drop(columns=["vendor", "family", "version"]).reset_index(drop=True)
    return pd.concat([base, group_cols.reset_index(drop=True)], axis=1)


def emerging_models(
    conn: sqlite3.Connection,
    as_of,
    group_level: str,
    window_hours: int = 24,
    min_recent_count: int = 2,
    top_n: int = 20,
) -> pd.DataFrame:
    """고유 story 기준 최근/직전 시간창 언급 수와 증가량/증가율을 계산한다.

    최근 창 [as_of - window, as_of), 직전 창 [as_of - 2*window, as_of - window).
    points_sum/comments_sum은 참여도 표시용 보조 컬럼이며 정렬 기준에는
    쓰이지 않는다 — 정렬은 mention_delta DESC, recent_story_count DESC.
    """
    _validate_group_level(group_level)
    as_of_ts = _parse_as_of(as_of)
    window_seconds = window_hours * 3600
    recent_start, recent_end = as_of_ts - window_seconds, as_of_ts
    previous_start, previous_end = recent_start - window_seconds, recent_start

    obs_df = _load_verified_observations(conn)
    meta = _metadata(conn, obs_df, as_of)
    if obs_df.empty:
        return pd.DataFrame(columns=_EMERGING_COLUMNS)

    grouped = _add_group_columns(obs_df, group_level)
    # 같은 story가 같은 그룹을 두 번 언급해도 한 번만 센다.
    dedup = grouped.drop_duplicates(subset=["story_id", "group_key"])

    recent = dedup[(dedup["created_at_i"] >= recent_start) & (dedup["created_at_i"] < recent_end)]
    previous = dedup[(dedup["created_at_i"] >= previous_start) & (dedup["created_at_i"] < previous_end)]
    if recent.empty:
        return pd.DataFrame(columns=_EMERGING_COLUMNS)

    recent_agg = recent.groupby("group_key").agg(
        recent_story_count=("story_id", "nunique"),
        points_sum=("points", "sum"),
        comments_sum=("num_comments", "sum"),
    )
    previous_counts = previous.groupby("group_key")["story_id"].nunique()
    recent_agg["previous_story_count"] = recent_agg.index.map(previous_counts).fillna(0).astype(int)
    recent_agg["mention_delta"] = recent_agg["recent_story_count"] - recent_agg["previous_story_count"]
    recent_agg["mention_growth"] = recent_agg["mention_delta"] / recent_agg["previous_story_count"].clip(lower=1)

    recent_agg = recent_agg[recent_agg["recent_story_count"] >= min_recent_count]
    if recent_agg.empty:
        return pd.DataFrame(columns=_EMERGING_COLUMNS)

    group_info = grouped.drop_duplicates(subset="group_key").set_index("group_key")[
        ["vendor", "family", "version", "resolution_status", "group_label"]
    ]
    result = recent_agg.join(group_info).reset_index(drop=True)
    result = result.sort_values(
        ["mention_delta", "recent_story_count"], ascending=[False, False]
    ).head(top_n).reset_index(drop=True)

    for key, value in meta.items():
        result[key] = value
    return result[_EMERGING_COLUMNS]


def model_cooccurrence(
    conn: sqlite3.Connection, as_of, group_level: str, min_count: int = 2
) -> pd.DataFrame:
    """한 story 안에서 서로 다른 두 해결된(resolved) 모델 그룹의 쌍을
    story당 한 번만 세어 반환한다. unresolved 그룹은 쌍에서 제외한다."""
    _validate_group_level(group_level)
    obs_df = _load_verified_observations(conn)
    meta = _metadata(conn, obs_df, as_of)
    if obs_df.empty:
        return pd.DataFrame(columns=_COOCCURRENCE_COLUMNS)

    grouped = _add_group_columns(obs_df, group_level)
    resolved = grouped[grouped["resolution_status"] == "resolved"]
    if resolved.empty:
        return pd.DataFrame(columns=_COOCCURRENCE_COLUMNS)

    dedup = resolved.drop_duplicates(subset=["story_id", "group_key"])
    # group_key는 튜플이라 DataFrame.loc가 다중 레벨 인덱서로 오해석한다
    # (pandas.errors.IndexingError: Too many indexers) — 일반 dict로 조회한다.
    group_info = {
        row["group_key"]: (row["vendor"], row["family"], row["version"])
        for _, row in dedup.drop_duplicates(subset="group_key").iterrows()
    }

    pair_counts: dict[tuple, int] = {}
    for _, sub in dedup.groupby("story_id"):
        keys = sorted(sub["group_key"].unique())
        for key_a, key_b in itertools.combinations(keys, 2):
            pair_counts[(key_a, key_b)] = pair_counts.get((key_a, key_b), 0) + 1

    rows = []
    for (key_a, key_b), count in pair_counts.items():
        if count < min_count:
            continue
        vendor_a, family_a, version_a = group_info[key_a]
        vendor_b, family_b, version_b = group_info[key_b]
        rows.append(
            {
                "vendor_a": vendor_a, "family_a": family_a, "version_a": version_a,
                "vendor_b": vendor_b, "family_b": family_b, "version_b": version_b,
                "story_count": count,
            }
        )
    if not rows:
        return pd.DataFrame(columns=_COOCCURRENCE_COLUMNS)

    result = pd.DataFrame(rows)
    for key, value in meta.items():
        result[key] = value
    return result[_COOCCURRENCE_COLUMNS]


def candidate_emerging_models(
    conn: sqlite3.Connection,
    as_of,
    group_level: str,
    window_hours: int = 24,
    min_recent_count: int = 2,
    top_n: int = 20,
) -> pd.DataFrame:
    """catalog alias 후보의 story 기준 최근/직전 언급 증가량을 집계한다.

    후보 Gold는 Silver extraction과 독립적이며, sentiment나 stance를 만들지 않는다.
    시간창과 정렬은 ``emerging_models``와 동일하게 유지한다.
    """
    _validate_group_level(group_level)
    as_of_ts = _parse_as_of(as_of)
    window_seconds = window_hours * 3600
    recent_start, recent_end = as_of_ts - window_seconds, as_of_ts
    previous_start, previous_end = recent_start - window_seconds, recent_start

    mentions = _load_candidate_mentions(conn)
    if mentions.empty:
        return pd.DataFrame(columns=_CANDIDATE_EMERGING_COLUMNS)

    grouped = _add_group_columns(mentions, group_level)
    dedup = grouped.drop_duplicates(subset=["story_id", "group_key"])
    recent = dedup[
        (dedup["created_at_i"] >= recent_start) & (dedup["created_at_i"] < recent_end)
    ]
    previous = dedup[
        (dedup["created_at_i"] >= previous_start) & (dedup["created_at_i"] < previous_end)
    ]
    if recent.empty:
        return pd.DataFrame(columns=_CANDIDATE_EMERGING_COLUMNS)

    recent_agg = recent.groupby("group_key").agg(
        recent_story_count=("story_id", "nunique"),
        points_sum=("points", "sum"),
        comments_sum=("num_comments", "sum"),
    )
    previous_counts = previous.groupby("group_key")["story_id"].nunique()
    recent_agg["previous_story_count"] = (
        recent_agg.index.map(previous_counts).fillna(0).astype(int)
    )
    recent_agg["mention_delta"] = (
        recent_agg["recent_story_count"] - recent_agg["previous_story_count"]
    )
    recent_agg["mention_growth"] = (
        recent_agg["mention_delta"]
        / recent_agg["previous_story_count"].clip(lower=1)
    )
    recent_agg = recent_agg[recent_agg["recent_story_count"] >= min_recent_count]
    if recent_agg.empty:
        return pd.DataFrame(columns=_CANDIDATE_EMERGING_COLUMNS)

    group_info = grouped.drop_duplicates(subset="group_key").set_index("group_key")[
        ["vendor", "family", "version", "resolution_status", "group_label"]
    ]
    result = recent_agg.join(group_info).reset_index(drop=True)
    result = result.sort_values(
        ["mention_delta", "recent_story_count"], ascending=[False, False]
    ).head(top_n).reset_index(drop=True)
    result["as_of"] = as_of
    result["collection_query_version"] = _single_or_joined(
        mentions["collection_query_version"].dropna().unique().tolist()
    )
    result["catalog_version"] = _single_or_joined(
        mentions["catalog_version"].dropna().unique().tolist()
    )
    result["candidate_reason"] = "catalog_alias_match"
    return result[_CANDIDATE_EMERGING_COLUMNS]


def candidate_model_cooccurrence(
    conn: sqlite3.Connection, as_of, group_level: str, min_count: int = 2
) -> pd.DataFrame:
    """catalog alias 후보의 서로 다른 모델 그룹 쌍을 story당 한 번만 센다.

    pair 생성·중복 제거·최소 빈도 규칙은 ``model_cooccurrence``와 같으며,
    candidate 입력은 모두 catalog에 연결된 resolved 모델이다.
    """
    _validate_group_level(group_level)
    mentions = _load_candidate_mentions(conn)
    if mentions.empty:
        return pd.DataFrame(columns=_CANDIDATE_COOCCURRENCE_COLUMNS)

    grouped = _add_group_columns(mentions, group_level)
    dedup = grouped.drop_duplicates(subset=["story_id", "group_key"])
    group_info = {
        row["group_key"]: (row["vendor"], row["family"], row["version"])
        for _, row in dedup.drop_duplicates(subset="group_key").iterrows()
    }

    pair_counts: dict[tuple, int] = {}
    for _, sub in dedup.groupby("story_id"):
        keys = sorted(sub["group_key"].unique())
        for key_a, key_b in itertools.combinations(keys, 2):
            pair_counts[(key_a, key_b)] = pair_counts.get((key_a, key_b), 0) + 1

    rows = []
    for (key_a, key_b), count in pair_counts.items():
        if count < min_count:
            continue
        vendor_a, family_a, version_a = group_info[key_a]
        vendor_b, family_b, version_b = group_info[key_b]
        rows.append(
            {
                "vendor_a": vendor_a, "family_a": family_a, "version_a": version_a,
                "vendor_b": vendor_b, "family_b": family_b, "version_b": version_b,
                "story_count": count,
            }
        )
    if not rows:
        return pd.DataFrame(columns=_CANDIDATE_COOCCURRENCE_COLUMNS)

    result = pd.DataFrame(rows)
    result["as_of"] = as_of
    result["collection_query_version"] = _single_or_joined(
        mentions["collection_query_version"].dropna().unique().tolist()
    )
    result["catalog_version"] = _single_or_joined(
        mentions["catalog_version"].dropna().unique().tolist()
    )
    result["candidate_reason"] = "catalog_alias_match"
    return result[_CANDIDATE_COOCCURRENCE_COLUMNS]


def model_framing_sentiment(
    conn: sqlite3.Connection, as_of, group_level: str, model_id: str | None = None
) -> pd.DataFrame:
    """검증된 evidence와 비어있지 않은 attributes.stance가 있는 observation만
    집계한다. stance 원문 라벨을 그대로 보존하고(닫힌 집합으로 강제하지
    않음), story별로 (그룹, stance) 조합을 한 번만 센다."""
    _validate_group_level(group_level)
    obs_df = _load_verified_observations(conn)
    meta = _metadata(conn, obs_df, as_of)
    if obs_df.empty:
        return pd.DataFrame(columns=_FRAMING_COLUMNS)

    grouped = _add_group_columns(obs_df, group_level)
    grouped = grouped.copy()
    grouped["stance"] = grouped["attributes"].apply(
        lambda attrs: attrs.get("stance") if isinstance(attrs, dict) else None
    )
    framed = grouped[grouped["stance"].notna() & (grouped["stance"] != "")]
    if model_id is not None:
        framed = framed[framed["model_id"] == model_id]
    if framed.empty:
        return pd.DataFrame(columns=_FRAMING_COLUMNS)

    dedup = framed.drop_duplicates(subset=["story_id", "group_key", "stance"])
    counts = dedup.groupby(["group_key", "stance"]).size().rename("story_count").reset_index()

    group_info = dedup.drop_duplicates(subset="group_key").set_index("group_key")[
        ["vendor", "family", "version", "resolution_status", "group_label"]
    ]
    result = counts.join(group_info, on="group_key").drop(columns=["group_key"])
    for key, value in meta.items():
        result[key] = value
    return result[_FRAMING_COLUMNS]


def unresolved_surface_report(conn: sqlite3.Connection, top_n: int = 20) -> pd.DataFrame:
    """카탈로그 보강 대상을 찾기 위해 model/product 미해결 surface를 언급 빈도순으로 모은다.

    organization/category 등 model_catalog 대상이 아닌 kind는 제외한다.
    resolved surface도 제외한다(이미 카탈로그에 있으므로 보강 대상이 아님).
    """
    obs_df = _load_verified_observations(conn)
    if obs_df.empty:
        return pd.DataFrame(columns=["surface", "mention_count"])

    is_model_or_product = obs_df["attributes"].apply(
        lambda attrs: isinstance(attrs, dict) and attrs.get("kind") in ("model", "product")
    )
    unresolved = obs_df[(obs_df["resolution_status"] == "unresolved") & is_model_or_product]
    if unresolved.empty:
        return pd.DataFrame(columns=["surface", "mention_count"])

    counts = (
        unresolved.groupby("surface")
        .size()
        .rename("mention_count")
        .reset_index()
        .sort_values(["mention_count", "surface"], ascending=[False, True])
        .head(top_n)
        .reset_index(drop=True)
    )
    return counts[["surface", "mention_count"]]


def review_sample(conn: sqlite3.Connection, sample_size: int, seed: int) -> pd.DataFrame:
    """수동 정확도 검토용 고정 시드 표본을 성공한 최신 extraction에서 뽑는다.

    title-only story(text 없음)와 self-post(text 있음)를 모두 있으면 포함하도록
    두 그룹으로 나눠 sample_size를 절반씩 배분하고(한쪽이 없으면 전량을 다른
    쪽에 배분), 그룹별 표본과 최종 순서 셔플을 모두 `random_state=seed`로
    고정한다 — 같은 seed는 항상 같은 story_id 순서를 반환한다. 그룹 표본 수는
    실제 보유량으로 캡되므로(부족해도 죽지 않음) sample_size보다 적게 돌아올
    수 있다. 쓰기·부수효과 없음(Gold는 읽기 전용을 유지).
    """
    extractions = latest_successful_extractions(conn)
    if extractions.empty:
        return pd.DataFrame(columns=_REVIEW_COLUMNS)

    stories = pd.read_sql_query("SELECT id AS story_id, title, text FROM stories", conn)
    merged = extractions.merge(stories, on="story_id", how="left")
    has_text = merged["text"].fillna("").str.strip() != ""
    title_only, self_post = merged[~has_text], merged[has_text]

    if title_only.empty:
        quota_title_only, quota_self_post = 0, sample_size
    elif self_post.empty:
        quota_title_only, quota_self_post = sample_size, 0
    else:
        quota_title_only = sample_size // 2
        quota_self_post = sample_size - quota_title_only

    picked = pd.concat(
        [
            title_only.sample(n=min(quota_title_only, len(title_only)), random_state=seed),
            self_post.sample(n=min(quota_self_post, len(self_post)), random_state=seed),
        ]
    )
    if picked.empty:
        return pd.DataFrame(columns=_REVIEW_COLUMNS)
    picked = picked.sample(frac=1, random_state=seed).reset_index(drop=True)

    picked["normalized_text"] = picked.apply(
        lambda row: normalize_story_text(row["title"] or "", row["text"] or "")[1], axis=1
    )
    for col in _REVIEWER_FILL_COLUMNS:
        picked[col] = None
    return picked[_REVIEW_COLUMNS]
