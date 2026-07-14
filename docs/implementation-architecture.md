# AI Pulse 구현 아키텍처

> **Superseded.** 이 문서는 Firebase·자동 수집·닫힌 엔티티 타입을 전제로 한 이전
> 초안이다. 현재 구현 기준은
> [`superpowers/specs/2026-07-14-ai-pulse-design.md`](superpowers/specs/2026-07-14-ai-pulse-design.md)다.

## 1. 목표와 범위

목표는 Hacker News에서 AI 관련 담론을 지속적으로 수집해, **지금 주목받는
엔티티**, **함께 언급되는 엔티티**, **담론의 감성**을 분석하는 것이다.

이번 MVP는 로컬에서 실행되는 데이터 분석 파이프라인이다. 웹 서비스, Reddit,
Supabase, MCP 서버는 Phase 2로 둔다.

## 2. 결정 기록

| 선택지 | 채택 | 이유 |
| --- | --- | --- |
| 실시간 수집 | HN Firebase API | 최신·인기·Ask/Show 목록과 변경 항목을 직접 추적할 수 있다. |
| 과거 데이터 백필 | Algolia Search API | 기간·키워드 조건으로 기존 AI 관련 글을 빠르게 채울 수 있다. |
| 저장소 | SQLite | 단일 파일, SQL 분석, 제출물 재현성에 적합하다. SQLite는 로컬 DB다. |
| AI 필터 및 추출 | 키워드 1차 필터 + Claude 2차 추출 | 불필요한 API 비용을 줄이면서 구조화된 엔티티·감성 데이터를 얻는다. |
| 댓글 분석 | Phase 1에서는 수치만, 원문 분석은 Phase 2 | 댓글 트리 수집과 LLM 비용을 통제한다. |

## 3. 전체 흐름

```text
HN Firebase API              HN Algolia API
top/new/ask/show/updates     keyword + period backfill
           │                         │
           └───────┬─────────────────┘
                   ▼
      [Collector: 조회 · 필터 · upsert]
                   │
                   ▼
          SQLite / Bronze: stories
                   │
                   ▼
       [Enricher: Claude 구조화 추출]
                   │
                   ▼
 SQLite / Silver: story_enrichment, story_entities
                   │
                   ▼
       [Analysis: 순수 SQL/Pandas 함수]
                   │
          ┌────────┴────────┐
          ▼                 ▼
 analysis.ipynb       향후 mcp_server.py
```

## 4. 수집 계층 (Bronze)

### 4.1 Firebase 실시간 수집

실행 주기는 15~30분이다. Firebase는 목록에서 ID만 반환하므로 각 ID에 대해
`/v0/item/{id}.json`을 조회한다.

1. `topstories`, `newstories`, `askstories`, `showstories`의 ID 목록을 가져온다.
2. 합집합을 만든 뒤 `item/{id}`를 병렬 제한(예: 최대 10개)으로 조회한다.
3. `type == "story"`인 공개 항목만 남긴다.
4. 제목과 본문에서 AI 키워드를 1차 판별한다.
5. `stories`에 `id` 기준 upsert한다.
6. `updates.json`의 변경 item ID 중 이미 저장한 story를 다시 조회해 점수와
   댓글 수(`descendants`)를 갱신한다.
7. 이번 실행 시각, 마지막 `maxitem`, 마지막 성공 상태를 `meta`에 기록한다.

Firebase 목록의 순위도 저장해야 시간에 따른 화제성을 분석할 수 있다. 따라서
`story_snapshots` 테이블에 수집 시각별 `score`, `comment_count`, `feed`, `rank`를
append-only로 저장한다.

### 4.2 Algolia 백필

`collector.py --backfill N`은 기존처럼 AI 키워드와 기간으로 검색한다. Firebase
실시간 수집과 같은 `stories` 테이블로 적재하므로 중복은 source ID PK로 제거된다.

## 5. 데이터 모델

기존 테이블은 유지하고 아래 두 테이블을 추가한다.

