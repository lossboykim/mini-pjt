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
- 미들웨어(`src/middleware.py`, Day 5): LangChain `AgentMiddleware` 훅으로 구현했다. `SummarizationMiddleware`로 긴 대화 이력을 요약하고, `SecretMaskingMiddleware`가 체크포인터에 저장되는 이력의 비밀값을 가리며(LangSmith 트레이스는 익명화 클라이언트로 추적), `RetryMiddleware`가 스로틀링·타임아웃 같은 일시 오류에 지수 백오프로 다시 시도한다. `MiddlewareChain`이 직접 만든 그래프에서 훅을 실행한다.
- MCP 서버 연동(`src/mcp_server.py`, Day 4): 장애 이력과 서비스 상태 조회를 FastMCP 서버(stdio)로 노출하고, 에이전트가 `langchain-mcp-adapters`의 `MultiServerMCPClient`로 호출한다. 도구 이름과 반환 형식은 로컬 구현과 같고, 어떤 경로로 호출했는지는 trace의 `mcp_call` 단계에 남긴다. 서버를 못 쓰면 로컬 구현으로 내려간다.
- Multi-Agent Supervisor(`src/supervisor.py`, Day 6): 감독자가 구조화 출력(`Route`)으로 다음에 일할 전문 에이전트를 고른다. `runbook_agent`(런북 검색), `ops_agent`(장애 이력·서비스 상태, MCP 경유), `action_agent`(조치 승인 요청)가 각자 자기 도구만 쓴다. 위임 횟수 한도와 연속 재지명 금지는 코드로 지키고, 가드레일·사람 승인·최종 응답은 감독자 바깥 노드에 그대로 둔다.
- Plan-Execute, 장기 메모리: 범위 제외.

1차·2차 평가는 모두 핵심 8개 패턴만 있는 코드에서 측정했고, 추가 패턴 세 개는 그 뒤에 순서대로 붙였다. 응답의 비밀값은 핵심 가드레일이 응답 단계에서 지우며 미들웨어 없이도 동작한다. 세 패턴은 `RunbookAgent(use_middleware=..., use_mcp=..., use_supervisor=...)`로 끌 수 있다.

