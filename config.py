DB_PATH = "ai_monitor.db"
ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
LOOKBACK_DAYS = 3
HITS_PER_PAGE = 100
REQUEST_PAUSE_SECONDS = 0.25
OVERLAP_SECONDS = 7_200
BACKFILL_SLICE_DAYS = 7
# Algolia는 페이지네이션 결과를 쿼리당 약 1,000건으로 제한한다(paginateLimitedTo).
ALGOLIA_MAX_RESULTS = 1_000
COLLECTION_QUERY_VERSION = "v2"
# v2: kind/role/stance/framing 슬롯의 의미를 계약에 정의(앵커 + unresolved 탈출구).
# v1은 슬롯 정의가 없어 stance에 기사 장르가 섞였다 — 두 버전은 섞어 집계하지 않는다.
PROMPT_VERSION = "schema-free-v2"
# 세션이 만든 추출의 출처 라벨. 특정 하네스(Codex/Claude Code)를 가리키지 않는다.
SESSION_EXTRACTION_MODEL = "session-v1"
SESSION_BATCH_LIMIT = 5

BROAD_KEYWORDS = ["artificial intelligence", "LLM", "machine learning", "AI agent"]
TRACKED_KEYWORDS = [
    "GPT", "Claude", "Gemini", "DeepSeek", "Qwen", "Kimi", "Moonshot AI",
    "GLM-4", "Zhipu AI", "ERNIE Bot", "Baidu ERNIE", "Doubao", "Hunyuan",
    "MiniMax", "Baichuan", "Yi-Large", "01.AI",
    "Llama", "Mistral", "Perplexity", "Cohere",
]
KEYWORDS = BROAD_KEYWORDS + TRACKED_KEYWORDS
