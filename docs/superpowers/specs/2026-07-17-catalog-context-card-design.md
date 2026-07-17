# 카탈로그 컨텍스트 카드 설계

## 목적

세션 기반 Silver 추출(session_enrich)의 정규화 일관성을 높인다. 세션이 story를
분석하기 전에 현재 model_catalog가 아는 vendor/family/version/별칭을 컴팩트한
"컨텍스트 카드"로 보여 줘서, 같은 모델의 표기 변형을 놓치거나 패밀리를 혼동하는
오류를 줄인다. 카드는 지식 주입이지 스키마 제약이 아니다 — envelope의
schema-light(open-world payload) 원칙은 그대로 유지한다.

## 범위

- `reference_data.py`에 카드 생성 함수를 추가한다. 카드는 DB의 `model_catalog`
  + `model_aliases`에서 생성하며 손으로 쓰지 않는다(단일 소스).
- `session_enrich.py pending` 출력의 배치 머리에 카드를 1회 포함한다.
  story마다 반복하지 않는다.

다음은 이번 범위에 포함하지 않는다.

- 관계 필드(succeeds/family_of 등)와 계보 추론.
- 리뷰 결과를 자동 반영하는 피드백 루프. 피드백은 수동 프로세스로 돌린다:
  수동 리뷰 → 오류 태깅 → `data/model_catalog.json` 별칭 보강 →
  `catalog_version` 증가 후 재임포트 → 같은 시드 표본으로 전후 비교.
- `.codex/skills/ai-pulse-session-enrichment/SKILL.md` 수정. 세션은 이미
  pending 출력을 읽으므로 통로가 자동으로 연결된다.

## 동작

`render_context_card(conn) -> str`는 다음 형태의 텍스트를 만든다.

- 머리에 `catalog_version` 1개를 표기한다. 버전이 없거나(빈 카탈로그) 여러 개면
  아래 규칙을 따른다.
- 모델당 한 줄: vendor, family, version(없으면 생략), released_on(없으면 생략),
  그리고 정규화된 별칭 전체. 정렬은 (vendor, family, version, model_id)로
  결정적이다.
- 마지막에 open-world 규칙 한 줄을 고정 포함한다: 이 목록은 참고용 지식이며,
  목록에 없는 이름은 카탈로그에 끼워 맞추지 말고 surface 그대로 기록해
  unresolved로 보존한다.

빈 카탈로그면 빈 문자열 `""`을 반환한다(예외 없음). catalog_version이 여러 개
섞여 있으면 `db.catalog_version`과 같은 기준으로 ValueError를 발생시킨다.

`pending` 명령 출력은 JSON 배열에서 객체로 바뀐다.

```json
{"context_card": "...", "stories": [{"story_id": "...", "input": "..."}]}
```

카드가 빈 문자열이면 `context_card`는 `null`로 내보낸다. `stories` 원소 구조와
선정 로직(`--limit`, `--from-candidates`, `--seed`)은 변경하지 않는다.

## 인터페이스

- `reference_data.render_context_card(conn: sqlite3.Connection) -> str`
- `session_enrich.pending_stories(...)`는 그대로 두고, `main`의 pending 분기에서
  카드를 생성해 출력 객체로 감싼다.

## 테스트·완료 기준

- 카드에 catalog_version, 각 모델의 vendor/family/version/released_on, 정규화된
  별칭, open-world 규칙 문구가 포함된다.
- 같은 카탈로그면 카드 문자열이 항상 동일하다(결정적 정렬).
- 빈 카탈로그면 `""`을 반환하고, pending 출력의 `context_card`는 `null`이다.
- catalog_version이 2개 이상이면 ValueError.
- pending 출력이 `{"context_card", "stories"}` 객체이고 카드가 배치당 1회만
  나타난다. 기존 선정 로직 테스트는 `stories` 키 기준으로 계속 통과한다.