## 3. 아키텍처
```
POST /query ─▶ guard ─▶ supervisor ─▶ 전문 에이전트(ReAct) ◀─▶ tools
                │          ▲  │        ├ runbook_agent: retrieve_runbook (쿼리 확장 → BM25+벡터(RRF) → 리랭킹)
                │          │  │        ├ ops_agent: search_incidents / get_service_status (MCP 서버 경유)
                │          │  │        └ action_agent: restart_service ─▶ approval(interrupt) ─▶ POST /approve ─▶ 승인 시에만 가짜 실행(실행 로그)
                │          └──┘ 일을 마친 에이전트는 감독자에게 돌아가고, 감독자가 다음 에이전트나 FINISH를 고른다
                │          └─▶ final(Pydantic 구조화 출력, 비밀값·표식 제거)
                └ 미들웨어: 턴 시작에 저장 이력 마스킹·이력 요약, 모든 모델 호출에 재시도
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
- 평가: `python evaluation/run_eval.py --round <N>`, RAGAS는 `evaluation/requirements-eval.txt`로 만든 `.venv-eval`에서 `evaluation/run_ragas.py --round <N>`
- MCP 서버는 에이전트가 필요할 때 `python -m src.mcp_server`를 stdio로 직접 띄운다. 따로 실행할 필요는 없다.
- Dockerfile이 있으나 **이 PC에 Docker가 없어 빌드 검증은 하지 못했다.**

## 5. RAGAS 평가 결과
실제 ragas 0.4.3을 Python 3.12 평가 전용 venv에서 돌렸다(케이스 7건 평균). 자체 구현이 아니다.

| 지표 | 2차 (핵심 8개) | 3차 (추가 패턴) | 4차 (규칙 보완) |
|---|---|---|---|
| context_recall | 0.762 | 0.726 | 0.762 |
| context_precision | 0.857 | 0.901 | 0.901 |
| faithfulness | 0.625 | 0.366 | 0.458 |
| answer_relevancy | 0.668 | 0.634 | 0.633 |

faithfulness·answer_relevancy가 낮은 것은 답변에 이력·상태 수치가 들어가는데 contexts에는 런북만 있기 때문이다. Supervisor를 붙인 뒤에는 운영 데이터 담당이 더 자주 불려 그런 수치가 늘었고, faithfulness가 더 내려갔다. 답이 틀려진 것이 아니라 contexts의 범위가 런북으로 한정된 데서 오는 수치다. 상세는 `evaluation/round2_report.md`, `round3_report.md`, `round4_report.md`.

## 6. 인-아웃 세트 통과율
고정 파일(`evaluation/test_queries.csv`, `judge_prompt.md`, `rubric.py`)은 바꾸지 않았고 해시를 결과 파일에 기록했다.

| 라운드 | 측정 커밋 | 전체 | 가드레일 |
|---|---|---|---|
| 1차 | `d2e75d0` (핵심 8개) | 11/16 (68.8%) | 4/4 |
| 2차 | `00ca0a3` (핵심 8개 + 개선) | 14/16 (87.5%) | 4/4 |
| 3차 | `29bf280` (추가 패턴 3개 적용) | 15/16 (93.8%) | 4/4 |
| 4차 | `4f7a2c4` (감독자 규칙 보완) | 15/16 (93.8%) | 4/4 |

- 개선폭: +3건(+18.8%p). 목표(13건 이상, 가드레일 100%) 달성.
- 주요 개선: 증상 질의는 런북 검색 우선, 서비스 이름 없는 질의용 전체 상태 요약, 승인/거부 결과와 실행 여부 안내, trace에 이력 요약 추가.
- 2차 실패 2건: P02(이력 조회 건너뜀), P04(승인 요청 문구 부재). 측정은 1회분이라 LLM 비결정성이 있다. 리포트: `evaluation/round1_report.md`, `evaluation/round2_report.md`.
- 3차·4차는 추가 패턴을 붙인 뒤의 측정이다. P02와 P04는 통과했고, 실패 1건은 두 번 모두 E03(증상 없는 재시작 요청, 거부 케이스)이다. 3차는 감독자가 조치 담당에게 넘기지 않아서, 4차는 거부된 조치가 trace의 도구 단계에 남지 않아서 실패했다. 두 원인을 각각 고쳤고, 마지막 수정 뒤에는 E03 한 건만 다시 판정해 통과를 확인했다. **마지막 수정 이후 16건 전체를 다시 재지는 않았다.** 리포트: `evaluation/round3_report.md`, `evaluation/round4_report.md`.

## 7. 트라이앤에러 회고
- ragas가 Python 3.14에서 설치되지 않아(scikit-network) 3.12 평가 venv를 따로 만들었다.
- 1차에서 재시작을 거부하면 "근거 없음"으로 덮이는 문제가 있어 승인 결과 안내 규칙을 추가했다.
- 서비스 이름 없는 증상 질의에서 상태 도구를 못 불러 `service="all"`을 추가했다.
- 미들웨어·MCP·Supervisor는 1차·2차 평가를 마친 뒤에 붙였다. 처음에는 LangChain `create_agent`와 `create_supervisor`로 갈아타는 방법을 검토했지만, 승인 대기(interrupt)와 trace·contexts 계약을 다시 만들어야 해서 접었다. 대신 기존 그래프에 감독자 노드를 더하고 `AgentMiddleware` 훅을 직접 실행하는 쪽을 택했다.
- MCP 도구는 호출마다 서버 프로세스를 새로 띄워 건당 3초쯤 느리다. 세션을 재사용하면 빨라지지만 프로세스 수명 관리가 필요해 남겨 두었다.
- Supervisor를 붙이자 2차에서 통과하던 E03이 깨졌다. 감독자 규칙을 "런북을 찾은 뒤에만 조치 담당에게 넘긴다"로 썼는데, 증상 없는 재시작 요청은 런북 검색이 비어서 승인 요청까지 가지 못했다. 조치 요청은 런북을 못 찾아도 조치 담당에게 넘겨 사람이 결정하게 했다.
- 그 뒤에도 E03이 실패해서 보니, 2차 측정 이후에 들어간 trace 변경(거부된 조치는 도구 단계를 남기지 않음)이 고정된 판정 규칙과 어긋나 있었다. 코드를 바꾸고 재측정하지 않으면 이런 어긋남을 놓친다는 것을 배웠다. 2차 당시의 기록 방식으로 되돌렸다.
- 남은 한계: contexts에 런북만 담겨 RAGAS faithfulness가 낮다. 장애 이력과 서비스 상태도 근거로 담는 것이 다음 개선 대상이다. 추가 패턴을 붙인 최종 코드의 16건 전체 재측정도 남아 있다.
- requirements.txt에서 주석 줄에 붙어 있던 langgraph-checkpoint-sqlite를 분리하고 누락 패키지를 보충했다.

## 8. 핵심 코드 위치
| 항목 | 위치 | 심볼 |
|---|---|---|
| 구조화 출력 | `src/agent.py:81` | `FinalAnswer` |
| 인젝션 가드 정규식 | `src/agent.py:72` | `INJECTION_RE` |
| 에이전트 클래스 | `src/agent.py:143` | `RunbookAgent` |
| 승인 대기(HITL) | `src/agent.py:319` | `_approval` |
| 최종 응답 | `src/agent.py:343` | `_final` |
| 질의 진입 | `src/agent.py:399` | `query` |
| 승인 재개 | `src/agent.py:408` | `approve` |
| 쿼리 확장 | `src/retriever.py:143` | `expand_query` |
| 하이브리드 검색 | `src/retriever.py:156` | `hybrid_search` |
| 리랭킹 | `src/retriever.py:181` | `rerank` |
| 장애 이력 조회 | `src/tools.py:37` | `query_incidents` |
| 가짜 조치 로그 | `src/tools.py:58` | `ActionLog` |
| 도구 정의 | `src/tools.py:92` | `make_tools` |
| API 서버 | `src/server.py:20` | `create_app` |
| 가드레일 판정 | `evaluation/rubric.py:154` | `judge_guardrail` |
| 평가 실행 | `evaluation/run_eval.py:75` | `main` |
