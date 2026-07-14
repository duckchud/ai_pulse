# AI Pulse MVP 설계 명세

## 목표와 범위

Hacker News에서 수집한 AI 관련 story를 분석해, 세부 AI 모델의 언급 추세,
모델 간 비교 관계, story의 서술 톤을 파악한다. 이번 MVP는 Algolia 수동 수집,
로컬 SQLite, Claude 추출, Gold 분석, 제출용 노트북으로 한정한다.

Firebase, 댓글 원문, 자동 스케줄러, 외부 기사 크롤링, Supabase, MCP 서버는
Phase 2다. 결과는 HN 전체 여론이 아니라 **설정된 Algolia 검색어로 수집한 HN
story의 담론**으로 표현한다.

## 핵심 원칙

- Bronze 원문과 Silver LLM 응답은 훼손하지 않고 보관한다.
- Silver는 `stable envelope + open-world payload`다. 값의 분류를 닫힌 집합으로
  강제하지 않지만, Gold가 읽을 최소 구조는 고정한다.
- 모델 출시일과 벤치마크 점수는 LLM이나 HN 글이 아닌 공식 참조 데이터로 관리한다.
- Gold에서만 이름 정규화, 모델 패밀리/버전 연결, 집계를 수행한다.
- 근거가 없거나 불명확한 항목은 `unresolved`로 남기며 억지로 분류하지 않는다.

## 아키텍처

```text
config.py ── collector.py ── db.py ── SQLite Bronze: stories, meta
                                      │
                              enrich.py ── Silver: story_extractions
                                      │
                    model_catalog / model_aliases / benchmark_results
                                      │
                              analysis.py ── analysis.ipynb
```

`db.py`는 연결, DDL, 마이그레이션, upsert, transaction을 담당한다. `config.py`는
검색어, query version, 안전 구간, prompt version처럼 비밀이 아닌 설정을 관리한다.
API 키는 `ANTHROPIC_API_KEY` 환경변수만 사용한다.

## Bronze: Algolia 수집

`collector.py --backfill DAYS`는 지정 기간을 재수집하고 watermark를 갱신하지
않는다. 기본 실행은 마지막 성공 watermark보다 2시간 앞선 시점부터 다시 검색한다.
story ID PK upsert로 중복을 제거하고, 모든 검색과 적재가 성공했을 때만 watermark를
갱신한다.

검색어는 `broad`와 `tracked`로 나눈다. tracked에는 GPT, Claude, Gemini와 DeepSeek,
Qwen, Kimi, Moonshot AI, GLM-4, Zhipu AI, ERNIE Bot, Baidu ERNIE, Doubao, Hunyuan,
MiniMax, Baichuan, Yi-Large, 01.AI를 포함한다. 각 story에는 매칭 검색어와
`collection_query_version`을 보관한다.

## Silver: schema-free extraction

기존 `story_enrichment`, `story_entities`는 과거 closed-schema 이력으로 보존하고,
새 분석의 입력으로 쓰지 않는다. 새 테이블은 다음과 같다.

```sql
CREATE TABLE story_extractions (
  story_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL,
  raw_response TEXT,
  parsed_json TEXT,
  input_hash TEXT NOT NULL,
  input_char_count INTEGER NOT NULL,
  input_truncated INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  enriched_at TEXT NOT NULL,
  PRIMARY KEY (story_id, prompt_version, model)
);
```

`parsed_json`은 아래 envelope을 만족하는 유효 JSON이다. `attributes`와
`extensions` 내부 key/value는 자유롭다.

```json
{
  "relevant": true,
  "observations": [
    {
      "surface": "Claude Opus 4.7",
      "evidence": {"field": "title", "quote": "Claude Opus 4.7"},
      "attributes": {"kind": "model", "role": "subject", "stance": "positive"}
    }
  ],
  "document_sentiment": {
    "label": "positive",
    "evidence": {"field": "title", "quote": "beats competitor"}
  },
  "extensions": {}
}
```

관련이 없거나 확신할 수 없는 경우 `relevant: false`, 빈 observations 또는 생략된
attribute를 사용한다. LLM에는 정제된 title/text를 명확한 데이터 구획으로 전달하고,
본문의 명령을 따르지 말라고 지시한다. evidence quote는 코드가 해당 정제 원문의
부분 문자열인지 검사한다. 검증 실패 observation은 원문에 남기되 Gold 기본 집계에서
제외한다.

상태는 `succeeded`, `invalid_json`, `failed`다. 기본 실행은 현재 prompt/model 조합의
성공 행이 없는 story만 처리하고, `--retry-failed`는 실패 상태만 재시도한다. Gold는
각 story의 최신 성공 extraction만 사용한다.

## 참조 데이터

`model_catalog`은 `model_id`, vendor, family, version, released_on,
release_source_url, catalog_version을 보관한다. `model_aliases`는 원문 표기를
`model_id`에 연결한다. `benchmark_results`는 model ID, benchmark, metric, score,
evaluation conditions, measured_at, source URL을 보관한다.

출시일은 제공사 공식 발표 URL이 있는 경우에만, 성능은 평가 조건과 출처가 있는
경우에만 등록한다. 서로 다른 벤치마크나 조건의 점수는 단일 순위로 합산하지 않는다.

## Gold 분석

Gold는 읽기 전용 DataFrame 함수다. observation의 surface를 별칭 사전으로 연결해
두 단계로 집계한다.

```text
vendor + family  →  vendor + family + version
Anthropic/Claude →  Anthropic/Claude/Opus 4.7
```

버전이 없는 언급은 패밀리 집계에는 포함하고 버전 집계에서는 `unresolved version`으로
표시한다. 별칭 사전에 없는 값도 `unresolved`로 남긴다.

- `emerging_models(conn, as_of, group_level, window_hours=24, min_recent_count=2, top_n=20)`:
  고유 story 기준 최근/직전 시간창 언급 수, 증가량, 증가율, points 합계, 댓글 수 합계를 반환한다. 급상승 정렬은 증가량, 최근 언급 수 순이며 points·댓글은 보조 지표다.
- `model_cooccurrence(conn, as_of, group_level, min_count=2)`: 동일 story의 서로 다른
  해결된 모델 쌍을 story당 한 번만 집계한다.
- `model_framing_sentiment(conn, as_of, group_level, model_id=None)`: 검증된 evidence와
  대상별 stance가 있는 observation만 집계한다. 이는 HN 여론이 아니라 해당 모델을
  묘사한 story의 톤이다.

각 Gold 결과에는 `collection_query_version`, `prompt_version`, `catalog_version`,
`as_of`를 포함해 재현성을 보장한다.

## 검수·테스트·완료 기준

검수는 enrichment 성공 story에서 고정 seed로 뽑은 30건을 사용한다. title-only와
self-post를 모두 포함하고, 관련성, 모델 언급 precision/recall, evidence 일치,
family/version 매핑, stance를 사람이 판정한다. 오류는 누락, 과잉 추출, 잘못된 매핑,
근거 불일치, stance 오류로 분류한다.

pytest는 외부 API 없이 watermark 안전 구간, upsert, envelope/근거 검증,
invalid JSON, 재시도, 별칭 매핑, unresolved 처리, Gold 빈 결과와 시간창을 검증한다.
노트북은 데이터 수집 범위와 모든 version 값을 출력하고, Gold 결과와 표본 검수를
시각화한다.
