# 기간 분할 검색어 백필 설계

## 목적

AI 관련 검색어 기반 MVP의 180일 Bronze 백필을 완전하게 만든다. Algolia 결과가
검색어별 한 요청 범위에서 제한되는 문제를 피하기 위해, 긴 기간을 고정 길이 시간
구간으로 나눠 각각 수집한다.

## 범위

- 기존 `BROAD_KEYWORDS`와 `TRACKED_KEYWORDS`를 그대로 사용한다.
- story만 수집하고, story ID upsert로 검색어·시간 구간 경계의 중복을 제거한다.
- `--backfill DAYS`는 watermark를 변경하지 않는다.
- 기본 증분 수집의 2시간 overlap 동작은 변경하지 않는다.

HN 전체 story 수집이나 모델 API/세션 분석은 이 변경 범위에 포함하지 않는다.

## 동작

`BACKFILL_SLICE_DAYS = 7` 설정을 추가한다. `--backfill 180`은 UTC 기준
180일 범위를 최대 7일 길이의 반열린 구간 `[start, end)`으로 나눈다.

각 검색어와 각 구간에 대해 Algolia에 다음 필터를 보낸다.

```text
created_at_i >= start AND created_at_i < end
tags=story
```

한 구간의 결과 페이지가 Algolia 한계에 도달하면, 해당 구간을 반으로 재귀 분할해
더 작은 두 구간으로 다시 수집한다. 한 story가 구간 경계에서 중복되지 않도록 끝은
배타적으로 처리한다. 재귀적으로 더 이상 나눌 수 없는 1초 구간에서도 한계에
도달하면 오류로 중단하며, watermark는 갱신하지 않는다.

모든 검색어·구간 요청이 성공한 뒤에만 병합 결과를 upsert한다. 오류 시 부분 수집
결과를 DB에 쓰지 않는다. backfill 성공 후에는 기존과 같이 watermark를 건드리지
않는다.

## 인터페이스

- `search_keyword(session, keyword, start_ts, end_ts)`는 반열린 시간 구간만 검색한다.
- `collect_backfill(conn, session, since_ts, until_ts)`는 모든 키워드·구간 결과를
  모아 한 번에 upsert하고 처리 story 수를 반환한다.
- CLI 출력은 검색어별 처리 수와 최종 DB 총 건수를 보여 준다.

## 테스트·완료 기준

- 180일 범위를 빈틈·겹침 없이 7일 이하 구간으로 나눈다.
- 각 요청의 numeric filter가 시작 포함·끝 배타 구간을 표현한다.
- 페이지 한계 신호가 오면 정확히 두 하위 구간을 재조회하고 결과를 합친다.
- 1초 구간의 한계 신호는 예외를 내며 DB와 watermark를 바꾸지 않는다.
- backfill은 watermark를 갱신하지 않고, 기본 증분은 기존 overlap을 유지한다.
- 전체 테스트는 네트워크 없이 mock HTTP로 실행한다.
