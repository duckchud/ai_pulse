# AI Pulse 인터랙티브 분석 대시보드 설계

## 목적

현재 `analysis_report.html`은 Python Matplotlib가 생성한 정적 SVG를 포함한다. 이
설계는 기존 Gold 분석 결과와 freshness 표시를 유지하면서, 보고서를 탐색 가능한
단일 HTML 대시보드로 전환한다.

독자는 별도 서버나 인터넷 연결 없이 HTML 파일을 열어 다음을 확인할 수 있어야 한다.

- 현재 데이터 범위와 extraction 신선도
- 모델별 언급량 추이와 단기 상승 모델
- 모델 라인업 및 동시 언급 관계
- extraction 기반 framing 결과와 그 한계

## 범위

포함:

- `build_report.py`의 시각화 렌더링을 Matplotlib SVG에서 Plotly.js 렌더링으로 교체
- 분석 결과를 HTML 내부 JSON 데이터로 직렬화
- Plotly.js 배포 파일을 HTML에 인라인 삽입하여 오프라인 실행 지원
- KPI, 핵심 인사이트, 탐색 차트 중심의 대시보드 레이아웃
- 기존 표를 차트의 접근 가능한 보조 표현으로 유지
- stale extraction 경고와 수집·추출 기준 시각 분리 표시

제외:

- Gold 분석 함수와 SQLite 스키마 변경
- 새로운 모델 추론 또는 데이터 수집 로직
- 서버 기반 API, 로그인, 실시간 자동 갱신
- 기존 미추적 `analysis.html`의 정리 또는 삭제

## 사용자 경험

보고서는 다음 순서로 읽힌다.

1. 제목, 분석 범위, 수집 기준 시각과 extraction 기준 시각
2. 전체 story, 후보 모델, 성공 extraction, 최신 기준일 KPI
3. 핵심 인사이트와 데이터 신선도 경고
4. 모델 담론 추이와 신흥 모델 비교
5. 라인업, 동시 언급, framing 상세 분석
6. 방법론, 데이터 품질, 보조 표

차트는 전체 폭을 우선 사용한다. 데스크톱에서는 관련된 KPI와 간단한 비교 차트만
2열로 배치하고, 시간 추이·heatmap처럼 읽기 폭이 필요한 차트는 독립된 전체 행으로
배치한다. 모바일에서는 모든 영역을 1열로 전환한다.

## 데이터 흐름과 경계

분석 데이터의 권위 있는 출처는 기존 `analysis.py` Gold 함수다. `build_report_data()`가
현재처럼 함수를 호출하고, 결과를 JSON으로 변환 가능한 Python 기본 타입으로
정규화한다.

```text
SQLite -> analysis.py Gold functions -> build_report_data()
       -> report JSON + metadata + table rows
       -> HTML template + inline Plotly.js
       -> browser-side chart rendering
```

시각화 코드는 분석값을 재계산하지 않는다. 브라우저는 전달받은 행을 series와 trace로
변환하고, hover·범례 토글·정렬·필터 같은 표시 동작만 처리한다. 따라서 HTML 안의
`reportData`는 생성된 차트의 재현 가능한 입력이 된다.

## 차트 계약

각 차트는 다음 정보를 가진다.

- `chart_id`: DOM 컨테이너와 테스트에서 사용하는 안정적인 식별자
- `data_key`: `reportData`의 입력 배열 키
- `chart_type`: Plotly trace/layout 조합
- `question`: 차트가 답하는 분석 질문
- `fallback`: 빈 데이터일 때 표시할 한국어 메시지
- `table_id`: 동일 데이터를 제공하는 접근 가능한 표

차트 매핑은 다음과 같다.

