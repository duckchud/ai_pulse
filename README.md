# AI Pulse

Hacker News AI-모델 담론(모델군/버전 트렌드, co-occurrence, 기사 프레이밍)을 분석하는
로컬 파이프라인이다. Bronze(`collector.py`) / Silver(`enrich.py`) / 참조 데이터
(`reference_data.py`) / Gold(`analysis.py`) / Presentation(`analysis.ipynb`) 구조를
따른다. 상세 설계는 `docs/superpowers/specs/2026-07-14-ai-pulse-design.md`를 참고한다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행 순서

```bash
python collector.py --backfill 3     # 최근 3일 Algolia HN 스토리 수집/업서트
python enrich.py --limit 10          # 스토리 10건을 evidence-backed JSON으로 추출
pytest -q                            # 오프라인 테스트 스위트 실행
jupyter notebook analysis.ipynb      # Gold 산출물 시각화 + 수동 리뷰 샘플 확인
```

### API 키 없이 세션에서 추출하기

프로젝트 내부 Codex 스킬 `ai-pulse-session-enrichment`은 사용자가 Codex에 명시적으로
요청한 소량의 story를 이 세션에서 분석해 SQLite에 저장한다. 이 경로는
`ANTHROPIC_API_KEY`가 필요 없고 Claude API를 호출하지 않는다. 이 수동 세션 기반 경로와
달리 대량 자동 실행은 `enrich.py`와 유효한 API 키를 사용한다.

- `ANTHROPIC_API_KEY`는 `enrich.py`(Silver 추출) 실행 시에만 필요하다. `collector.py`,
  `pytest`, 노트북 열람 자체는 API 키 없이 동작한다.

```bash
python candidate_selection.py select                            # catalog alias 후보 선별
python candidate_selection.py unmatched-sample --sample-size 30 --seed 20260716
```

- 후보 선별과 미매칭 샘플링은 오프라인 catalog 기반 명령이며 sentiment/stance를 의도적으로 포함하지 않는다.
- 후보 Gold 집계는 `candidate_emerging_models`와 `candidate_model_cooccurrence`를 사용하며,
  두 결과는 catalog alias 후보만 읽고 sentiment/stance를 의도적으로 포함하지 않는다.
- 참조 데이터(`data/model_catalog.json`, `data/benchmark_results.json`)에 새 레코드를
  추가할 때는 반드시 출처 URL(source URL)을 포함해야 한다. 검증되지 않은 모델 출시일이나
  벤치마크 점수는 기입하지 않는다.
- 로컬 `ai_monitor.db`는 커밋하지 않는다(`.gitignore`로 이미 제외됨).

## 로컬 스모크 테스트 결과 (2026-07-14)

API 키가 설정되지 않은 환경에서 실행한 실제 결과이다.

- `pytest -q` → `38 passed`, 네트워크/키 없이 통과.
- `python collector.py --backfill 1` → 실행 270건 처리 / DB 총 270건, `ai_monitor.db`
  생성 확인, `stories.id` 중복 없음(`GROUP BY id HAVING COUNT(*) > 1` 결과 0행).
- `python enrich.py --limit 3` → `ANTHROPIC_API_KEY` 미설정으로 3건 모두 `failed`로
  기록됨(성공 0 / invalid_json 0 / 실패 3). 스토리별로 예외를 개별 처리하므로 첫 실패 후에도
  중단되지 않고 3건 모두 시도했다. 실제 `succeeded` 추출을 확인하려면 유효한
  `ANTHROPIC_API_KEY`를 설정하고 재실행해야 한다.
