# 규칙 기반 모델 후보 선별 설계

## 목적

6개월 규모의 Hacker News Bronze story를 외부 모델 API나 대화형 에이전트 세션에
의존하지 않고 선별한다. 이 단계의 산출물은 검증 가능한 모델 언급 후보와 근거이며,
모델별 언급 추세와 동시 언급 분석의 입력으로 사용한다.

자동 document sentiment와 observation stance 추출은 이 범위에서 제외한다.

## 원칙

- 모델명 규칙은 Python 코드에 하드코딩하지 않는다. `model_catalog`와
  `model_aliases`의 버전 관리된 참조 데이터를 유일한 규칙 원천으로 사용한다.
- 원문 story는 수정하지 않는다. 후보 결과에는 원문에서 확인 가능한 별칭과 근거
  위치를 함께 보관한다.
- 카탈로그에 없는 모델 표기를 억지로 해석하지 않는다. 미매칭 story의 재현 가능한
  탐색 표본을 제공해 사람이 카탈로그를 보완할 수 있게 한다.
- 선별은 네트워크와 모델 API를 호출하지 않는다. 같은 DB와 카탈로그 버전에서는
  같은 결과를 재현해야 한다.

## 데이터 모델

새 테이블 `story_candidates`를 둔다.

```sql
CREATE TABLE story_candidates (
  story_id TEXT NOT NULL,
  catalog_version TEXT NOT NULL,
  candidate_reason TEXT NOT NULL,
  matched_model_ids TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  selected_at TEXT NOT NULL,
  PRIMARY KEY (story_id, catalog_version),
  FOREIGN KEY (story_id) REFERENCES stories(id)
);
```

- `candidate_reason`은 현재 `catalog_alias_match`로 고정한다. 이후 별도 후보 규칙을
  추가할 때 출처를 구분하기 위한 필드다.
- `matched_model_ids`는 중복 없는 모델 ID의 JSON 배열이다.
- `evidence_json`은 각 매칭마다 `model_id`, `alias`, `field`(`title` 또는 `text`),
  `quote`를 가진 JSON 배열이다. `quote`는 저장된 원문 필드의 부분 문자열이어야 한다.
- 같은 `story_id`라도 카탈로그 버전이 다르면 별도 행을 보관한다. 현재 카탈로그
  버전으로의 재실행은 upsert하여 idempotent하게 갱신한다.

## 선별 흐름

1. 카탈로그를 DB에 임포트한다.
2. `candidate_selection.py`가 현재 `model_aliases`와 연결된 `model_catalog`을 읽는다.
   한 실행에 여러 catalog version이 섞여 있으면 오류로 중단한다.
3. 각 별칭을 대소문자 비구분 정규식으로 찾는다. 별칭 양끝이 영숫자일 때는 영숫자
   앞뒤를 허용하지 않아 `GPT`가 `gptology`에 매칭되지 않게 한다. 공백·하이픈·구두점
   차이는 `normalize_alias`와 동등한 토큰 경계로 허용한다.
4. title과 text에서 찾은 모든 근거를 수집한다. 같은 모델/필드/quote의 중복은 제거하고,
   모델 ID는 안정적으로 정렬한다.
5. 하나 이상의 근거가 있는 story만 `story_candidates`에 저장한다.

CLI는 다음을 제공한다.

```bash
python candidate_selection.py select
python candidate_selection.py unmatched-sample --sample-size 30 --seed 20260716
```

`select`는 후보 수를 출력한다. `unmatched-sample`은 현재 카탈로그 버전의 후보가
없는 story 중 고정 시드 표본을 JSON으로 출력하며 DB를 변경하지 않는다.

## Gold 사용 범위

후보 결과는 모델 언급의 결정론적 입력이다. 기존 Gold 결과와 혼동하지 않도록 후보
기반 분석은 별도 함수로 제공하거나, 명시적으로 candidate source를 선택해야 한다.
모델별 언급 추세와 동시 언급만 계산한다. sentiment와 stance는 Silver envelope이
없으므로 후보 기반 분석에서 반환하지 않는다.

## 오류 처리와 운영

- 카탈로그가 비어 있거나 catalog version이 하나가 아니면 명확한 오류를 낸다.
- JSON으로 저장하는 후보 근거가 검증되지 않으면 해당 story를 저장하지 않고 오류로
  중단한다. 부분 결과가 남지 않도록 story 저장 단위에서 트랜잭션을 사용한다.
- 새로운 별칭은 반드시 출처 URL이 있는 `data/model_catalog.json` 레코드에 추가한다.
  이후 카탈로그를 재임포트하고 `select`를 다시 실행한다.
- 세션 기반 `session_enrich.py`는 유지한다. 이는 자동 선별의 대체가 아니라, 탐색
  표본·애매한 사례의 수동 검수 경로다.

## 테스트와 완료 기준

- 제목과 본문에서의 별칭 매칭 및 evidence quote 보존
- 대소문자·하이픈·공백 차이 허용과 단어 경계 오매칭 방지
- 같은 모델의 다중 별칭과 중복 근거 제거
- 현재 catalog version의 idempotent upsert 및 새 catalog version의 이력 보존
- 빈 카탈로그/혼합 catalog version 오류
- 미매칭 표본의 대상 범위와 고정 시드 재현성
- 후보 기반 언급 추세·동시 언급의 빈 결과와 기본 집계

## 제외 범위

- Anthropic, OpenAI 등 외부 모델 API 호출
- 로컬 LLM 설치 또는 호출
- 자동 sentiment, stance, relevance 판단
- 180일 실제 백필 실행과 수동 검수 수행