| 영역 | 분석 질문 | Plotly 표현 |
| --- | --- | --- |
| 모델 담론 추이 | 기간별 언급량이 어떻게 변했는가? | multi-series line, 범례 토글, hover |
| 신흥 모델 | 직전 24시간 대비 단기 상승 모델은 무엇인가? | 정렬된 horizontal bar |
| 모델 라인업 | 현재 관측된 모델의 상대적 위치는 어떤가? | ranked horizontal bar 또는 dot plot |
| 동시 언급 | 같은 story에서 어떤 모델 조합이 반복되는가? | heatmap, 빈약하면 ranked bar로 대체 |
| framing | 모델이 어떤 stance로 언급되는가? | stance별 stacked bar |

표현 규칙:

- 시간은 사람이 읽을 수 있는 UTC 날짜 라벨을 사용한다.
- 긴 모델명은 horizontal bar와 hover 텍스트로 처리한다.
- 색상만으로 series를 구분하지 않고 범례, hover, 표를 함께 제공한다.
- framing은 stale extraction이면 차트는 렌더링하되, 최신 story의 stance를 대표하지
  않는다는 경고를 차트 바로 위에 표시한다.
- 데이터가 없으면 Plotly 빈 캔버스 대신 기존 empty-state 문구와 보조 표를 표시한다.

## 인라인 Plotly.js와 파일 정책

HTML은 단일 파일로 배포한다. Plotly.js는 CDN 링크가 아니라 프로젝트가 관리하는
고정 버전 배포 파일에서 읽어 HTML에 인라인 삽입한다. 버전은 구현 시 명시하고,
빌드 결과에는 외부 `<script src="https://...">` 의존성이 없어야 한다.

인라인 데이터는 HTML-safe JSON으로 직렬화한다. `</script>`가 데이터 문자열에
포함되어도 script 태그가 조기 종료되지 않도록 이스케이프한다. 원문 story와 evidence
텍스트는 기존 표의 HTML escaping 규칙을 유지한다.

## 레이아웃과 스타일

- 기존의 절제된 보고서 톤과 한국어 텍스트를 유지한다.
- KPI는 작은 카드로 제한하고, 페이지 전체를 카드 격자로 만들지 않는다.
- 차트는 `height`, `min-height`, 반응형 폭을 안정적으로 지정한다.
- Plotly의 modebar는 다운로드·확대·초기화 같은 유용한 동작만 노출한다.
- 인쇄 시 차트의 정적 표현과 표가 남도록 `@media print` 스타일을 제공한다.
- 색상은 후보 모델 series, positive/negative framing, 경고 상태가 서로 혼동되지
  않도록 분리한다.

## 오류 처리와 품질 규칙

- Plotly 초기화 실패는 해당 차트 컨테이너에 한국어 오류 메시지를 표시하고, 보조
  표는 계속 보여준다.
- 손상되거나 누락된 행은 전체 보고서를 중단하지 않고 해당 차트의 empty-state로
  처리한다.
- `as_of`와 `extraction_as_of`는 기존 freshness 판정 함수를 사용한다.
- HTML에 DB 파일, 비밀값, raw LLM 응답 전체를 삽입하지 않는다. 보고서에 필요한
  Gold 결과와 현재 표 데이터만 삽입한다.

## 테스트와 검증

구현 전 테스트를 먼저 추가한다.

- HTML에 `reportData` JSON과 모든 `chart_id` 컨테이너가 포함되는지 검증
- 외부 Plotly CDN script가 없고 인라인 Plotly bundle이 포함되는지 검증
- 각 차트 입력이 비어도 empty-state와 표가 생성되는지 검증
- stale extraction에서 경고 문구와 차트 데이터가 함께 유지되는지 검증
- 기존 report data 테스트와 모든 pytest 테스트 통과
- 생성된 HTML을 브라우저에서 열어 데스크톱·모바일 크기에서 차트가 비어 있지
  않고 겹치지 않는지 확인
- `git diff --check`와 HTML 내 외부 리소스 검색 수행

완료 기준은 `analysis_report.html`을 파일로 직접 열었을 때 인터넷 없이 KPI,
차트, hover, 범례 토글, 보조 표, freshness 경고가 동작하는 것이다.
