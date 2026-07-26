# AI Pulse 정제 분석 보고서 설계

## 1. 목적

현재 `analysis.ipynb`와 `analysis_ko.html`은 Gold 분석을 재현하고 차트를 확인하는 데는 적합하지만, 독자가 핵심 결론을 빠르게 파악하는 정제 보고서로는 정보 밀도가 높고 실행 코드와 분석 설명이 섞여 있다.

이 설계의 목적은 현재 SQLite 데이터와 `analysis.py`의 Gold 함수를 사용해, 노트북과 분리된 한국어 독립형 HTML 보고서를 제공하는 것이다. 보고서는 일반 이해관계자가 첫 화면에서 결론을 이해할 수 있어야 하며, 기술 분석가가 뒤쪽 기술 부록에서 데이터 범위와 계산 기준을 검증할 수 있어야 한다.

## 2. 독자와 의사결정 지원

- 주 독자: 제품·전략·기술 이해관계자
- 보조 독자: 데이터/기술 분석가
- 지원할 질문:
  - 최근 수집된 Hacker News AI 모델 담론에서 어떤 모델 family가 증가하거나 유지되는가?
  - 모델들이 어떤 조합으로 함께 언급되는가?
  - story가 모델을 어떤 stance로 framing하는가?
  - 이 결과를 어디까지 신뢰할 수 있으며, 어떤 표본과 한계가 있는가?

보고서는 모델 성능의 우열이나 시장 점유율을 주장하지 않는다. 관측 대상은 수집된 Hacker News story의 담론이며, 차트와 문장은 상관관계·기술적 인과·전체 뉴스 생태계 대표성을 암시하지 않는다.

## 3. 구현 방식

추천 방식은 `build_report.py`와 독립형 `analysis_report.html`의 조합이다.

- `build_report.py`: SQLite를 읽고 Gold 함수 결과를 계산해 보고서용 데이터와 HTML을 생성한다.
- `analysis_report.html`: 실행 코드나 Jupyter UI 없이 요약, 차트, 해석, 기술 부록만 포함한다.
- 차트: Python에서 생성한 정적 SVG 또는 인라인 이미지로 HTML 안에 포함해 파일 하나로 이동·공유 가능하게 한다.
- 외부 CDN, 서버, API 호출, DB 연결은 최종 HTML에 필요하지 않다.
- `analysis.ipynb`는 탐색·재현·수동 검수용 원본으로 유지하며, 정제 보고서의 렌더링 계층으로 사용하지 않는다.

### 대안과 선택 이유

1. 노트북을 다시 HTML로 변환하는 방식은 실행 코드와 출력이 섞여 독자용 보고서로서의 초점이 약하다.
2. HTML과 JSON을 별도 배포하는 방식은 확장성은 있지만 현재 정적 보고서 목적에 비해 복잡하다.
3. 따라서 재생성 가능한 Python 빌더와 단일 HTML 산출물을 선택한다.

## 4. 데이터와 분석 기준

### 입력 데이터

- `ai_monitor.db`의 `stories`: 수집 story, 생성 시각, HN 참여도
- `model_catalog`: 버전 관리되는 모델 alias와 family/vendor 매핑
- `story_extractions`: 세션 기반 Silver extraction 및 evidence 검증 결과
- `analysis.py`: 읽기 전용 Gold 집계 함수

### 분모 구분

보고서의 각 분석은 서로 다른 분모를 사용하므로 화면에서 명시한다.

- Candidate 경로: catalog alias로 매칭된 수집 story 전체. family/version trend, lineup, co-occurrence에 사용한다.
- Extraction 경로: 최신 성공 extraction 중 `evidence_verified=True`인 observation. framing과 수동 검수 품질 지표에 사용한다.
- Extraction 수를 전체 수집 story 수처럼 해석하지 않는다.

### 시간 기준

- 기준일 `AS_OF`: `stories.created_at`의 최댓값
- 시계열·lineup·co-occurrence·framing: `AS_OF` 기준 최근 180일
- 급상승 비교: 최근 24시간과 같은 길이의 직전 24시간
- 시간대: 저장된 UTC timestamp 기준
- lineup 최신성 가중치: 기본 half-life 30일

### 지표 정의

- 주간 언급량: 해당 calendar bucket 안에서 family를 언급한 고유 story 수
- 급상승 delta: 최근 24시간 고유 story 수 - 직전 24시간 고유 story 수
- weighted count: story별 기여도를 `0.5 ** (age_days / 30)`으로 감쇠한 합
- co-occurrence: 같은 story 안에서 서로 다른 resolved family 쌍이 함께 나타난 story 수. story당 pair는 한 번만 센다.
- stance: story가 모델을 framing하는 open-world 속성. HN 댓글 감정, 조회수, 모델 자체의 입장을 뜻하지 않는다.

## 5. 보고서 구조

### 5.1 표지 및 분석 기준

첫 화면에 다음을 표시한다.

