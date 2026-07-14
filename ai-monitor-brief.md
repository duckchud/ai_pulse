# AI 담론 실시간 모니터링 — 프로젝트 브리프

> **상태: 과거 인수인계 문서.** 현재 구현의 단일 기준은
> [`docs/superpowers/specs/2026-07-14-ai-pulse-design.md`](docs/superpowers/specs/2026-07-14-ai-pulse-design.md)다.
> 아래의 Firebase, 닫힌 엔티티 타입, `story_entities` 관련 내용은 현재 MVP에 적용하지 않는다.

> Claude Code 인수인계용 문서. 아래 "핵심 결정"은 이미 논의로 확정된 사항이니 뒤집지 말고 이어서 진행할 것.

## 1. 목적

**과제 (데이터 분석 실전반 12기, 11주차 필수)**
- 요구: 임의의 분석 과제를 AI 기반으로 수행하고 결과를 리뷰
- 마감: 2026-07-19 21:00
- 제출: `pdf, ipynb, py, sql, ppt, pptx, twbx` / 최대 5개 · 총 50MB

**개인 목적**
- AI 뉴스를 매번 스킴하는 데 시간이 너무 걸림 → 실시간으로 "지금 뜨는 엔티티 / 여론"을 파악하는 도구가 필요

**분석 질문 (프로젝트의 척추)**
> 실시간 AI 담론에서 **어떤 엔티티(기업·모델·기술)가 부상하고, 무엇과 함께 언급되며, 여론(감성)은 어떤가?**

---

## 2. 핵심 결정 (확정 — 재논의 금지)

| 항목 | 결정 | 이유 |
|---|---|---|
| 데이터 소스 | **HackerNews (Algolia Search API)** | 인증 불필요, 키워드+시간 검색 가능, AI 토픽 시그널 품질 좋음 |
| Reddit | **이번엔 제외** | API가 승인 게이트로 막혀 있어(2025 말~) 마감 안에 접근 확보 불확실. Phase 2 후보 |
| 프레이밍 | **"데이터 분석 과제"로 포지셔닝** | 온톨로지/MCP를 *제출물*로 내세우면 "이게 분석 맞나" 리스크. 엔티티 추출은 "고급 분석 기법"으로 표현 |
| 엔티티 추출 | 분석 기법으로 사용 (형식적 온톨로지 X) | 위와 동일 |
| 저장소 | **SQLite** | 파이프라인·분석 증명엔 충분. Supabase(관리형 Postgres)는 Phase 2 |
| enrich 모델 | `claude-haiku-4-5` 기본, 필요시 `claude-sonnet-5` | 대량 처리 비용 |
| 엔티티 타입 | **닫힌 집합** 강제 | 정규화 → co-occurrence 분석 품질 |

**semantic layer**: 현재는 경량 버전(닫힌 엔티티 타입 + gold 지표 정의)으로 충분. dbt/정식 metric layer 격상은 Phase 2.

---

## 3. 아키텍처 (medallion)

```
Bronze  collector.py  → stories                         [완료]
   │                     (HN 원천 데이터)
   ▼
Silver  enrich.py     → story_enrichment, story_entities [완료]
   │                     (Claude로 엔티티·감성·주제 추출, canonical)
   ▼
Gold    analysis.py   → 부상 / co-occurrence / 감성 마트   [다음 단계]
   │
   ├─▶ analysis.ipynb   제출용 · 결과 리뷰               [이번 마감 목표]
   └─▶ mcp_server.py    개인 도구                         [Phase 2]

Phase 2 (마감 후): Supabase 이전 · 정식 semantic layer · MCP 호스팅 · Reddit 소스 추가
```

Gold 함수는 `analysis.ipynb`(제출)와 `mcp_server.py`(도구) **양쪽에서 재사용**되므로 순수 함수로 작성.

---

## 4. 현재 상태

- ✅ `collector.py` — HN 증분 수집 + 중복 제거 (SQLite 적재)
- ✅ `enrich.py` — 미분석 story를 Claude로 엔티티·감성·주제 추출
- ⏳ `analysis.py` — gold 마트 3종 (다음)
- ⏳ `analysis.ipynb` — 시각화 + **결과 리뷰**
- ⏳ `mcp_server.py` — Phase 2

---

## 5. 데이터 스키마 (SQLite: `ai_monitor.db`)

