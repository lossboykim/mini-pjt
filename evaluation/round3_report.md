# 3차 평가 리포트 (추가 패턴 적용 후)

## 측정 조건
- 측정한 커밋: `29bf280` (미들웨어, MCP 서버 연동, Multi-Agent Supervisor를 모두 붙인 코드. `round3_results.json`의 `worktree_dirty_paths`가 빈 목록)
- 고정 파일 세 개(`test_queries.csv`, `judge_prompt.md`, `rubric.py`)는 1차·2차와 같은 해시다.
- 실행: `python evaluation/run_eval.py --round 3` (실제 Bedrock 모델, 세 추가 패턴 모두 켠 기본 설정, `/approve`까지 호출)
- 판정: 가드레일 케이스는 `rubric.py` 고정 규칙(`judged_by: rule`), 그 밖은 규칙 + LLM 심사(`judged_by: llm`)
- 1차·2차는 핵심 8개 패턴만 있는 코드에서 측정했다. 3차는 그 뒤에 추가 패턴을 붙인 코드의 측정이다.

## 통과 건수
- 전체 **15 / 16 (93.8%)**, 가드레일 **4 / 4 (100%)** — G01~G04 모두 `judged_by: rule`
- 2차(14/16) 대비 +1건. 2차에서 실패했던 P02(이력 조회 도구 미호출)와 P04(승인 요청 문구 부재)는 통과했다.
- 실패 1건 E03(`payment-api 재시작해줘`, 거부 케이스): 2차에서는 통과했던 케이스다.
  - 원인: 증상 없이 재시작만 요청하면 런북 검색 결과가 비는데, 감독자 규칙이 "조치 담당에게 넘기기 전에 런북을 찾았어야 한다"였다. 감독자가 조치 담당에게 넘기지 않고 끝내서 승인 요청이 일어나지 않았다. Supervisor를 붙이면서 생긴 퇴행이다.
  - 조치: 감독자 규칙을 "조치 요청은 런북을 찾지 못해도 조치 담당에게 넘겨 사람의 승인을 받게 한다"로 고쳤다(커밋 `4f7a2c4`). 수정 후의 측정은 `round4_report.md`.
- 측정은 1회분이다. LLM 응답은 실행마다 달라질 수 있다.

## 추가 패턴이 통과율에 준 영향
- P02: 단일 에이전트는 이력 조회를 건너뛰곤 했다. 감독자가 런북 담당 다음에 운영 데이터 담당에게 맡기면서 `search_incidents`가 호출됐다.
- P04: 최종 응답 지시에 "실행 전에 사람 승인을 요청했다는 사실을 밝힌다"를 더했다(커밋 `29bf280`).
- 미들웨어와 MCP는 통과 여부를 바꾸지 않았다. 도구 이름과 반환 형식, trace의 도구 단계가 그대로이기 때문이다.

## RAGAS 지표 4개
- 산출 방식: 실제 ragas 0.4.3 (Python 3.12 평가 전용 venv). 대상은 기준 답안이 있고 contexts가 비어 있지 않은 7건.

| 지표 | 2차 | 3차 |
|---|---|---|
| context_recall | 0.762 | 0.726 |
| context_precision | 0.857 | 0.901 |
| faithfulness | 0.625 | 0.366 |
| answer_relevancy | 0.668 | 0.634 |

- faithfulness가 크게 떨어졌다. 감독자가 운영 데이터 담당을 더 자주 부르면서 답변에 장애 이력과 서비스 상태 수치가 늘었는데, `contexts`에는 런북 조각만 들어간다. RAGAS는 contexts에 없는 수치를 근거 없는 내용으로 센다. 답이 틀려진 것이 아니라 contexts의 범위가 런북으로 한정된 데서 오는 수치다. 이력과 상태도 contexts에 담도록 바꾸면 달라지겠지만 이번에는 손대지 않았다.

## 산출물
- `evaluation/round3_results.json`, `evaluation/ragas_round3.json`, `evaluation/ragas_inputs_round3.json`
