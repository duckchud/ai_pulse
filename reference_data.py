"""reference_data.py — 출처가 있는 model_catalog/model_aliases 임포트와 별칭 해석.

Silver 단계 observation의 자유 텍스트 "surface"를 Gold가 vendor/family/version
정체성에 연결할 수 있도록, 벤더 공식 출처가 있는 model_catalog 레코드를 JSON에서
읽어 model_catalog + model_aliases에 적재하고, 정규화된 별칭으로 되찾는 조회
함수를 제공한다. 출시일/성능 수치는 이 모듈이 추정하지 않는다 — 소스 JSON에
있는 값만 그대로 저장한다.
"""

import json
import re
import sqlite3
from pathlib import Path

REQUIRED_CATALOG_FIELDS = ("model_id", "vendor", "family", "catalog_version", "release_source_url")


def normalize_alias(value: str) -> str:
    """소문자화 후 비영숫자 연속 구간을 공백 하나로 치환하고 양끝을 trim한다."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def import_catalog(conn: sqlite3.Connection, path: str | Path) -> int:
    """JSON 배열(model_catalog 레코드 + aliases)을 읽어 적재한다.

    필수 필드(model_id/vendor/family/catalog_version/release_source_url)가 없는
    레코드나, 이미 다른 model_id에 매핑된 별칭은 예외를 발생시킨다. 파일 전체를
    단일 트랜잭션으로 처리하므로, 레코드 하나라도 실패하면 그 임포트 전체가
    커밋되지 않고 catalog가 절반만 채워진 상태로 남지 않는다.

    반환값은 임포트된 catalog 레코드 수(별칭 개수는 포함하지 않는다).
    """
    records = json.loads(Path(path).read_text())
    count = 0
    try:
        for record in records:
            missing = [field for field in REQUIRED_CATALOG_FIELDS if not record.get(field)]
            if missing:
                raise ValueError(
                    f"catalog record missing required field(s): {', '.join(missing)}"
                )

            conn.execute(
                """
                INSERT INTO model_catalog (
                    model_id, vendor, family, version, released_on,
                    release_source_url, catalog_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    vendor = excluded.vendor,
                    family = excluded.family,
                    version = excluded.version,
                    released_on = excluded.released_on,
                    release_source_url = excluded.release_source_url,
                    catalog_version = excluded.catalog_version
                """,
                (
                    record["model_id"],
                    record["vendor"],
                    record["family"],
                    record.get("version"),
                    record.get("released_on"),
                    record["release_source_url"],
                    record["catalog_version"],
                ),
            )

            for alias in record.get("aliases", []):
                normalized = normalize_alias(alias)
                existing = conn.execute(
                    "SELECT model_id FROM model_aliases WHERE alias_normalized = ?",
                    (normalized,),
                ).fetchone()
                if existing is not None and existing["model_id"] != record["model_id"]:
                    raise ValueError(
                        f"alias '{normalized}' already resolves to {existing['model_id']}, "
                        f"cannot also map to {record['model_id']}"
                    )
                # 같은 model_id로의 재매핑은 무해한 no-op(재실행 대비 idempotent).
                # 다른 model_id로의 재할당은 위 pre-check에서 이미 raise되므로 여기
                # ON CONFLICT 경로에는 model_id가 바뀌지 않는 경우만 도달한다.
                conn.execute(
                    "INSERT INTO model_aliases (alias_normalized, model_id) VALUES (?, ?) "
                    "ON CONFLICT(alias_normalized) DO UPDATE SET model_id = excluded.model_id",
                    (normalized, record["model_id"]),
                )

            count += 1
    except Exception:
        conn.rollback()
        raise

    conn.commit()
    return count


def resolve_model(conn: sqlite3.Connection, surface: str) -> dict | None:
    """surface(원문 표기)를 정규화해 model_aliases에서 찾고, 매칭되는
    model_catalog 행을 dict로 반환한다. 해석되지 않으면 None."""
    row = conn.execute(
        """
        SELECT mc.* FROM model_aliases ma
        JOIN model_catalog mc ON mc.model_id = ma.model_id
        WHERE ma.alias_normalized = ?
        """,
        (normalize_alias(surface),),
    ).fetchone()
    return dict(row) if row is not None else None


# 카드 끝에 고정 포함하는 open-world 규칙. 카드는 지식 주입이지 스키마 제약이 아니다.
CONTEXT_CARD_RULE = (
    "이 목록은 참고용 지식이다. 목록에 없는 이름은 카탈로그에 끼워 맞추지 말고 "
    "surface 그대로 기록해 unresolved로 보존한다."
)


def render_context_card(conn: sqlite3.Connection) -> str:
    """model_catalog + model_aliases를 세션 주입용 컨텍스트 카드 텍스트로 만든다.

    빈 카탈로그면 ""를 반환하고, catalog_version이 2개 이상이면 ValueError.
    정렬이 결정적이라 같은 카탈로그는 항상 같은 카드를 만든다.
    """
    from db import catalog_version  # db.py가 이 모듈을 임포트하므로 순환 임포트 방지 위해 지연 임포트

    models = conn.execute(
        """
        SELECT model_id, vendor, family, version, released_on
        FROM model_catalog
        ORDER BY vendor, family, version, model_id
        """
    ).fetchall()
    if not models:
        return ""

    aliases: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT model_id, alias_normalized FROM model_aliases ORDER BY alias_normalized"
    ):
        aliases.setdefault(row["model_id"], []).append(row["alias_normalized"])

    lines = [f"Model catalog context (catalog_version: {catalog_version(conn)})"]
    for model in models:
        name = f"{model['vendor']} {model['family']}"
        if model["version"]:
            name += f" {model['version']}"
        if model["released_on"]:
            name += f" (released {model['released_on']})"
        model_aliases = ", ".join(aliases.get(model["model_id"], []))
        lines.append(f"- {name} — aliases: {model_aliases}" if model_aliases else f"- {name}")
    lines.append(CONTEXT_CARD_RULE)
    return "\n".join(lines)
