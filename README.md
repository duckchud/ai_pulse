# AI Pulse

Hacker News AI-모델 담론(모델군/버전 트렌드, co-occurrence, 기사 프레이밍)을 분석하는
로컬 파이프라인이다. Bronze(`collector.py`) / Silver(`session_enrich.py`) / 참조 데이터
(`reference_data.py`) / Gold(`analysis.py`) / Presentation(`analysis.ipynb`) 구조를
따른다. 상세 설계는 `docs/superpowers/specs/2026-07-14-ai-pulse-design.md`를 참고한다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행 순서

```bash
python collector.py --backfill 3     # 최근 3일 Algolia HN 스토리 수집/업서트
pytest -q                            # 오프라인 테스트 스위트 실행
jupyter notebook analysis.ipynb      # Gold 산출물 시각화 + 수동 리뷰 샘플 확인
```

## Silver 추출은 세션에서 한다

이 파이프라인은 모델 API를 호출하지 않는다. Silver 추출은 에이전트 세션이 스킬
`ai-pulse-session-enrichment`을 따라 story를 직접 분석하고 그 결과를 SQLite에 저장한다.

```bash
python3 session_enrich.py pending --limit 5                     # 미추출 story 입력 받기
python3 session_enrich.py save --story-id ID --raw-file PATH    # 결과 1건 검증 후 저장
python3 candidate_selection.py select                            # catalog alias 후보 선별
python3 candidate_selection.py unmatched-sample --sample-size 30 --seed 20260716
```

- 후보 선별과 미매칭 샘플링은 오프라인 catalog 기반 명령이며 sentiment/stance를 의도적으로
  포함하지 않는다.
- 후보 Gold 집계는 `candidate_emerging_models`와 `candidate_model_cooccurrence`를 사용하며,
  두 결과는 catalog alias 후보만 읽고 sentiment/stance를 의도적으로 포함하지 않는다.
- `enrich.py`는 API 호출기가 아니라 envelope 계약(`EXTRACTION_CONTRACT`)과 입력 정규화·
  파싱·evidence 검증 코어다. `session_enrich.py`와 `analysis.py`가 이 코어를 공유한다.
- 세션이 만든 행은 `model = 'session-v1'`로 기록된다(`config.SESSION_EXTRACTION_MODEL`).
  특정 하네스를 가리키지 않는 중립 라벨이다.
- 참조 데이터(`data/model_catalog.json`, `data/benchmark_results.json`)에 새 레코드를
  추가할 때는 반드시 출처 URL(source URL)을 포함해야 한다. 검증되지 않은 모델 출시일이나
  벤치마크 점수는 기입하지 않는다.
- 로컬 `ai_monitor.db`는 커밋하지 않는다(`.gitignore`로 이미 제외됨).

## 로컬 스모크 테스트 결과

네트워크나 API 키 없이 실행한 실제 결과이다.

- `pytest -q` → `49 passed` (2026-07-16).
- `python collector.py --backfill 1` → 실행 270건 처리 / DB 총 270건 (2026-07-14),
  `ai_monitor.db` 생성 확인, `stories.id` 중복 없음
  (`GROUP BY id HAVING COUNT(*) > 1` 결과 0행).
- 세션 추출 → `story_extractions`에 `session-v1` 12건 `succeeded` 기록 (2026-07-16).
