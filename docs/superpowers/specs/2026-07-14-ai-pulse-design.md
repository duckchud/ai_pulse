# AI Pulse MVP 설계 명세

## 목표

Hacker News의 AI 관련 story를 수동으로 수집하고 Claude로 구조화한 뒤,
부상 엔티티·엔티티 간 동시 언급·감성을 분석한다. 결과는 데이터 분석 과제의
`analysis.ipynb`에서 시각화하고, LLM 추출 결과의 표본 검수를 포함한다.

이번 MVP의 완료 범위는 Algolia 기반 수집, SQLite 적재, Claude enrichment,
Gold 분석 함수 3개, 제출용 노트북이다.

## 범위와 제외 사항

포함:

- Hacker News Algolia Search API를 이용한 키워드·시간 기반 백필 및 증분 수집
- 로컬 SQLite 데이터베이스
- Claude Haiku 기반 엔티티·감성·주제 추출
- 부상 엔티티, co-occurrence, 감성 집계
- 수동 실행과 표본 기반 결과 검수

제외:

- cron, GitHub Actions 등 자동 수집 스케줄러
- Firebase API, Reddit, 댓글 원문 수집·분석
- 점수·댓글 수의 시계열 snapshot
- Supabase, 호스팅, MCP 서버

제외 항목은 과제 제출 뒤 Phase 2에서 검토한다.

## 아키텍처

```text
config.py ───────┐
                 ▼
collector.py → db.py → SQLite: stories, meta
                      │
enrich.py ────────────┼→ SQLite: story_enrichment, story_entities
                      ▼
                 analysis.py → pandas.DataFrame → analysis.ipynb
```

### `config.py`

비밀 값이 아닌 실행 설정을 제공한다. `DB_PATH`, Algolia endpoint, `LOOKBACK_DAYS`,
`HITS_PER_PAGE`, `KEYWORDS`, 요청 간격을 정의한다. API 키는 계속 환경변수
`ANTHROPIC_API_KEY`로만 읽으며 설정 파일에 저장하지 않는다.

### `db.py`

SQLite 영속성의 단일 진입점이다.

- 연결 생성과 `init_db(conn)`
- Bronze `stories`, `meta` 테이블 초기화
- Silver `story_enrichment`, `story_entities` 테이블 초기화
- watermark 조회·저장
- story upsert
- enrichment와 entity 저장

모든 저장 함수는 호출자가 명시적으로 전달한 `sqlite3.Connection`을 사용한다.
`collector.py`와 `enrich.py`는 테이블 DDL이나 SQL upsert를 직접 갖지 않는다.

### `collector.py`

Algolia를 호출하는 Bronze 수집 CLI다.

- 기본 실행: `meta.last_created_at_i` 이후 증분 수집
- `--backfill DAYS`: watermark를 변경하지 않고 최근 DAYS일을 재수집
- 키워드별 hit을 `objectID`로 병합하고 매칭 키워드를 합산
- `db.upsert_stories`로 저장
- 성공적으로 API 조회와 저장이 완료된 경우에만 증분 watermark 갱신

저장된 story의 PK는 Algolia `objectID`다. 재수집 시 `points`, `num_comments`,
`matched_keywords`, `fetched_at`만 최신 값으로 갱신한다.

### `enrich.py`

Silver enrichment CLI다. `story_enrichment`가 없는 story만 최신순으로 조회하고,
Claude 응답에서 감성·근거·대표 주제·엔티티를 저장한다.

엔티티 type은 다음 닫힌 집합만 허용한다.

```text
company, model, product, technology, person, org, concept
```

개별 story의 API·JSON 오류는 기록하고 다음 story로 진행한다. 실패 건은
enrichment 행이 없으므로 다음 수동 실행에서 재시도된다.

### `analysis.py`

입력으로 SQLite connection을 받고 pandas DataFrame을 반환하는 읽기 전용 순수
분석 함수 모듈이다. 노트북과 Phase 2 MCP가 같은 함수를 재사용할 수 있다.

| 함수 | 반환 열 | 규칙 |
| --- | --- | --- |
| `emerging_entities(conn, window_hours=24, top_n=20)` | `name`, `type`, `recent_count`, `prev_count`, `growth` | 최근 시간창과 바로 전 같은 시간창을 비교하고 `growth=(recent-prev)/max(prev,1)`로 계산한다. |
| `entity_cooccurrence(conn, min_count=2)` | `entity_a`, `entity_b`, `weight` | 한 story 내 서로 다른 엔티티의 정렬된 쌍을 한 번만 집계한다. |
| `entity_sentiment(conn, entity=None)` | `name`, `positive`, `neutral`, `negative`, `net_score` | `net_score=positive-negative`이며 `entity`가 있으면 해당 canonical name만 필터한다. |

Gold 함수는 데이터가 없을 때도 정의된 열을 가진 빈 DataFrame을 반환한다.

## 데이터 계약

기존 SQLite 테이블과 의미를 유지한다.

- `stories.id`: Algolia `objectID`, Bronze story의 PK
- `meta.last_created_at_i`: 마지막 성공 증분 수집의 최대 `created_at_i`
- `story_enrichment.story_id`: 분석된 story당 하나의 Silver 행
- `story_entities`: `(story_id, name, type)` 복합 PK; canonical entity 저장

`stories.created_at_i`는 Gold의 시간창 기준이다. 분석 시각이나 fetch 시각으로
대체하지 않는다. `sentiment`는 `positive`, `neutral`, `negative` 중 하나만 저장한다.

## 오류 처리

- HTTP는 429/5xx에 대해 재시도한다.
- 수집 중 API 조회 또는 DB 저장이 실패하면 watermark를 갱신하지 않는다.
- LLM 한 건의 실패는 전체 batch를 중단하지 않는다.
- 응답 JSON이 유효하지 않거나 허용되지 않은 entity type이면 해당 값은 저장하지 않는다.
- CLI는 성공·실패 건수와 사람이 확인 가능한 오류 메시지를 출력한다.

## 테스트

외부 API와 Claude를 호출하지 않는 pytest 단위 테스트를 작성한다.

- watermark의 초기값·조회·저장
- story PK 기준 중복 제거 및 갱신 필드
- 키워드별 hit 병합
- Gold 함수의 빈 DB, 이전 기간 0건, 최소 빈도 조건
- 엔티티 쌍의 중복 없는 집계와 감성별 집계

## 노트북 구성

1. 분석 질문, 데이터 소스, 수집 기간과 데이터 규모
2. Bronze → Silver → Gold 파이프라인 설명
3. 부상 엔티티 표와 막대차트
4. co-occurrence 네트워크
5. 엔티티별 감성 분포
6. 무작위 표본의 수동 검수: 엔티티·감성 정확도, 오류 유형, 개선 방향
7. 한계와 Phase 2 계획

노트북은 DB에 쓰지 않으며 Gold 함수를 호출해 결과를 재현한다.

## 수동 실행 절차와 완료 기준

```bash
python collector.py --backfill 3
python enrich.py --limit 10
```

반복 실행은 필요할 때 위 명령으로 수행한다. 자동 스케줄러는 이번 명세에 없다.

다음 조건을 모두 만족하면 MVP는 완료다.

- 재실행해도 story가 중복되지 않고 최근 지표가 갱신된다.
- 아직 분석되지 않은 story만 Claude enrichment 대상이 된다.
- Gold 함수 3종이 지정된 열을 가진 DataFrame을 반환한다.
- 노트북이 세 분석 결과와 LLM 표본 검수를 보여준다.
