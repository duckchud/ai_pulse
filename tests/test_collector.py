from collector import effective_since, merge_hits


def test_effective_since_rewinds_by_two_hours():
    assert effective_since(10_000) == 2_800


def test_merge_hits_unions_keywords_and_query_version():
    hit = {"objectID": "10", "title": "DeepSeek", "created_at_i": 100}
    rows = merge_hits({"LLM": [hit], "DeepSeek": [hit]})
    assert rows[0]["matched_keywords"] == "DeepSeek,LLM"
    assert rows[0]["collection_query_version"] == "v1"
