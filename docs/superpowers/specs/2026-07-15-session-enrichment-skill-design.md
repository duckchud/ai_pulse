# 세션 기반 Silver 추출 스킬 설계

## 목표

Anthropic API 키 없이 Codex 세션이 AI Pulse의 미분석 Hacker News story를
Silver extraction 형식으로 분석하고 로컬 SQLite에 저장할 수 있게 한다.
이 스킬은 사용자가 Codex에 명시적으로 요청할 때만 실행한다.

## 범위

- 프로젝트 내부 `.codex/skills/ai-pulse-session-enrichment/`에 스킬을 둔다.
- 스킬은 처리 건수 제한을 받으며 기본적으로 작은 배치만 다룬다.
- 기존 `story_extractions` 테이블, prompt/model 버전, 상태 값과 JSON envelope을 유지한다.
- 기존 evidence 검증 규칙을 그대로 적용한다.

이번 변경은 Claude API 호출의 자동 대체, 스케줄 실행, 외부 API 공급자 추가를 포함하지 않는다.

## 구조와 데이터 흐름

```text
사용자 요청
  -> Codex 스킬
  -> 대상 story 조회 도구
  -> 세션 모델이 envelope JSON 작성
  -> parse_envelope + verify_evidence
  -> story_extractions 저장 도구
```

스킬은 `enrich.py`의 프롬프트와 검증 계약을 참조해 story마다 다음을 만든다.

- `relevant`
- 근거가 포함된 `observations`
- `document_sentiment`
- 선택적 `extensions`

근거 인용은 title 또는 정제된 본문에 정확히 존재해야 한다. 검증 실패 observation은
원문 결과에 유지하되 `verified: false`로 표시되어 Gold 기본 집계에서 제외된다.

## 로컬 도구 인터페이스

새 Python 보조 도구는 두 역할만 가진다.

1. 현재 `(prompt_version, model)` 조합으로 성공한 추출이 없는 story를 제한된 수만큼
   JSON으로 출력한다.
2. 스킬이 생성한 extraction JSON을 기존 검증 함수로 검증한 뒤 상태와 원문 응답을 포함해
   SQLite에 저장한다.

도구는 모델 API를 호출하지 않으며 `ANTHROPIC_API_KEY`를 읽지 않는다. DB 쓰기는
명시적으로 받은 한 story의 결과만 수행한다.

## 오류 처리

- 세션 모델의 결과가 JSON envelope이 아니면 `invalid_json`으로 저장한다.
- 저장 도구 실행 오류는 가능한 한 해당 story에 `failed` 상태와 오류 메시지를 남기고,
  남은 story 처리는 계속한다.
- story 조회 결과가 비어 있으면 DB를 바꾸지 않고 종료한다.

## 검증

- API 키가 없는 환경에서 대상 조회와 저장 도구를 테스트한다.
- 정상 envelope, malformed JSON, 존재하지 않는 evidence quote 각각의 상태와 저장값을 검사한다.
- 전체 `pytest -q`를 실행한다.

## 사용 예

`AI Pulse 세션 기반 추출 스킬로 미분석 story 5건을 추출해줘.`

스킬은 먼저 5건 이하의 입력을 읽고, 각 story의 결과를 작성·검증·저장한 다음
처리 건수와 성공/invalid/failed 수를 보고한다.
