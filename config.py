DB_PATH = "ai_monitor.db"
ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
LOOKBACK_DAYS = 3
HITS_PER_PAGE = 100
REQUEST_PAUSE_SECONDS = 0.25
OVERLAP_SECONDS = 7_200
COLLECTION_QUERY_VERSION = "v1"
PROMPT_VERSION = "schema-free-v1"
# 세션이 만든 추출의 출처 라벨. 특정 하네스(Codex/Claude Code)를 가리키지 않는다.
SESSION_EXTRACTION_MODEL = "session-v1"
SESSION_BATCH_LIMIT = 5

BROAD_KEYWORDS = ["artificial intelligence", "LLM", "machine learning", "AI agent"]
TRACKED_KEYWORDS = [
    "GPT", "Claude", "Gemini", "DeepSeek", "Qwen", "Kimi", "Moonshot AI",
    "GLM-4", "Zhipu AI", "ERNIE Bot", "Baidu ERNIE", "Doubao", "Hunyuan",
    "MiniMax", "Baichuan", "Yi-Large", "01.AI",
]
KEYWORDS = BROAD_KEYWORDS + TRACKED_KEYWORDS
