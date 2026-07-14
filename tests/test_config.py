from config import BROAD_KEYWORDS, COLLECTION_QUERY_VERSION, OVERLAP_SECONDS, TRACKED_KEYWORDS


def test_collection_configuration_has_overlap_and_chinese_model_keywords():
    assert COLLECTION_QUERY_VERSION == "v1"
    assert OVERLAP_SECONDS == 7_200
    assert "DeepSeek" in TRACKED_KEYWORDS
    assert "LLM" in BROAD_KEYWORDS
