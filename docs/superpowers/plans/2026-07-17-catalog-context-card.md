# Catalog Context Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject a deterministic, catalog-generated context card into the head of `session_enrich.py pending` output so session extraction reads stories with grounded model knowledge.

**Architecture:** `reference_data.render_context_card(conn)` renders `model_catalog` + `model_aliases` into one compact text card ending with the open-world rule. `session_enrich.main`'s pending branch wraps the existing story list in a `{"context_card", "stories"}` JSON object. Selection logic, envelope contract, and SKILL.md stay unchanged.

**Tech Stack:** Python 3.11, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-17-catalog-context-card-design.md`

## Global Constraints

- Use four-space indentation, English identifiers, and concise Korean comments.
- The worktree has unrelated line-ending-only changes across many files. Stage ONLY
  the files each task names (`git add <exact paths>`), inspect `git diff --cached --stat`
  before every commit, and never stage `ai_monitor.db`.
- Do not modify `.codex/skills/ai-pulse-session-enrichment/SKILL.md`, `enrich.py`,
  or `pending_stories`.

---

### Task 1: Render the context card from the catalog

**Files:**
- Modify: `reference_data.py` (append after `resolve_model`)
- Test: `tests/test_reference_data.py`

**Interfaces:**
- Produces `render_context_card(conn: sqlite3.Connection) -> str`.
- Reuses `db.catalog_version` for the single-version guard (raises `ValueError` on 2+ versions).

- [ ] **Step 1: Write the failing tests**

Add `import json` and extend the imports at the top of `tests/test_reference_data.py`:

```python
import json

import pytest

from db import connect, migrate
from reference_data import (
    import_catalog,
    normalize_alias,
    render_context_card,
    resolve_model,
)
```

Append these tests to the end of the file:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_reference_data.py -q`
Expected: ImportError — `render_context_card` does not exist yet.

- [ ] **Step 3: Implement `render_context_card`**

In `reference_data.py`, add to the imports:

```python
from db import catalog_version
```

Append after `resolve_model`:

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/test_reference_data.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add reference_data.py tests/test_reference_data.py
git diff --cached --stat
git commit -m "feat: render the catalog context card"
```

---

### Task 2: Wrap pending output with the context card

**Files:**
- Modify: `session_enrich.py:168-176` (the pending branch of `main`)
- Test: `tests/test_session_enrich.py`

**Interfaces:**
- `pending` CLI prints `{"context_card": str | null, "stories": [...]}` instead of a bare array.
- `pending_stories` is unchanged; existing tests keep passing.

- [ ] **Step 1: Write the failing tests**

`tests/test_session_enrich.py` already imports `json`, `sys`, `pytest`, `session_enrich`
and defines `_insert_story`. Add to its imports:

```python
from db import connect, migrate
from reference_data import import_catalog
```

Append these tests:

```python
def _run_pending_cli(monkeypatch, capsys, db_path) -> dict:
    monkeypatch.setattr(session_enrich, "DB_PATH", db_path)
    monkeypatch.setattr(sys, "argv", ["session_enrich.py", "pending", "--limit", "2"])
    session_enrich.main()
    return json.loads(capsys.readouterr().out)


def test_pending_cli_wraps_stories_with_context_card(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli.db"
    conn = connect(db_path)
    migrate(conn)
    _insert_story(conn, "11", "GPT-5 rumor", "body")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([
        {
            "model_id": "openai:gpt:5",
            "vendor": "OpenAI",
            "family": "GPT",
            "version": "5",
            "released_on": None,
            "release_source_url": "https://openai.example/gpt-5",
            "catalog_version": "v1",
            "aliases": ["GPT-5"],
        }
    ]))
    import_catalog(conn, catalog)
    conn.close()

    payload = _run_pending_cli(monkeypatch, capsys, db_path)

    assert payload["context_card"].startswith("Model catalog context (catalog_version: v1)")
    assert payload["context_card"].count("catalog_version:") == 1
    assert payload["stories"] == [
        {"story_id": "11", "input": {"title": "GPT-5 rumor", "text": "body"}}
    ]


def test_pending_cli_context_card_is_null_without_catalog(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli-empty.db"
    conn = connect(db_path)
    migrate(conn)
    _insert_story(conn, "12", "title", "body")
    conn.close()

    payload = _run_pending_cli(monkeypatch, capsys, db_path)

    assert payload["context_card"] is None
    assert [row["story_id"] for row in payload["stories"]] == ["12"]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_session_enrich.py -q`
Expected: the two new tests fail — CLI output is still a bare JSON array, so
`payload["context_card"]` raises `TypeError: list indices must be integers`.

- [ ] **Step 3: Wrap the pending output**

In `session_enrich.py`, add to the imports:

```python
from reference_data import render_context_card
```

Replace the pending branch of `main` (currently `session_enrich.py:168-176`):

```python
        if args.command == "pending":
            # 카드는 배치당 1회만 머리에 싣는다. 빈 카탈로그면 null.
            card = render_context_card(conn)
            print(
                json.dumps(
                    {
                        "context_card": card or None,
                        "stories": pending_stories(
                            conn, args.limit, args.from_candidates, args.seed
                        ),
                    },
                    ensure_ascii=False,
                )
            )
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass (existing `pending_stories` tests are unaffected).

- [ ] **Step 5: Commit**

```bash
git add session_enrich.py tests/test_session_enrich.py
git diff --cached --stat
git commit -m "feat: wrap pending output with the context card"
```