**stories** (Bronze)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | TEXT PK | Algolia objectID |
| source | TEXT | 'hackernews' |
| title, url, author | TEXT | |
| points, num_comments | INTEGER | |
| created_at | TEXT | ISO8601 |
| created_at_i | INTEGER | unix ts (증분 watermark 기준) |
| text | TEXT | self-post 본문 |
| matched_keywords | TEXT | 쉼표 구분 |
| fetched_at | TEXT | |

**meta**: `(key, value)` — `last_created_at_i` watermark 저장

**story_enrichment** (Silver): `story_id PK, sentiment, sentiment_reason, primary_topic, model, enriched_at`

**story_entities** (Silver): `story_id, name, type` — PK `(story_id, name, type)`
- `type` ∈ `{company, model, product, technology, person, org, concept}`

---

## 6. API 참고

**HackerNews Algolia Search** (인증 불필요)
- Endpoint: `https://hn.algolia.com/api/v1/search_by_date`
- 주요 파라미터: `query`, `tags=story`, `numericFilters=created_at_i>{ts}`, `hitsPerPage`(≤100), `page`
- 응답 hit 필드: `objectID, title, url, author, points, num_comments, created_at, created_at_i, story_text`
- rate limit 넉넉함. 키워드별 검색 후 `objectID`로 dedupe.

**GDELT** (Phase 2 뉴스 확장용) — 등록 불필요·완전 무료, 15분 갱신. GKG가 기사별로 테마·인물·지역·조직·감성 태깅 제공.

---

## 7. 다음 단계 — Gold 레이어 (`analysis.py`)

순수 함수 3종. `conn`(sqlite3) 받아 pandas.DataFrame 반환 권장.

```python
def emerging_entities(conn, window_hours=24, top_n=20):
    """최근 window vs 직전 window 언급량 비교 → 급상승 엔티티.
    반환: name, type, recent_count, prev_count, growth"""

def entity_cooccurrence(conn, min_count=2):
    """같은 story에 함께 등장한 엔티티 쌍 → 네트워크 엣지.
    반환: entity_a, entity_b, weight"""

def entity_sentiment(conn, entity=None):
    """엔티티별 감성 집계·추이.
    반환: name, positive, neutral, negative, net_score"""
```

**analysis.ipynb 구성**
1. gold 함수 로드 → 부상 엔티티 표/막대차트
2. co-occurrence 네트워크 (networkx / pyvis)
3. 엔티티별 감성 추이
4. **결과 리뷰 (과제 채점 핵심)** — 표본 N건을 뽑아 LLM이 뽑은 엔티티·감성이 실제로 맞는지 사람이 검수 → 정확도 산출 + 오류 유형 분류 + 개선 방향. (LG_KG 감사에서 하던 정확도 검수와 같은 방식)

---

## 8. 5일 계획 (오늘 7/14 → 마감 7/19 21:00)

- **D1 (오늘)**: collector 백필 + 스케줄 등록해 데이터 쌓기 시작, enrich 소량 실행 검증
- **D2**: `analysis.py` gold 3종 + `analysis.ipynb` 초안 (부상/co-occurrence/감성)
- **D3**: **결과 리뷰** 섹션 (LLM 추출 정확도 검수) — 채점 핵심이라 시간 확보
- **D4**: MCP 서버 래핑 (시간 되면, Phase 2 미리보기)
- **D5**: 정리 + 발표자료(ppt/pdf), 제출

---

## 9. 실행 방법 / 환경

- 환경: WSL2 + Windows, AWS
- 의존성: `pip install requests anthropic pandas networkx`
- API 키: `export ANTHROPIC_API_KEY=sk-...`

```bash
python collector.py --backfill 3          # 최근 3일 채우기
python enrich.py --limit 10               # 우선 10건 분석 검증
sqlite3 ai_monitor.db "SELECT name, type, COUNT(*) c FROM story_entities GROUP BY name, type ORDER BY c DESC LIMIT 15;"
```

- 실시간화: `collector.py`를 cron / GitHub Actions로 주기 실행(예: 30분), 뒤이어 `enrich.py`.

---

## 10. 제출물 체크리스트

- [ ] `collector.py`, `enrich.py`, `analysis.py` (수집·정제·분석 파이프라인)
- [ ] `analysis.ipynb` (분석 + **결과 리뷰**)
- [ ] 발표자료 `.pdf` 또는 `.pptx` (문제정의 → 파이프라인 → 인사이트 → 리뷰 → Phase 2 로드맵)
- [ ] (선택) `mcp_server.py` — 보너스

---

## 코딩 컨벤션
- 코드 식별자·파일명은 영어, 주석·설명은 한국어
- 설명은 간결하게, 과한 추상화보다 평이한 표현