```sql
CREATE TABLE story_snapshots (
  story_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  feed TEXT NOT NULL,
  feed_rank INTEGER NOT NULL,
  points INTEGER,
  num_comments INTEGER,
  PRIMARY KEY (story_id, observed_at, feed),
  FOREIGN KEY (story_id) REFERENCES stories(id)
);

CREATE TABLE collection_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  mode TEXT NOT NULL,          -- firebase_realtime | algolia_backfill
  status TEXT NOT NULL,        -- running | succeeded | failed
  discovered_count INTEGER DEFAULT 0,
  accepted_count INTEGER DEFAULT 0,
  upserted_count INTEGER DEFAULT 0,
  error_message TEXT
);
```

`stories`에는 원문과 최신 수치를, `story_snapshots`에는 시간별 변화량을 저장한다.
`story_enrichment`과 `story_entities`는 Silver 계층을 유지한다.

## 6. 분석 계층 (Gold)

`analysis.py`의 함수는 DB 연결을 입력으로 받고 DataFrame만 반환한다. 노트북이나
향후 MCP 서버의 상태에 의존하지 않는다.

| 함수 | 기준 데이터 | 결과 |
| --- | --- | --- |
| `emerging_entities` | 최근/이전 동일 시간창의 엔티티 언급 수 | 급상승 엔티티와 성장률 |
| `entity_cooccurrence` | 동일 story의 entity 조합 | 엔티티 쌍과 가중치 |
| `entity_sentiment` | entity + enrichment | 긍정·중립·부정 수와 순감성 |
| `story_momentum` | story_snapshots | 점수·댓글 증가량과 피드 순위 변화 |

급상승 지표는 0으로 나누는 문제를 피하기 위해 다음처럼 계산한다.

```text
growth = (recent_count - previous_count) / max(previous_count, 1)
```

표본이 너무 작은 엔티티는 최소 최근 언급 수(예: 2)를 적용해 제외한다.

## 7. 모듈 구성

```text
collector.py            # 기존 Algolia 백필 호환 + CLI 진입점
firebase_collector.py   # Firebase 실시간 수집과 snapshot 저장
enrich.py               # 미분석 story의 Claude 구조화 추출
analysis.py             # Gold 순수 함수
db.py                   # DB 초기화, 공통 연결, 스키마 마이그레이션
config.py               # DB 경로, 키워드, 실행 주기 등 비밀이 아닌 설정
analysis.ipynb          # 결과 시각화·표본 검수·결과 리뷰
tests/                  # 키워드 필터, upsert, Gold 함수 테스트
```

MVP에서는 기존 파일을 무리하게 쪼개지 않는다. Firebase 수집기 구현 시 DB 초기화와
공통 upsert 로직이 중복되기 시작할 때만 `db.py`로 이동한다.

## 8. 실행과 운영

```text
초기 1회:  python collector.py --backfill 7
반복 실행:  python firebase_collector.py
분석 적재:  python enrich.py --limit 20
분석 확인:  Jupyter에서 analysis.ipynb 실행
```

cron 또는 GitHub Actions에서 아래 순서를 유지한다.

```text
firebase_collector → enrich --limit 설정값 → 실패 알림/로그 확인
```

SQLite는 동시 writer에 약하므로 수집과 enrich를 동시에 실행하지 않는다. 하나의
스케줄 작업에서 순차 실행하고, 각 실행은 `collection_runs`에 성공/실패를 남긴다.

## 9. 구현 순서

1. `.gitignore`에 로컬 DB·키를 유지한 상태에서 기본 파일을 첫 커밋한다.
2. `firebase_collector.py`에 feed ID 수집, item 조회, 키워드 필터, story upsert를 구현한다.
3. `story_snapshots`, `collection_runs`와 필요한 인덱스를 초기화에 추가한다.
4. `updates.json` 반영과 수집 실행 로그를 추가한다.
5. `analysis.py` Gold 함수 4개와 단위 테스트를 구현한다.
6. `analysis.ipynb`에서 차트와 LLM 추출 표본 검수 결과를 작성한다.

## 10. 완료 기준

- 30분 간격의 재실행으로 새 AI story가 중복 없이 쌓인다.
- 같은 story의 점수·댓글·피드 순위 변화가 snapshot으로 남는다.
- Claude 분석은 미분석 story만 처리하며, 허용된 엔티티 타입만 저장한다.
- 노트북에서 부상 엔티티, 관계망, 감성, 모멘텀을 재현할 수 있다.
- 표본 검수의 정확도와 오류 유형이 결과 리뷰에 포함된다.
