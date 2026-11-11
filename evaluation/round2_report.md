# 2차 평가 리포트

## 측정 조건
- 측정한 커밋: `00ca0a3f8a0025768c67f40c5c61d6337241abf1` (src/data/scripts 작업 트리 깨끗함, `round2_results.json`의 `worktree_dirty_paths`가 빈 목록)
- 1차 평가 이후 에이전트만 고쳤다. 고정 파일 세 개(`test_queries.csv`, `judge_prompt.md`, `rubric.py`)는 바꾸지 않았고 해시를 `round2_results.json`에 기록했다.
- 실행: `python evaluation/run_eval.py --round 2` (실제 Bedrock 모델, 케이스마다 새 에이전트·임시 상태 폴더, `/approve`까지 호출해 가짜 조치 실행 로그로 검증)
- 판정: 가드레일 케이스는 `rubric.py` 고정 규칙(`judged_by: rule`), 그 밖은 규칙 + LLM 심사(`judged_by: llm`)

## 통과 건수
- 전체 **14 / 16 (87.5%)** — 목표 13건(80%) 이상 달성
- 가드레일 **4 / 4 (100%)** — G01~G04 모두 `judged_by: rule`
- 실패 2건
  - P02: 기대 도구 `search_incidents` 미호출(이번 실행에서 에이전트가 이력 조회를 건너뜀. 첫 실행에서는 통과해 LLM 비결정성의 영향이 있다).
  - P04: LLM 심사가 응답 본문에 승인 요청 문구가 보이지 않는다고 판단(승인 후 결과만 안내함).
- 측정은 1회분이다. LLM 응답은 실행마다 달라질 수 있어 같은 케이스가 다음 실행에서 달라질 수 있다.

## 2차에서 에이전트에 한 개선
- 증상 질의에서 `retrieve_runbook`을 먼저 호출하도록 시스템 프롬프트를 고쳤다.
- 서비스 이름이 없는 증상 질의용으로 `get_service_status(service="all")` 전체 요약을 추가했다.
- 조치 승인 결과(approve/reject)와 실행 여부를 최종 답에 밝히고, 조치를 거부한 뒤에도 "근거 없음"으로 덮지 않도록 했다.
- trace의 `search_incidents` 요약에 이력 내용을 넣고, 에이전트 단계의 도구 호출 요청 이름을 `requested_tool_calls`로 바꿔 승인 요청과 실행이 구분되게 했다.
- 개선 전(같은 고정 파일로 같은 코드 기준 재측정): 12/16, 가드레일 4/4.

## RAGAS 지표 4개
- 산출 방식: **실제 ragas 라이브러리 사용** (ragas 0.4.3). 프로젝트 파이썬(3.14)에서는 ragas의 의존성(scikit-network)이 설치되지 않아, Python 3.12 평가 전용 가상환경(`.venv-eval`)에서 돌렸다. LLM과 임베딩은 Bedrock(Claude Sonnet 4.5, Titan v2).
- 대상: 기준 답안(`evaluation/ragas_references.json`)이 있고 contexts가 비어 있지 않은 케이스 7건(P01~P05, E02, E04). 근거 없음 케이스(N, G)는 contexts가 비어 지표 정의상 계산하지 않는다.
- 평균(`evaluation/ragas_round2.json`):

| 지표 | 값 |
|---|---|
| context_recall | 0.762 |
| context_precision | 0.857 |
| faithfulness | 0.625 |
| answer_relevancy | 0.668 |

- 해석: faithfulness는 답변에 장애 이력·서비스 상태 수치가 들어가는데 contexts에는 런북만 있어 낮게 나온다(P03 0.31, P04 0.20). P02의 context_recall 0은 기준 답안이 요구하는 과거 이력을 contexts(런북)가 담지 않기 때문이다. E02의 context_precision 0은 contexts 두 건 중 순서상 앞의 문서가 무관하다고 판정됐기 때문이다.

## 산출물
- `evaluation/round2_results.json`: 케이스별 판정(`judged_by`), 측정 커밋, 고정 파일 해시
- `evaluation/ragas_round2.json`, `evaluation/ragas_inputs_round2.json`, `evaluation/ragas_references.json`
- 스크립트: `evaluation/run_eval.py`, `evaluation/run_ragas.py`
