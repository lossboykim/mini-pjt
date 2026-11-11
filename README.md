# 런북 대응 어시스턴트 (Agentic RAG)

## 1. 무엇을 푸나
쿠버네티스 위에서 도는 웹 서비스의 야간 당직 엔지니어·신규 운영자가 장애 증상을 물으면, 가짜 런북·장애 이력(SQLite)·서비스 상태 API를 근거로 대응 절차를 안내한다. 재시작 같은 위험 조치는 사람이 승인한 경우에만 가짜 도구로 실행한다. 목표는 복구 시간 단축이다. 자세한 기획은 `SERVICE.md`를 본다. 모든 데이터는 가짜이며, 가드레일 검증용 비밀값은 `FAKE-SECRET-` 로 시작한다.

## 2. 활용한 패턴 (Day 1~7)
핵심 8개 패턴
- 구조화 출력: LCEL chain + Pydantic (`FinalAnswer`, `ExpandedQueries`, `RerankResult`)
- ReAct: `agent` ↔ `tools` 루프
- RAG: 쿼리 확장 → BM25+벡터 하이브리드(RRF) → LLM 리랭킹
- 도구 다중: `retrieve_runbook`, `search_incidents`, `get_service_status`, `restart_service`
- 가드레일: 무승인 조치 금지, 비밀값 비노출, 런북에 없는 절차 금지(근거 없으면 contexts 비움), 프롬프트 인젝션 거부
- HITL: `interrupt()`로 승인 대기, `/approve`로 재개
- 트레이스: 응답 `trace` 필드와 LangSmith(환경 변수로 설정)
- 평가: 고정 평가 세트 16건 + LLM-as-Judge + RAGAS 4지표

추가 패턴(구현 순서: 미들웨어 → MCP 서버 연동 → Supervisor)
- 미들웨어(`src/middleware.py`): 미구현 — 시간 부족으로 제외했다.
- MCP 서버 연동(`src/mcp_server.py`): 미구현 — 시간 부족으로 제외했다.
- Multi-Agent Supervisor(`src/supervisor.py`): 미구현 — 시간 부족으로 제외했다(가장 먼저 빠지는 순서).
- Plan-Execute, 장기 메모리: 범위 제외.

따라서 1차·2차 평가는 모두 핵심 8개 패턴만 있는 코드에서 측정했다. 비밀값은 핵심 가드레일이 응답 단계에서 지우며, 미들웨어 없이 동작한다.

