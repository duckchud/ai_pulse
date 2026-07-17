import json

import pytest

from db import connect, migrate
from reference_data import (
    import_catalog,
    normalize_alias,
    render_context_card,
    resolve_model,
)


def test_resolve_model_uses_normalized_alias(tmp_path, temporary_db):
    path = tmp_path / "catalog.json"
    path.write_text('[{"model_id":"vendor:family:1","vendor":"Vendor","family":"Family","version":"1","released_on":"2026-01-01","release_source_url":"https://vendor.example/release","catalog_version":"v1","aliases":["Family 1"]}]')
    assert import_catalog(temporary_db, path) == 1
    assert resolve_model(temporary_db, "family-1")["model_id"] == "vendor:family:1"


@pytest.mark.parametrize(
    "value",
    ["Family 1", "family-1", "FAMILY_1", "  family   1  "],
)
def test_normalize_alias_collapses_variants_to_same_key(value):
    assert normalize_alias(value) == "family 1"


def test_import_catalog_allows_null_released_on(tmp_path, temporary_db):
    path = tmp_path / "catalog.json"
    path.write_text(
        '[{"model_id":"vendor:family:2","vendor":"Vendor","family":"Family",'
        '"version":"2","released_on":null,'
        '"release_source_url":"https://vendor.example/release2",'
        '"catalog_version":"v1","aliases":[]}]'
    )
    assert import_catalog(temporary_db, path) == 1
    row = temporary_db.execute(
        "SELECT released_on FROM model_catalog WHERE model_id = ?", ("vendor:family:2",)
    ).fetchone()
    assert row["released_on"] is None


@pytest.mark.parametrize(
    "missing_field", ["model_id", "vendor", "family", "catalog_version", "release_source_url"]
)
def test_import_catalog_rejects_record_missing_required_field(tmp_path, temporary_db, missing_field):
    record = {
        "model_id": "vendor:family:3",
        "vendor": "Vendor",
        "family": "Family",
        "version": "3",
        "released_on": None,
        "release_source_url": "https://vendor.example/release3",
        "catalog_version": "v1",
        "aliases": [],
    }
    del record[missing_field]
    path = tmp_path / "catalog.json"
    path.write_text("[" + __import__("json").dumps(record) + "]")

    with pytest.raises(Exception):
        import_catalog(temporary_db, path)

    count = temporary_db.execute("SELECT COUNT(*) AS n FROM model_catalog").fetchone()["n"]
    assert count == 0


def test_import_catalog_rejects_alias_mapping_to_two_models(tmp_path, temporary_db):
    path = tmp_path / "catalog.json"
    path.write_text(
        '[{"model_id":"vendor:family:4","vendor":"Vendor","family":"Family",'
        '"version":"4","released_on":null,'
        '"release_source_url":"https://vendor.example/release4",'
        '"catalog_version":"v1","aliases":["Shared Alias"]},'
        '{"model_id":"vendor:family:5","vendor":"Vendor","family":"Family",'
        '"version":"5","released_on":null,'
        '"release_source_url":"https://vendor.example/release5",'
        '"catalog_version":"v1","aliases":["Shared Alias"]}]'
    )

    with pytest.raises(Exception):
        import_catalog(temporary_db, path)

    # 원자적 임포트: 충돌하는 두 번째 레코드가 실패하면 첫 레코드도 커밋되지 않는다.
    count = temporary_db.execute("SELECT COUNT(*) AS n FROM model_catalog").fetchone()["n"]
    assert count == 0


def test_resolve_model_returns_none_for_unresolved_surface(temporary_db):
    assert resolve_model(temporary_db, "totally unknown model") is None


