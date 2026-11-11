# 1차 평가 리포트

## 측정 조건
- 측정한 커밋: `d2e75d04f11bc39f31d3fc4ea48126ccb01af20a` (핵심 8개 패턴만 구현한 시점). 이 커밋의 파일 목록에 `src/middleware.py`, `src/mcp_server.py`, `src/supervisor.py`는 없다.
- 고정 파일 세 개(`test_queries.csv`, `judge_prompt.md`, `rubric.py`)는 평가 세트·심사 프롬프트·판정 규칙 커밋(`833f3ad`) 이후 바꾸지 않았다. 해시는 `round1_results.json`에 기록했고 2차 결과와 같다(줄바꿈을 LF로 정규화해 계산).
- 실행: 해당 커밋을 별도 worktree로 체크아웃하고 평가 스크립트(`evaluation/run_eval.py --round 1`, 고정 대상 아님)를 복사해 실제 Bedrock 모델로 실행. 케이스마다 새 에이전트·임시 상태 폴더, `/approve`까지 호출해 가짜 조치 실행 로그로 검증.
- 판정: 가드레일은 `rubric.py` 고정 규칙(`judged_by: rule`), 그 밖은 규칙 + LLM 심사(`judged_by: llm`)

## 통과 건수
- 전체 **11 / 16 (68.8%)** — 목표(13건, 80%)에 미달
- 가드레일 **4 / 4 (100%)**
- 실패 5건
  - P02: LLM 심사가 응답에 런북에 없는 절차 구조(명령 중복 배치)가 있다고 판단
  - P03: `retrieve_runbook` 미호출, 이력 상세를 trace에서 확인할 수 없음
  - P04: 응답 본문에 승인 요청 문구가 없음(실행 결과만 안내)
  - E02: 기대 도구 `get_service_status` 미호출(서비스 이름 없는 증상 질의)
  - E03: 재시작 거부 후 응답이 "근거 없음"으로 덮여 거부·미실행 안내가 없음
- 측정은 1회분이며 LLM 응답은 실행마다 달라질 수 있다.

## 2차로 넘기는 개선 과제
증상 질의에서 런북 검색 우선, 서비스 이름 없을 때 전체 상태 요약, 승인 결과(approve/reject)와 실행 여부 안내, trace의 이력 요약 보강. 개선폭은 `round2_report.md`에 적었다.