## 3. 아키텍처
```
POST /query ─▶ guard ─▶ agent(ReAct) ◀─▶ tools(retrieve_runbook / search_incidents / get_service_status)
                          │                  └ retrieve_runbook: 쿼리 확장 → BM25+벡터(RRF) → 리랭킹
                          ├─▶ approval(interrupt, restart_service 요청 시) ─▶ POST /approve ─▶ 승인 시에만 가짜 실행(실행 로그)
                          └─▶ final(Pydantic 구조화 출력, 비밀값·표식 제거)
응답: answer, contexts[{doc_id,text}], trace[{step,input,output}], status(completed|awaiting_approval), thread_id
```
- 상태·대화 이력은 LangGraph SqliteSaver에 저장한다. 벡터 색인은 커밋하지 않고 `python scripts/build_index.py`로 다시 만든다.
- 모델: Bedrock `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, 임베딩 `amazon.titan-embed-text-v2:0`(us-east-1).

## 4. 실행 방법
```
pip install -r requirements.txt
# .env(커밋하지 않음)에 AWS 자격 증명, 선택으로 LANGSMITH_TRACING/LANGSMITH_API_KEY 설정
python scripts/build_incidents_db.py   # 장애 이력 DB 재생성(data/incidents.db는 커밋돼 있음)
python scripts/build_index.py          # 벡터 색인 생성
uvicorn src.server:app --port 8000
curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question": "payment-api가 CrashLoopBackOff야"}'
curl -X POST localhost:8000/approve -H "Content-Type: application/json" -d '{"thread_id": "<id>", "decision": "approve"}'
```
- 테스트(Bedrock 호출 없음, 가짜 모델): `python -m pytest tests -q`
- 평가: `python evaluation/run_eval.py --round 2`, RAGAS는 `evaluation/requirements-eval.txt`로 만든 `.venv-eval`에서 `evaluation/run_ragas.py --round 2`
- Dockerfile이 있으나 **이 PC에 Docker가 없어 빌드 검증은 하지 못했다.**

## 5. RAGAS 평가 결과
실제 ragas 0.4.3을 Python 3.12 평가 전용 venv에서 돌렸다(2차, 케이스 7건 평균). 자체 구현이 아니다.

| 지표 | 값 |
|---|---|
| context_recall | 0.762 |
| context_precision | 0.857 |
| faithfulness | 0.625 |
| answer_relevancy | 0.668 |

faithfulness·answer_relevancy가 낮은 것은 답변에 이력·상태 수치가 들어가는데 contexts에는 런북만 있기 때문이다. 상세는 `evaluation/round2_report.md`.

## 6. 인-아웃 세트 통과율
고정 파일(`evaluation/test_queries.csv`, `judge_prompt.md`, `rubric.py`)은 바꾸지 않았고 해시를 결과 파일에 기록했다.

| 라운드 | 측정 커밋 | 전체 | 가드레일 |
|---|---|---|---|
| 1차 | `d2e75d0` | 11/16 (68.8%) | 4/4 |
| 2차 | `00ca0a3` | 14/16 (87.5%) | 4/4 |

- 개선폭: +3건(+18.8%p). 목표(13건 이상, 가드레일 100%) 달성.
- 주요 개선: 증상 질의는 런북 검색 우선, 서비스 이름 없는 질의용 전체 상태 요약, 승인/거부 결과와 실행 여부 안내, trace에 이력 요약 추가.
- 2차 실패 2건: P02(이력 조회 건너뜀), P04(승인 요청 문구 부재). 측정은 1회분이라 LLM 비결정성이 있다. 리포트: `evaluation/round1_report.md`, `evaluation/round2_report.md`.

## 7. 트라이앤에러 회고
- ragas가 Python 3.14에서 설치되지 않아(scikit-network) 3.12 평가 venv를 따로 만들었다.
- 1차에서 재시작을 거부하면 "근거 없음"으로 덮이는 문제가 있어 승인 결과 안내 규칙을 추가했다.
- 서비스 이름 없는 증상 질의에서 상태 도구를 못 불러 `service="all"`을 추가했다.
- 시간이 모자라 미들웨어·MCP·Supervisor는 만들지 못했다.
- requirements.txt에서 주석 줄에 붙어 있던 langgraph-checkpoint-sqlite를 분리하고 누락 패키지를 보충했다.

## 8. 핵심 코드 위치
| 항목 | 위치 | 심볼 |
|---|---|---|
| 구조화 출력 | `src/agent.py:72` | `FinalAnswer` |
| 인젝션 가드 정규식 | `src/agent.py:63` | `INJECTION_RE` |
| 에이전트 클래스 | `src/agent.py:132` | `RunbookAgent` |
| 승인 대기(HITL) | `src/agent.py:244` | `_approval` |
| 최종 응답 | `src/agent.py:264` | `_final` |
| 질의 진입 | `src/agent.py:305` | `query` |
| 승인 재개 | `src/agent.py:313` | `approve` |
| 쿼리 확장 | `src/retriever.py:141` | `expand_query` |
| 하이브리드 검색 | `src/retriever.py:154` | `hybrid_search` |
| 리랭킹 | `src/retriever.py:169` | `rerank` |
| 장애 이력 조회 | `src/tools.py:37` | `query_incidents` |
| 가짜 조치 로그 | `src/tools.py:58` | `ActionLog` |
| 도구 정의 | `src/tools.py:90` | `make_tools` |
| API 서버 | `src/server.py:20` | `create_app` |
| 가드레일 판정 | `evaluation/rubric.py:154` | `judge_guardrail` |
| 평가 실행 | `evaluation/run_eval.py:74` | `main` |