- `AI Pulse` 제목과 한국어 부제
- 기준일과 분석 기간
- 수집 story 수, 모델 catalog 수, 성공 extraction 수
- 분석 범위 한 줄 요약

### 5.2 핵심 요약

핵심 발견을 3~4개 문장으로 요약한다. 수치가 비어 있거나 표본이 부족하면 억지로 결론을 만들지 않고 “관측 불충분”으로 표시한다. 요약은 아래 증거 섹션과 같은 용어·분모를 사용한다.

### 5.3 모델 담론 추이

- 기본 시각화: 최근 180일 family별 주간 언급량 line chart
- 보조 시각화: 최근 24시간 대비 급상승 family horizontal bar chart
- 해석: 지속적인 상승과 단기 spike를 구분한다.
- 주의: 참여도 합계는 맥락 정보로만 표시하고 ranking 근거로 사용하지 않는다.

### 5.4 최근 모델 라인업

- 기본 시각화: vendor별 weighted count 상위 모델 bar chart
- 보조 표: model family/version, lifetime story count, weighted count
- 해석: 누적량과 최신성 가중량이 다를 수 있음을 설명한다.

### 5.5 함께 언급되는 모델 조합

- 기본 시각화: co-occurrence 상위 family pair horizontal bar chart
- 보조 표: pair와 shared story count
- 해석: 함께 논의되는 관계이지, 모델 간 성능 비교나 의존관계를 의미하지 않는다.

### 5.6 Story framing

- 기본 시각화: family별 stance 분포 stacked bar chart
- 보조 표: stance 원문 label과 story count
- 해석: extraction 표본에 한정된 결과이며, 빈 label과 unresolved label을 임의로 합치지 않는다.

### 5.7 신뢰도와 한계

다음 항목을 별도 영역으로 표시한다.

- collector keyword와 수동 실행에 따른 coverage 한계
- candidate 전체 수집본과 extraction 표본의 분모 차이
- catalog에 없는 모델은 candidate 경로에서 보이지 않을 수 있음
- lexical alias matching의 false positive 가능성
- framing은 evidence-verified extraction만 사용
- stance는 댓글 sentiment가 아님

### 5.8 기술 부록

- 데이터 테이블과 Gold 함수 이름
- `AS_OF`, lookback, bucket, half-life
- collection/prompt/catalog version
- 빈 결과 처리 규칙
- 보고서 재생성 명령
- 수동 검수 샘플과 precision/recall 지표의 상태

## 6. 시각 설계

- 밝은 배경과 검정/회색 본문을 기본으로 한다.
- 모델 family는 제한된 색상 팔레트로 일관되게 표시한다.
- 긍정·부정·중립·혼합은 색상만으로 구분하지 않고 범례, 직접 라벨, 표를 함께 제공한다.
- 모든 차트에는 발견 중심 제목, 기간·분모를 담은 부제, 축 단위가 있다.
- 절대량 비교 bar chart의 축은 0에서 시작한다.
- 긴 family/model label은 가로 막대 또는 충분한 좌측 여백으로 처리한다.
- 모바일 폭에서는 차트를 한 열로 쌓고 표는 가로 스크롤 가능하게 한다.
- 장식용 카드나 의미 없는 시각 요소는 추가하지 않는다.

## 7. 재생성과 오류 처리

- `build_report.py`는 DB가 없거나 필수 테이블이 비어 있을 때 명확한 오류를 낸다.
- Gold 결과가 비어 있으면 빈 차트 대신 “해당 기준에서 관측된 결과 없음”을 표시한다.
- 차트에 사용할 행 수는 상위 N개로 제한해 HTML 크기와 가독성을 관리한다.
- 생성 시 보고서 메타데이터에 데이터 기준일과 버전을 저장한다.
- HTML은 생성 시점의 정적 snapshot이며, DB가 바뀌어도 자동으로 변하지 않는다.

## 8. 검증 기준

구현 완료 시 다음을 확인한다.

1. `build_report.py`가 현재 `ai_monitor.db`에서 오류 없이 실행된다.
2. 결과 HTML에 코드 셀, Jupyter toolbar, 외부 네트워크 의존성이 없다.
3. 핵심 요약의 수치가 Gold 함수 결과와 일치한다.
4. candidate와 extraction 분모가 각 섹션에 표시된다.
5. 차트 제목, 기간, 단위, 범례가 모두 존재하고 긴 label이 잘리지 않는다.
6. 빈 결과·부분 표본·unresolved 상태가 과장 없이 표시된다.
7. `pytest -q`가 통과한다.
8. 최종 HTML을 데스크톱과 좁은 화면에서 열어 레이아웃과 차트가 깨지지 않는지 확인한다.

## 9. 산출물과 범위

구현 산출물은 다음 두 파일이다.

- `build_report.py`: 재생성 가능한 보고서 빌더
- `analysis_report.html`: 현재 DB 기준 정제된 한국어 보고서

이번 범위에는 웹 서버, 대시보드 필터, 자동 배포, 새로운 분석 지표, 모델 API 호출, DB 스키마 변경을 포함하지 않는다.
