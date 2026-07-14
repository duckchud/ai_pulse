import pytest

from reference_data import import_catalog, normalize_alias, resolve_model


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
