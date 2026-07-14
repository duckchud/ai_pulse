import pytest

from enrich import parse_envelope, verify_evidence


def test_verify_evidence_marks_exact_title_quote_verified():
    envelope = {"relevant": True, "observations": [{"surface": "Qwen3", "evidence": {"field": "title", "quote": "Qwen3 release"}, "attributes": {}}], "extensions": {}}
    verified = verify_evidence(envelope, {"title": "Qwen3 release announced", "text": ""})
    assert verified["observations"][0]["evidence_verified"] is True


def test_parse_envelope_rejects_missing_observations():
    with pytest.raises(ValueError, match="observations"):
        parse_envelope('{"relevant": true}')
