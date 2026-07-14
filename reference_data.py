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
                conn.execute(
                    "INSERT OR IGNORE INTO model_aliases (alias_normalized, model_id) VALUES (?, ?)",
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
