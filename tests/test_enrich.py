import pytest

import enrich
from enrich import parse_envelope, verify_evidence


def test_build_record_accepts_explicit_session_model():
    stable_input = {"title": "Qwen3 release", "text": "body"}

    record = enrich.build_record(
        "story-1", stable_input, "body", "succeeded", "raw", "{}", None,
        prompt_version="schema-free-v1", model="session-v1",
    )

    assert record["prompt_version"] == "schema-free-v1"
    assert record["model"] == "session-v1"


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
