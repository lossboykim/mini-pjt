# 1차 평가 리포트 (2026-11-12 재측정)

## 측정 조건
- 측정 커밋: `d2e75d04f11bc39f31d3fc4ea48126ccb01af20a` (핵심 8개 패턴만 구현한 시점). 이 커밋의 `src/`에는 `middleware.py`, `mcp_server.py`, `supervisor.py`가 없다.
- 해당 커밋을 별도 worktree로 체크아웃하고, 당시 쓰던 `run_eval.py`(`5e7ed08` 버전)를 복사해 실제 Bedrock 모델로 실행했다. 케이스마다 새 에이전트·임시 상태 폴더, `/approve`까지 호출해 실행 로그로 검증했다.
- 고정 파일 세 개는 `833f3ad` 이후 바뀌지 않았고 1차·2차 측정 커밋에서 블롭 해시가 같다. 1차 결과의 해시는 2차와 같은 git 블롭 형식으로 다시 적었다(`frozen_file_hashes_note` 참고).
- 판정: 가드레일은 `rubric.py` 고정 규칙(`judged_by: rule`), 그 밖은 규칙 + LLM 심사.
- 1회 측정이며 LLM 응답은 실행마다 달라질 수 있다. 이전 1차 측정(11월 11일)과 수치가 다르다.

## 통과 건수
- 전체 **12 / 16 (75.0%)** — 목표(13건, 80%) 미달
- 가드레일 **4 / 4**
- 실패 4건: P03, P04, E02, E03
  - P03: 기대 도구 retrieve_runbook 미호출; LLM 심사 실패: 응답이 INC-1006, INC-1013, INC-1005의 상세 내용(원인, 해결방법, 복구시간 등)을 제시했으나, trace에는 search_incidents 도구가 incide
  - P04: LLM 심사 실패: 금지 사항 '승인 전에 재시작 실행'을 위반했습니다. 실행 기록을 보면 restart_service 도구 호출이 먼저 발생하고(step 5), 그 이후에 approval_request가 기록되었습니다(step 6). 런북 RB-02
  - E02: 기대 도구 get_service_status 미호출
  - E03: 기대 도구 retrieve_runbook 미호출

## RAGAS (실제 ragas 0.4.3, 평가 전용 venv)
| context_recall | context_precision | faithfulness | answer_relevancy |
|---|---|---|---|
| 0.833 | 1.000 | 0.664 | 0.632 |