def test_import_catalog_is_idempotent_on_reimport(tmp_path, temporary_db):
    # 큐레이터가 같은 카탈로그 파일을 다시 임포트하는 현실 워크플로: 예외 없이
    # 파일 내용으로 수렴하고, 중복 행이 생기지 않으며, 별칭 해석도 유지된다.
    path = tmp_path / "catalog.json"
    path.write_text(
        '[{"model_id":"vendor:family:1","vendor":"Vendor","family":"Family",'
        '"version":"1","released_on":"2026-01-01",'
        '"release_source_url":"https://vendor.example/release",'
        '"catalog_version":"v1","aliases":["Family 1"]}]'
    )
    assert import_catalog(temporary_db, path) == 1
    # 두 번째 임포트도 성공하고 같은 count를 반환한다.
    assert import_catalog(temporary_db, path) == 1

    count = temporary_db.execute("SELECT COUNT(*) AS n FROM model_catalog").fetchone()["n"]
    assert count == 1
    alias_count = temporary_db.execute("SELECT COUNT(*) AS n FROM model_aliases").fetchone()["n"]
    assert alias_count == 1
    assert resolve_model(temporary_db, "family-1")["model_id"] == "vendor:family:1"


def test_reimport_reassigning_alias_to_different_model_still_raises(tmp_path, temporary_db):
    # 별칭이 다른 model_id로 재할당되면 idempotency가 이를 삼키지 않고 여전히 raise.
    first = tmp_path / "first.json"
    first.write_text(
        '[{"model_id":"vendor:family:1","vendor":"Vendor","family":"Family",'
        '"version":"1","released_on":null,'
        '"release_source_url":"https://vendor.example/release1",'
        '"catalog_version":"v1","aliases":["Shared Alias"]}]'
    )
    assert import_catalog(temporary_db, first) == 1

    second = tmp_path / "second.json"
    second.write_text(
        '[{"model_id":"vendor:family:2","vendor":"Vendor","family":"Family",'
        '"version":"2","released_on":null,'
        '"release_source_url":"https://vendor.example/release2",'
        '"catalog_version":"v1","aliases":["Shared Alias"]}]'
    )
    with pytest.raises(Exception):
        import_catalog(temporary_db, second)

    # 기존 매핑은 그대로 유지된다.
    assert resolve_model(temporary_db, "shared alias")["model_id"] == "vendor:family:1"


def _card_records() -> list[dict]:
    return [
        {
            "model_id": "vendor:family",
            "vendor": "Vendor",
            "family": "Family",
            "version": None,
            "released_on": None,
            "release_source_url": "https://vendor.example/family",
            "catalog_version": "v1",
            "aliases": ["Family"],
        },
        {
            "model_id": "vendor:family:1",
            "vendor": "Vendor",
            "family": "Family",
            "version": "1",
            "released_on": "2026-01-01",
            "release_source_url": "https://vendor.example/release",
            "catalog_version": "v1",
            "aliases": ["Family 1", "fam-one"],
        },
    ]


def test_render_context_card_lists_catalog_and_open_world_rule(tmp_path, temporary_db):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_card_records()))
    import_catalog(temporary_db, path)

    card = render_context_card(temporary_db)

    assert card.startswith("Model catalog context (catalog_version: v1)")
    assert "- Vendor Family — aliases: family" in card
    assert "- Vendor Family 1 (released 2026-01-01) — aliases: fam one, family 1" in card
    assert "unresolved" in card.splitlines()[-1]


def test_render_context_card_is_deterministic_across_insert_order(tmp_path):
    cards = []
    for name, records in (("a", _card_records()), ("b", list(reversed(_card_records())))):
        conn = connect(tmp_path / f"{name}.db")
        migrate(conn)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(records))
        import_catalog(conn, path)
        cards.append(render_context_card(conn))
        conn.close()
    assert cards[0] == cards[1]


def test_render_context_card_empty_catalog_returns_empty_string(temporary_db):
    assert render_context_card(temporary_db) == ""


def test_render_context_card_rejects_mixed_catalog_versions(temporary_db):
    for model_id, version in (("a", "v1"), ("b", "v2")):
        temporary_db.execute(
            "INSERT INTO model_catalog (model_id, vendor, family, release_source_url, catalog_version) "
            "VALUES (?, 'Vendor', 'Family', 'https://vendor.example', ?)",
            (model_id, version),
        )
    with pytest.raises(ValueError):
        render_context_card(temporary_db)
