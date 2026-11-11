"""Multi-Agent Supervisor (Day 6): 감독자가 질의를 보고 역할이 나뉜 전문 에이전트에게 일을 맡긴다.

전문 에이전트(각자 자기 도구만 쓴다):
- runbook_agent: 런북 검색 (retrieve_runbook)
- ops_agent: 운영 데이터 조회 (search_incidents, get_service_status — MCP 서버 경유)
- action_agent: 조치 요청 (restart_service — 실행은 그래프의 승인 노드가 맡는다)

감독자는 구조화 출력(Route)으로 다음에 일할 에이전트나 FINISH를 고른다. 가드레일, 사람 승인, 최종 응답은
감독자와 전문 에이전트 바깥(src/agent.py의 guard, approval, final 노드)에 그대로 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

FINISH = "FINISH"
MAX_HANDOFFS = 4  # 한 질의에서 감독자가 일을 맡기는 최대 횟수
DIGEST_ENTRIES = 24
VISITED_LABEL = "지금까지 일한 에이전트"
NONE_LABEL = "없음"


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    role: str
    tools: tuple[str, ...]
    prompt: str


WORKERS: dict[str, WorkerSpec] = {
    "runbook_agent": WorkerSpec(
        name="runbook_agent",
        role="런북 검색 담당. 증상에 맞는 런북의 진단 절차와 조치 절차를 찾는다.",
        tools=("retrieve_runbook",),
        prompt=("너는 런북 검색 담당이다. retrieve_runbook으로 증상에 맞는 런북을 찾아 진단 절차와 조치 절차를 "
                "정리한다. 런북에 없는 내용은 없다고 말한다. 운영 데이터 조회와 조치 실행은 다른 담당의 일이다."),
    ),
    "ops_agent": WorkerSpec(
        name="ops_agent",
        role="운영 데이터 담당. 과거 장애 이력과 서비스의 현재 상태를 조회한다.",
        tools=("search_incidents", "get_service_status"),
        prompt=("너는 운영 데이터 담당이다. 과거 사례는 search_incidents로, 현재 상태는 get_service_status로 "
                "확인해 사실만 전한다. 서비스 이름이 없으면 get_service_status(service=\"all\")로 전체 요약을 본다. "
                "수치를 지어내지 않는다."),
    ),
    "action_agent": WorkerSpec(
        name="action_agent",
        role="조치 담당. 사용자가 재시작 같은 조치를 요청했을 때 승인을 요청한다.",
        tools=("restart_service",),
        prompt=("너는 조치 담당이다. 사용자가 요청한 조치가 런북 절차에 있으면 restart_service를 호출해 사람의 "
                "승인을 요청한다. 호출은 실행이 아니라 승인 요청이다. 승인 결과와 실행 여부를 그대로 전한다."),
    ),
}

NextWorker = Literal["runbook_agent", "ops_agent", "action_agent", "FINISH"]


class Route(BaseModel):
    """감독자의 결정: 다음에 일할 전문 에이전트 또는 FINISH."""
    next: NextWorker = Field(description="다음에 일할 에이전트 이름. 더 할 일이 없으면 FINISH")
    reason: str = Field(description="그렇게 고른 이유 한 문장")


SUPERVISOR_PROMPT = (
    "너는 장애 대응 팀의 감독자다. 사용자의 질의와 지금까지의 작업 기록을 보고, 다음에 일할 전문 에이전트를 "
    "하나 고르거나 FINISH를 고른다. 직접 답하지 않는다.\n\n전문 에이전트:\n"
    + "\n".join(f"- {w.name}: {w.role}" for w in WORKERS.values())
    + "\n\n규칙:\n"
    "1. 장애 증상이나 절차를 묻는 질의는 runbook_agent부터 맡긴다.\n"
    "2. 과거 사례나 이력, 현재 상태나 수치를 묻거나 그것이 대응에 필요하면 ops_agent에게 맡긴다.\n"
    "3. 사용자가 재시작 같은 조치를 요청한 경우에만 action_agent에게 맡긴다. 그 전에 runbook_agent가 관련 런북을 "
    "찾았어야 한다.\n"
    "4. 필요한 정보가 모였거나, 운영 장애와 무관한 질의이거나, 런북에서 근거를 찾지 못했으면 FINISH를 고른다.\n"
    "5. 방금 일한 에이전트에게 연달아 다시 맡기지 않는다."
)


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def digest(messages: list) -> str:
    """감독자에게 줄 작업 기록. 원본 메시지 대신 짧은 글로 넘겨 토큰을 아끼고 역할 순서 제약을 피한다."""
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            who = "감독자" if m.additional_kwargs.get("handoff") else "사용자"
            lines.append(f"{who}: " + _clip(m.content, 400))
        elif isinstance(m, AIMessage):
            for call in m.tool_calls or []:
                lines.append(f"도구 호출: {call['name']}({_clip(call['args'], 150)})")
            if m.content and not m.tool_calls:
                lines.append("에이전트: " + _clip(m.content, 300))
        elif isinstance(m, ToolMessage):
            lines.append(f"도구 결과[{m.name}]: " + _clip(m.content, 250))
    return "\n".join(lines[-DIGEST_ENTRIES:])


def build_request(messages: list, visited: list[str]) -> list:
    done = ", ".join(visited) if visited else NONE_LABEL
    body = (f"작업 기록:\n{digest(messages)}\n\n{VISITED_LABEL}: {done}\n"
            "다음에 일할 에이전트를 하나 고르거나 FINISH를 골라라.")
    return [SystemMessage(SUPERVISOR_PROMPT), HumanMessage(body)]


def choose_next(llm, messages: list, visited: list[str], invoke=None) -> Route:
    """다음에 일할 에이전트를 고른다. 횟수 제한과 연속 재지명 금지는 모델 판단과 무관하게 코드로 지킨다.

    invoke: 모델 호출을 감싸는 함수(재시도 미들웨어용). 없으면 그대로 호출한다.
    """
    if len(visited) >= MAX_HANDOFFS:
        return Route(next=FINISH, reason=f"한 질의의 위임 한도({MAX_HANDOFFS}회)에 도달했다")
    request = build_request(messages, visited)
    runnable = llm.with_structured_output(Route)
    route = invoke(request, runnable.invoke) if invoke else runnable.invoke(request)
    if route.next != FINISH and visited and visited[-1] == route.next:
        return Route(next=FINISH, reason=f"{route.next}가 방금 일했으므로 연달아 맡기지 않는다")
    return route


def worker_system_prompt(base_prompt: str, worker: str) -> str:
    spec = WORKERS[worker]
    return f"{base_prompt}\n\n[전문 에이전트: {spec.name}] {spec.prompt}"


def worker_tools(worker: str, tools: list) -> list:
    """전문 에이전트에게 바인딩할 도구만 고른다."""
    allowed = set(WORKERS[worker].tools)
    return [t for t in tools if t.name in allowed]
