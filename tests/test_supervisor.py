"""Supervisor 단위 테스트: 위임 결정의 코드 쪽 안전장치, 작업 기록 요약, 전문 에이전트의 도구 분리."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from src.supervisor import (FINISH, MAX_HANDOFFS, WORKERS, Route, build_request, choose_next, digest,
                            worker_system_prompt, worker_tools)


class StubRouter:
    """항상 같은 결정을 돌려주는 가짜 모델. 받은 요청을 기록한다."""

    def __init__(self, next_worker):
        self.next_worker = next_worker
        self.requests = []

    def with_structured_output(self, schema):
        def run(request):
            self.requests.append(request)
            return schema(next=self.next_worker, reason="stub")
        return RunnableLambda(run)


class NamedTool:
    def __init__(self, name):
        self.name = name


ALL_TOOLS = [NamedTool(n) for n in ("retrieve_runbook", "search_incidents", "get_service_status", "restart_service")]


def test_each_tool_belongs_to_exactly_one_worker():
    owned = [t for spec in WORKERS.values() for t in spec.tools]
    assert sorted(owned) == sorted(t.name for t in ALL_TOOLS)


def test_worker_tools_are_restricted_to_the_role():
    assert [t.name for t in worker_tools("runbook_agent", ALL_TOOLS)] == ["retrieve_runbook"]
    assert [t.name for t in worker_tools("ops_agent", ALL_TOOLS)] == ["search_incidents", "get_service_status"]
    assert [t.name for t in worker_tools("action_agent", ALL_TOOLS)] == ["restart_service"]


def test_choose_next_follows_the_model_decision():
    llm = StubRouter("ops_agent")
    route = choose_next(llm, [HumanMessage("payment-api 상태 알려줘")], ["runbook_agent"])
    assert route.next == "ops_agent" and len(llm.requests) == 1


def test_choose_next_stops_at_handoff_limit_without_calling_the_model():
    llm = StubRouter("runbook_agent")
    visited = ["runbook_agent", "ops_agent"] * (MAX_HANDOFFS // 2)
    route = choose_next(llm, [HumanMessage("q")], visited)
    assert route.next == FINISH and llm.requests == []


def test_choose_next_never_reassigns_the_worker_that_just_ran():
    route = choose_next(StubRouter("runbook_agent"), [HumanMessage("q")], ["runbook_agent"])
    assert route.next == FINISH


def test_choose_next_uses_the_invoke_wrapper_when_given():
    seen = []

    def invoke(request, handler):
        seen.append(request)
        return handler(request)

    route = choose_next(StubRouter(FINISH), [HumanMessage("q")], [], invoke=invoke)
    assert route.next == FINISH and len(seen) == 1


def test_digest_summarises_work_and_request_lists_visited_workers():
    messages = [
        HumanMessage("payment-api CrashLoopBackOff 대응 절차 알려줘"),
        AIMessage("", tool_calls=[{"name": "retrieve_runbook", "args": {"query": "CrashLoopBackOff"}, "id": "c1", "type": "tool_call"}]),
        ToolMessage('{"documents": [{"doc_id": "runbooks/rb-01"}]}', tool_call_id="c1", name="retrieve_runbook"),
        AIMessage("런북 rb-01의 절차를 찾았습니다."),
    ]
    text = digest(messages)
    assert "사용자: payment-api" in text and "도구 호출: retrieve_runbook" in text
    assert "도구 결과[retrieve_runbook]" in text and "에이전트: 런북 rb-01" in text
    request = build_request(messages, ["runbook_agent"])
    assert "지금까지 일한 에이전트: runbook_agent" in request[-1].content
    assert "지금까지 일한 에이전트: 없음" in build_request(messages, [])[-1].content


def test_worker_system_prompt_keeps_base_rules_and_adds_role():
    prompt = worker_system_prompt("기본 규칙", "ops_agent")
    assert prompt.startswith("기본 규칙") and "ops_agent" in prompt


def test_route_rejects_unknown_worker():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Route(next="unknown_agent", reason="x")
