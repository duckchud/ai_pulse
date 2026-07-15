import pytest

import enrich
from enrich import enrich_story, parse_envelope, verify_evidence


def test_build_record_accepts_explicit_session_model():
    stable_input = {"title": "Qwen3 release", "text": "body"}

    record = enrich.build_record(
        "story-1", stable_input, "body", "succeeded", "raw", "{}", None,
        prompt_version="schema-free-v1", model="codex-session-v1",
    )

    assert record["prompt_version"] == "schema-free-v1"
    assert record["model"] == "codex-session-v1"


def test_verify_evidence_marks_exact_title_quote_verified():
    envelope = {"relevant": True, "observations": [{"surface": "Qwen3", "evidence": {"field": "title", "quote": "Qwen3 release"}, "attributes": {}}], "extensions": {}}
    verified = verify_evidence(envelope, {"title": "Qwen3 release announced", "text": ""})
    assert verified["observations"][0]["evidence_verified"] is True


def test_parse_envelope_rejects_missing_observations():
    with pytest.raises(ValueError, match="observations"):
        parse_envelope('{"relevant": true}')


def test_verify_evidence_handles_non_dict_evidence_without_raising():
    # 오염된 응답: evidence가 문자열/리스트여도 raise하지 않고 False로 표시하며,
    # 다른 key/attribute는 보존해야 한다.
    envelope = {
        "relevant": True,
        "observations": [
            {"surface": "Qwen3", "evidence": "Qwen3 release", "attributes": {"kind": "model"}},
            {"surface": "GPT-5", "evidence": ["title", "GPT-5"], "attributes": {"role": "subject"}},
        ],
        "extensions": {},
    }
    verified = verify_evidence(envelope, {"title": "Qwen3 release", "text": ""})
    assert verified["observations"][0]["evidence_verified"] is False
    assert verified["observations"][1]["evidence_verified"] is False
    # 원래 key/attribute 보존
    assert verified["observations"][0]["surface"] == "Qwen3"
    assert verified["observations"][0]["attributes"] == {"kind": "model"}
    assert verified["observations"][1]["evidence"] == ["title", "GPT-5"]


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.messages = self

    def create(self, **_kwargs):
        return _FakeResp(self._text)


def test_enrich_story_persists_invalid_json_for_malformed_reply(temporary_db):
    # parse는 되지만 envelope shape이 오염된 응답(observations가 리스트가 아님).
    # 예외가 전파되지 않고 invalid_json 행으로 저장되어야 한다(무한 재처리 방지).
    temporary_db.execute(
        "INSERT INTO stories (id, source, title, url, author, points, num_comments, "
        "created_at, created_at_i, text, matched_keywords, fetched_at) "
        "VALUES ('1','hackernews','Qwen3 release',NULL,'a',1,0,'2026-07-14T00:00:00Z',1,'body','LLM','x')"
    )
    malformed = '{"relevant": true, "observations": "not-a-list", "extensions": {}}'
    client = _FakeClient(malformed)

    status = enrich_story(client, temporary_db, "1", "Qwen3 release", "body")

    assert status == "invalid_json"
    row = temporary_db.execute(
        "SELECT status, parsed_json FROM story_extractions WHERE story_id='1' "
        "AND prompt_version=? AND model=?",
        (enrich.PROMPT_VERSION, enrich.EXTRACTION_MODEL),
    ).fetchone()
    assert row["status"] == "invalid_json"
    assert row["parsed_json"] is None
