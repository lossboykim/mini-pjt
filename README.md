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
- MCP 서버 연동(`src/mcp_server.py`, Day 4): 장애 이력과 서비스 상태 조회를 FastMCP 서버(stdio)로 노출하고, 에이전트가 `langchain-mcp-adapters`의 `MultiServerMCPClient`로 호출한다. 도구 이름과 반환 형식은 로컬 구현과 같고, 어떤 경로로 호출했는지는 trace의 `mcp_call` 단계에 남긴다. 서버 프로세스와 세션은 첫 호출 때 한 번 열어 다시 쓰고(`_SessionWorker`), 끊기면 새로 연다. 서버를 못 쓰면 로컬 구현으로 내려간다.
- Multi-Agent Supervisor(`src/supervisor.py`, Day 6): 감독자가 구조화 출력(`Route`)으로 다음에 일할 전문 에이전트를 고른다. `runbook_agent`(런북 검색), `ops_agent`(장애 이력·서비스 상태, MCP 경유), `action_agent`(조치 승인 요청)가 각자 자기 도구만 쓴다. 위임 횟수 한도, 재지명 금지(한 질의에서 담당마다 한 번), 조치 담당은 사용자가 조치를 요청한 경우에만 넘기는 규칙은 코드로 지키고, 가드레일·사람 승인·최종 응답은 감독자 바깥 노드에 그대로 둔다.
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
- contexts에는 최종 답이 인용한 근거를 담는다: 런북 조각(`runbooks/...#chunk-N`), 장애 이력(`incidents/<ID>`), 서비스 상태(`status/<서비스>`). 근거가 없으면 비운다.
- 상태·대화 이력은 LangGraph SqliteSaver에 저장한다. 벡터 색인은 커밋하지 않고 `python scripts/build_index.py`로 다시 만든다.
- 모델: Bedrock `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, 감독자의 경로 선택만 `us.anthropic.claude-haiku-4-5-20251001-v1:0`, 임베딩 `amazon.titan-embed-text-v2:0`(us-east-1).
- 일일 한도 대체(`src/llm.py`): 처음 모델을 부르기 전에 설정 모델에 출력 1토큰짜리 요청을 보내 한도를 확인하고, 일일 한도를 다 썼으면 후보 목록의 다음 모델로 넘어간다. 실행 중 호출이 `Too many tokens per day`로 실패해도 다음 후보로 바꿔 같은 요청을 다시 보낸다. 후보 순서는 같은 모델의 다른 리전·추론 프로필(Sonnet 4.5: us-east-1 → us-west-2 → global) → Sonnet 4.6 → Haiku 4.5 → Nova Pro다. 에이전트·심사·RAGAS가 모두 따르고, 평가 결과 파일의 `llm` 항목에 실제로 쓴 모델과 대체 기록이 남는다. 임베딩(Titan)은 대체하지 않는다.

## 4. 실행 방법
```
pip install -r requirements.txt
# .env(커밋하지 않음)에 AWS 자격 증명, 선택으로 LANGSMITH_TRACING/LANGSMITH_API_KEY 설정
python scripts/build_incidents_db.py   # 장애 이력 DB 재생성(data/incidents.db는 커밋돼 있음)
python scripts/build_index.py          # 벡터 색인 생성
python scripts/check_bedrock.py        # 실행 전 모델별 한도 점검(쓸 모델이 없으면 종료 코드 1)
uvicorn src.server:app --port 8000
curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question": "payment-api가 CrashLoopBackOff야"}'
curl -X POST localhost:8000/approve -H "Content-Type: application/json" -d '{"thread_id": "<id>", "decision": "approve"}'
```
- 테스트(Bedrock 호출 없음, 가짜 모델): `python -m pytest tests -q`
- 평가: `python evaluation/run_eval.py --round <N>`(같은 코드를 반복 측정하면 `--run <K>`), RAGAS는 `evaluation/requirements-eval.txt`로 만든 `.venv-eval`에서 `evaluation/run_ragas.py --round <N>`
- MCP 서버는 에이전트가 필요할 때 `python -m src.mcp_server`를 stdio로 직접 띄운다. 따로 실행할 필요는 없다.
- Dockerfile이 있으나 **이 PC에 Docker가 없어 빌드 검증은 하지 못했다.**

## 5. RAGAS 평가 결과
실제 ragas 0.4.3을 Python 3.12 평가 전용 venv에서 돌렸다(케이스 7건 평균). 자체 구현이 아니다. 6차부터는 같은 코드로 3회 잰 평균이다.

| 지표 | 2차 (핵심 8개) | 3차 (추가 패턴) | 4차 (규칙 보완) | 5차 | 6차 (contexts 확장, 3회 평균) | 7차 (가린 줄 제거, 3회 평균) | 8차 (승인 기록·속도, 3회 평균) |
|---|---|---|---|---|---|---|---|
| context_recall | 0.762 | 0.726 | 0.762 | 0.762 | 1.000 | 1.000 | 1.000 |
| context_precision | 0.857 | 0.901 | 0.901 | 0.901 | 0.782 (0.73~0.82) | 0.786 (0.74~0.83) | 0.809 (0.78~0.83) |
| faithfulness | 0.625 | 0.366 | 0.458 | 0.356 | 0.826 (0.77~0.90) | 0.821 (0.82~0.83) | 0.791 (0.67~0.86) |
| answer_relevancy | 0.668 | 0.634 | 0.633 | 0.608 | 0.636 (0.62~0.65) | 0.647 (0.61~0.68) | 0.581 (0.55~0.61) |

5차까지 faithfulness가 낮았던 것은 답변에 이력·상태 수치가 들어가는데 contexts에는 런북만 있었기 때문이다. Supervisor를 붙인 뒤에는 운영 데이터 담당이 더 자주 불려 그런 수치가 늘었고, faithfulness가 더 내려갔다. 6차에서 장애 이력과 서비스 상태도 contexts에 담자 faithfulness가 0.36에서 0.83(3회 평균)으로, context_recall이 1.0으로 올랐다. 대신 contexts가 늘어 context_precision은 0.90에서 0.78로 내려갔다. 서비스 이름 없는 질의(E02)가 다섯 서비스의 상태를 모두 근거로 담는 것이 가장 크다. 7차에서 가린 비밀값 줄을 contexts에서 빼도 지표는 같은 수준이었다. 상세는 `evaluation/round2_report.md`~`round7_report.md`.

## 6. 인-아웃 세트 통과율
고정 파일(`evaluation/test_queries.csv`, `judge_prompt.md`, `rubric.py`)은 바꾸지 않았고 해시를 결과 파일에 기록했다.

| 라운드 | 측정 커밋 | 전체 | 가드레일 |
|---|---|---|---|
| 1차 | `d2e75d0` (핵심 8개) | 11/16 (68.8%) | 4/4 |
| 2차 | `00ca0a3` (핵심 8개 + 개선) | 14/16 (87.5%) | 4/4 |
| 3차 | `29bf280` (추가 패턴 3개 적용) | 15/16 (93.8%) | 4/4 |
| 4차 | `4f7a2c4` (감독자 규칙 보완) | 15/16 (93.8%) | 4/4 |
| 5차 | `7248453` (추가 패턴 최종 코드) | 15/16 (93.8%) | 4/4 |
| 6차 (3회) | `0ce0825` (contexts 확장, MCP 세션 재사용) | 16/16, 15/16, 15/16 | 4/4 (3회 모두) |
| 7차 (3회) | `9fc80b3` (contexts에서 가린 비밀값 줄 제거) | 16/16, 16/16, 14/16 | 4/4 (3회 모두) |
| 8차 (3회) | `e854957` (승인 기록, 응답 속도, 일일 한도 대체) | 16/16, 16/16, 15/16 | 4/4 (3회 모두) |

- 개선폭: +3건(+18.8%p). 목표(13건 이상, 가드레일 100%) 달성.
- 주요 개선: 증상 질의는 런북 검색 우선, 서비스 이름 없는 질의용 전체 상태 요약, 승인/거부 결과와 실행 여부 안내, trace에 이력 요약 추가.
- 2차 실패 2건: P02(이력 조회 건너뜀), P04(승인 요청 문구 부재). 측정은 1회분이라 LLM 비결정성이 있다. 리포트: `evaluation/round1_report.md`, `evaluation/round2_report.md`.
- 3차~5차는 추가 패턴을 붙인 뒤의 측정이다. P02와 P04는 세 번 모두 통과했다. 3차·4차의 실패 1건은 E03(증상 없는 재시작 요청, 거부 케이스)이다. 3차는 감독자가 조치 담당에게 넘기지 않아서, 4차는 거부된 조치가 trace의 도구 단계에 남지 않아서 실패했다. 두 원인을 각각 고쳤다.
- 5차는 그 수정까지 담은 최종 코드의 전체 재측정이다. E03은 통과했고, 이번에는 E02(원인 두 가지를 구분해 달라는 질의)가 한쪽 원인으로 단정해 실패했다. E02는 2차~4차에서 통과했고 호출한 도구도 같아서 실행마다 달라지는 폭으로 보지만, 여러 번 돌려 확인한 것은 아니다. 고치지 않고 그대로 기록했다.
- 3차~5차는 모두 15/16이지만 실패한 케이스가 다르다. 추가 패턴을 붙인 코드의 통과율은 "16건 중 15건 안팎"으로 읽어야 한다. 가드레일 4건은 1차부터 5차까지 한 번도 실패하지 않았다. 리포트: `evaluation/round3_report.md`, `round4_report.md`, `round5_report.md`.
- 6차는 처음으로 같은 코드를 3회 쟀다. 결과는 16/16, 15/16(E02), 15/16(P04)이다. 같은 코드에서도 실패 케이스가 매번 달라, 3차~5차의 한 건 차이가 실행마다 달라지는 폭이라는 것을 확인했다. 통과율은 "16건 중 15~16건"으로 읽는다. 가드레일은 3회 모두 4/4였다.
- 6차 2회의 E02 실패는 contexts 확장으로 생긴 새 유형이다. 장애 이력 메모의 가려진 비밀값 자리(`DB_PASSWORD=[REDACTED]`)를 심사 모델이 민감 정보 참조로 봤다. 실제 비밀값은 나가지 않았고 고정 규칙 검사는 통과했다. 리포트: `evaluation/round6_report.md`.
- 7차는 가린 비밀값이 있는 줄을 contexts에서 뺀 코드를 3회 쟀다. 결과는 16/16, 16/16, 14/16(P04, P05)이다. contexts에 `[REDACTED]`가 남은 경우는 0건이고, E02는 3회 모두 통과했다. P04(승인 요청 사실을 답에 쓰지 않음)는 6차·7차 여섯 번 중 두 번 실패해 실행에 따라 다시 나오는 약점이다. 6차·7차 여섯 번의 통과 건수는 16, 15, 15, 16, 16, 14건(평균 약 15.3건)이다. 리포트: `evaluation/round7_report.md`.
- 8차는 승인 기록을 코드로 붙이고 응답 속도를 줄인 코드를 3회 쟀다. 결과는 16/16, 16/16, 15/16(P03)이다. P04는 3회 모두 통과했다. 측정 중 us-east-1 Sonnet 4.5가 일일 한도에 걸려 세 번 모두 us-west-2의 같은 모델로 대체해 끝까지 쟀다. P03 실패는 Haiku 감독자가 조치 요청이 없는 질의를 조치 담당에게 넘겨 승인 대기로 끝난 것이다(실행은 되지 않았다). 사용자 질문에 조치 요청이 없으면 조치 담당에게 넘기지 않게 코드로 막았고, 9차에서 잰다. P04의 RAGAS faithfulness는 0.63에서 0.42로 내려갔다. 승인 기록 문장이 contexts(런북)에 없는 시스템 상태에서 나오기 때문이다. 리포트: `evaluation/round8_report.md`.

## 7. 트라이앤에러 회고
- ragas가 Python 3.14에서 설치되지 않아(scikit-network) 3.12 평가 venv를 따로 만들었다.
- 1차에서 재시작을 거부하면 "근거 없음"으로 덮이는 문제가 있어 승인 결과 안내 규칙을 추가했다.
- 서비스 이름 없는 증상 질의에서 상태 도구를 못 불러 `service="all"`을 추가했다.
- 미들웨어·MCP·Supervisor는 1차·2차 평가를 마친 뒤에 붙였다. 처음에는 LangChain `create_agent`와 `create_supervisor`로 갈아타는 방법을 검토했지만, 승인 대기(interrupt)와 trace·contexts 계약을 다시 만들어야 해서 접었다. 대신 기존 그래프에 감독자 노드를 더하고 `AgentMiddleware` 훅을 직접 실행하는 쪽을 택했다.
- MCP 도구는 처음에 호출마다 서버 프로세스를 새로 띄워 건당 3초쯤 걸렸다. 6차 전에 세션을 전용 스레드의 이벤트 루프에 열어 두고 요청을 큐로 넘기는 방식으로 바꿔, 첫 호출 뒤에는 약 0.002초가 됐다. stdio 세션은 연 작업 안에서 닫아야 해서(anyio 취소 범위) 세션을 여는 코루틴이 큐를 직접 돌게 했다. 평가 스크립트는 케이스마다 `RunbookAgent.close()`로 서버를 내린다.
- Supervisor를 붙이자 2차에서 통과하던 E03이 깨졌다. 감독자 규칙을 "런북을 찾은 뒤에만 조치 담당에게 넘긴다"로 썼는데, 증상 없는 재시작 요청은 런북 검색이 비어서 승인 요청까지 가지 못했다. 조치 요청은 런북을 못 찾아도 조치 담당에게 넘겨 사람이 결정하게 했다.
- 그 뒤에도 E03이 실패해서 보니, 2차 측정 이후에 들어간 trace 변경(거부된 조치는 도구 단계를 남기지 않음)이 고정된 판정 규칙과 어긋나 있었다. 코드를 바꾸고 재측정하지 않으면 이런 어긋남을 놓친다는 것을 배웠다. 2차 당시의 기록 방식으로 되돌렸다.
- 5차까지는 contexts에 런북만 담겨 RAGAS faithfulness가 낮았다(0.36). 6차에서 장애 이력과 서비스 상태도 근거로 담아 0.83(3회 평균)이 됐다.
- 5차까지는 라운드마다 1회만 재서 한 건 차이가 실행마다 달라지는 폭인지 구분할 수 없었다. 6차에서 같은 코드를 3회 재 16, 15, 15건을 얻었다.
- `.venv-eval`을 다시 만들자 `langchain-community` 0.4.2에서 ragas import가 깨졌다. 0.4.1로 고정했다.
- contexts 확장 뒤 장애 이력 메모의 가린 비밀값 자리(`DB_PASSWORD=[REDACTED]`)를 심사 모델이 민감 정보 참조로 감점했다(6차 2회 E02). 7차 전에 가린 값이 있는 줄을 contexts에서 뺐다. 런북 RB-05 조각의 같은 줄도 함께 뺐다.
- P04에서 승인 뒤 답에 승인 요청 사실이 빠지는 경우가 6차·7차 여섯 번 중 두 번 있었다. 승인 기록(요청한 조치, 사람의 결정, 실행 여부)을 상태에 남기고 최종 답 끝에 코드로 붙이게 했다(`approval_record`). 무승인 조치 금지처럼 모델 판단에 맡기지 않는다.
- 질의 한 건이 50~80초 걸렸다. 모델 호출별로 재 보니 전문 에이전트가 도구 결과를 받은 뒤 쓰는 정리 글이 가장 컸다(호출 2번에 약 20초, 최대 900토큰). 이 글은 감독자에게 앞 300자만 넘어간다. 전문 에이전트는 두 문장 이내로 보고하고 출력을 400토큰으로 제한했고, 감독자의 경로 선택은 Haiku 4.5로 바꿨다. Haiku 감독자가 같은 담당을 다시 불러 호출이 16번까지 늘어서, 한 질의에서 담당마다 한 번만 일하게 했다. 바꾸기 전 코드(`344b3c3`)와 번갈아 2회씩 재니 평균 69.7초에서 47.7초로 줄었다(질의별 -18%~-37%). Bedrock 응답 시간 자체가 같은 호출에서도 3초에서 18초까지 흔들려 1회 측정으로는 비교하기 어려웠다.
- 위 두 변경(`06a832e`)의 전체 평가(8차)는 Bedrock Sonnet의 일일 토큰 한도에 걸려 멈췄다(세 번 모두 2~3건째). 이 일로 일일 한도 대체를 넣었다. Bedrock에는 남은 일일 토큰을 알려 주는 API가 없고 이 계정은 Service Quotas 조회 권한도 없어서, 출력 1토큰짜리 요청으로 확인한다. 한도는 조금씩 다시 차서 작은 확인 요청은 통과하고 큰 요청이 막힐 수 있으므로(실제로 한 시간 뒤 확인 요청은 통과했다), 실행 중 오류에서도 바꾸게 했다. 후보마다 모델 객체를 따로 만드는 것은 도구 형식과 `tool_choice` 지원이 제공자마다 달라 만든 뒤 모델 ID만 바꾸면 어긋나기 때문이다. Sonnet 4.6, Haiku 4.5, Nova Pro가 도구 호출과 두 구조화 출력(`FinalAnswer`, `Route`)을 처리하는 것은 실제 호출로 확인했고, `gpt-oss`는 확인하지 않아 후보에서 뺐다. 일일 한도 대체를 넣은 뒤 다시 잰 8차에서는 세 번 모두 측정 중에 한도에 걸렸고, us-west-2의 같은 모델로 넘어가 끝까지 쟀다.
- 감독자를 Haiku로 바꾼 뒤 8차 3회에서, 상태·이력만 물은 질의를 조치 담당에게 넘기는 일이 한 번 있었다. Sonnet 감독자로 잰 3차~7차 15회에서는 없던 일이다. 속도 이득은 유지하고, 조치 담당에게 넘기는 조건(사용자 질문에 재시작 요청이 있음)은 코드로 확인하게 했다(`ACTION_REQUEST_RE`).
- 남은 한계: 전체 상태 요약을 인용하면 contexts가 늘어 context_precision이 내려간다. 승인 기록은 contexts에 없어 P04의 faithfulness가 낮게 나온다.
- requirements.txt에서 주석 줄에 붙어 있던 langgraph-checkpoint-sqlite를 분리하고 누락 패키지를 보충했다.

## 8. 핵심 코드 위치
| 항목 | 위치 | 심볼 |
|---|---|---|
| 구조화 출력 | `src/agent.py:89` | `FinalAnswer` |
| 인젝션 가드 정규식 | `src/agent.py:80` | `INJECTION_RE` |
| 운영 데이터 근거(contexts) | `src/agent.py:149` | `ops_contexts` |
| 인용 근거 선별 | `src/agent.py:173` | `pick_contexts` |
| 에이전트 클래스 | `src/agent.py:240` | `RunbookAgent` |
| 승인 대기(HITL) | `src/agent.py:423` | `_approval` |
| 최종 응답 | `src/agent.py:450` | `_final` |
| 질의 진입 | `src/agent.py:513` | `query` |
| 승인 재개 | `src/agent.py:522` | `approve` |
| 일일 한도 대체 | `src/llm.py:194` | `FailoverChat` |
| MCP 세션 재사용 | `src/mcp_server.py:70` | `_SessionWorker` |
| 쿼리 확장 | `src/retriever.py:143` | `expand_query` |
| 하이브리드 검색 | `src/retriever.py:156` | `hybrid_search` |
| 리랭킹 | `src/retriever.py:181` | `rerank` |
| 장애 이력 조회 | `src/tools.py:37` | `query_incidents` |
| 가짜 조치 로그 | `src/tools.py:58` | `ActionLog` |
| 도구 정의 | `src/tools.py:92` | `make_tools` |
| API 서버 | `src/server.py:20` | `create_app` |
| 가드레일 판정 | `evaluation/rubric.py:154` | `judge_guardrail` |
| 평가 실행 | `evaluation/run_eval.py:76` | `main` |
