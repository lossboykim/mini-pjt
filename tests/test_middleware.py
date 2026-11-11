"""미들웨어 단위 테스트: 저장 이력 마스킹, 재시도, 이력 요약, 트레이스 익명화. Bedrock 호출 없이 돈다."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.middleware import (MiddlewareChain, RetryMiddleware, SecretMaskingMiddleware, anonymize_for_trace,
                            build_middleware, is_transient)

SECRET = "FAKE-SECRET-db-pass-0001"


def test_masking_replaces_secrets_in_stored_messages_keeping_ids():
    msgs = [
        HumanMessage("토큰 " + SECRET + " 으로 접속이 안 돼요", id="h1"),
        AIMessage("", id="a1", tool_calls=[{"name": "search_incidents", "args": {"query": SECRET}, "id": "c1", "type": "tool_call"}]),
        ToolMessage("결과 " + SECRET, tool_call_id="c1", id="t1"),
        HumanMessage("비밀값 없는 질문", id="h2"),
    ]
    update = SecretMaskingMiddleware().before_model({"messages": msgs}, None)
    changed = {m.id: m for m in update["messages"]}
    assert set(changed) == {"h1", "a1", "t1"}  # 비밀값이 없는 메시지는 건드리지 않는다
    assert SECRET not in str(changed["h1"].content) and "[REDACTED]" in changed["h1"].content
    assert changed["a1"].tool_calls[0]["args"]["query"] == "[REDACTED]"
    assert SECRET not in str(changed["t1"].content)


def test_masking_returns_none_when_nothing_to_mask():
    assert SecretMaskingMiddleware().before_model({"messages": [HumanMessage("안녕", id="h")]}, None) is None


def test_chain_applies_masking_to_model_input_and_state_update():
    chain = MiddlewareChain([SecretMaskingMiddleware()])
    current, updates, events = chain.before_model([HumanMessage(SECRET, id="h1")])
    assert SECRET not in str(current[0].content)
    assert [m.id for m in updates] == ["h1"]
    assert events[0]["middleware"] == "SecretMaskingMiddleware"


def test_retry_retries_transient_errors_then_succeeds():
    sleeps, calls = [], []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            raise RuntimeError("ThrottlingException: Too many requests")
        return "ok"

    retry = RetryMiddleware(max_attempts=3, base_delay=0.5, sleep=sleeps.append)
    assert MiddlewareChain([retry]).call_model("req", handler) == "ok"
    assert len(calls) == 3 and sleeps == [0.5, 1.0]  # 지수 백오프
    assert retry.last_attempts == 3


def test_retry_does_not_retry_permanent_errors():
    calls = []

    def handler(request):
        calls.append(request)
        raise ValueError("ValidationException: bad request")

    with pytest.raises(ValueError):
        RetryMiddleware(sleep=lambda s: None).wrap_model_call("req", handler)
    assert len(calls) == 1


def test_retry_gives_up_after_max_attempts():
    calls = []

    def handler(request):
        calls.append(request)
        raise TimeoutError("read timed out")

    with pytest.raises(TimeoutError):
        RetryMiddleware(max_attempts=2, sleep=lambda s: None).wrap_model_call("req", handler)
    assert len(calls) == 2


def test_is_transient_classification():
    assert is_transient(RuntimeError("ServiceUnavailableException"))
    assert not is_transient(ValueError("AccessDeniedException"))


def test_summarization_shrinks_long_history(llm):
    chain = build_middleware(llm, trigger_messages=6, keep_messages=2)
    msgs = []
    for i in range(5):
        msgs.append(HumanMessage(f"질문 {i}", id=f"h{i}"))
        msgs.append(AIMessage(f"답 {i}", id=f"a{i}"))
    current, updates, events = chain.before_model(msgs)
    assert len(current) < len(msgs)  # 오래된 메시지가 요약 한 건으로 줄었다
    assert current[-1].content == "답 4"  # 최근 메시지는 원문으로 남는다
    assert any(e["middleware"] == "SummarizationMiddleware" for e in events)
    assert updates  # 상태에 반영할 갱신(전체 삭제 + 요약 + 보존분)이 있다


def test_summarization_leaves_short_history_alone(llm):
    chain = build_middleware(llm)
    msgs = [HumanMessage("질문", id="h"), AIMessage("답", id="a")]
    current, updates, events = chain.before_model(msgs)
    assert current == msgs and not updates and not events


def test_trace_anonymizer_masks_nested_payload():
    payload = {"inputs": {"question": "pw " + SECRET}, "outputs": [{"text": SECRET}]}
    masked = anonymize_for_trace(payload)
    assert SECRET not in str(masked) and "[REDACTED]" in str(masked)


# ---------------- 에이전트에 연결된 상태에서의 동작 ----------------
def _stored_messages(agent, thread_id):
    return agent.graph.get_state(agent._cfg(thread_id)).values["messages"]


def test_agent_does_not_store_user_pasted_secret(agent):
    res = agent.query(f"payment-api crashloop 대응 절차 알려줘. 접속 토큰은 {SECRET} 입니다", thread_id="mw-1")
    assert res["status"] == "completed"
    assert all(SECRET not in str(m.content) for m in _stored_messages(agent, "mw-1"))
    steps = [s for s in res["trace"] if s["step"] == "middleware"]
    assert steps and steps[0]["output"][0]["middleware"] == "SecretMaskingMiddleware"
    assert SECRET not in str(res)


def test_agent_summarizes_long_thread(agent, llm):
    agent.middleware = build_middleware(llm, trigger_messages=6, keep_messages=2)
    last = None
    for i in range(4):
        last = agent.query(f"payment-api crashloop 대응 절차 알려줘 ({i})", thread_id="mw-2")
    assert last["status"] == "completed" and last["answer"]
    events = [e for s in last["trace"] if s["step"] == "middleware" for e in s["output"]]
    assert any(e["middleware"] == "SummarizationMiddleware" for e in events)
    assert len(_stored_messages(agent, "mw-2")) < 4 * 4  # 요약이 없었다면 턴마다 4건 이상 쌓인다


def test_agent_retries_transient_model_errors(agent, llm, monkeypatch):
    from src.middleware import RetryMiddleware
    retry = next(m for m in agent.middleware.middlewares if isinstance(m, RetryMiddleware))
    retry._sleep = lambda seconds: None
    original = type(llm)._generate
    failures = {"left": 2}

    def flaky(self, messages, stop=None, run_manager=None, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("ThrottlingException: Too many requests")
        return original(self, messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr(type(llm), "_generate", flaky)
    res = agent.query("payment-api crashloop 대응 절차 알려줘", thread_id="mw-3")
    assert res["status"] == "completed" and failures["left"] == 0


def test_agent_works_with_middleware_disabled(tmp_path, llm):
    from src.agent import RunbookAgent
    from tests.conftest import FakeEmbeddings
    plain = RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state", use_middleware=False)
    res = plain.query("payment-api crashloop 대응 절차 알려줘")
    assert res["status"] == "completed" and not [s for s in res["trace"] if s["step"] == "middleware"]
