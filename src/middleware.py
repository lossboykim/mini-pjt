"""미들웨어 (Day 5): 대화 이력 요약, 저장 이력·외부 트레이스의 비밀값 마스킹, 모델 호출 재시도.

LangChain의 AgentMiddleware 훅(before_model, wrap_model_call)을 그대로 구현하고, 이 프로젝트가 직접 만든
LangGraph 그래프에서도 돌 수 있도록 MiddlewareChain이 훅을 등록 순서대로 실행한다.

역할 경계:
- 사용자에게 돌려주는 응답(answer, contexts, trace)의 비밀값 차단은 핵심 가드레일(src/agent.py의 _scrub)이 맡는다.
- 여기의 마스킹은 체크포인터에 저장되는 대화 이력과 LangSmith로 나가는 실행 기록만 다룬다.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph.message import add_messages

from src.retriever import REDACTED, SECRET_RE, mask_secrets

SUMMARY_TRIGGER_MESSAGES = 24  # 한 대화의 메시지가 이 수를 넘으면 오래된 부분을 요약한다
SUMMARY_KEEP_MESSAGES = 8      # 요약 뒤에도 원문으로 남기는 최근 메시지 수
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0

# 다시 시도하면 풀릴 수 있는 일시 오류(Bedrock 스로틀링, 타임아웃, 5xx)
TRANSIENT_MARKERS = (
    "throttling", "toomanyrequests", "too many requests", "serviceunavailable", "service unavailable",
    "modeltimeout", "timeout", "timed out", "internalserver", "internal server", "connection reset",
    "connectionerror", "modelnotready", "503", "529",
)


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


class SecretMaskingMiddleware(AgentMiddleware):
    """모델을 부르기 전에, 상태에 쌓인 메시지의 비밀값을 가린 사본으로 바꿔 저장한다.

    같은 id로 돌려주므로 add_messages 리듀서가 원본을 덮어쓴다. 사용자가 질문에 붙여 넣은 토큰이나
    도구가 미처 가리지 못한 값이 체크포인터에 그대로 남는 것을 막는다.
    """

    def before_model(self, state, runtime=None) -> dict[str, Any] | None:
        changed: list[BaseMessage] = []
        for msg in state["messages"]:
            update: dict[str, Any] = {}
            masked = _mask_value(msg.content)
            if masked != msg.content:
                update["content"] = masked
            if isinstance(msg, AIMessage) and msg.tool_calls:
                calls = [{**c, "args": _mask_value(c["args"])} for c in msg.tool_calls]
                if calls != msg.tool_calls:
                    update["tool_calls"] = calls
            if update:
                changed.append(msg.model_copy(update=update))
        return {"messages": changed} if changed else None


def is_transient(error: BaseException) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


class RetryMiddleware(AgentMiddleware):
    """모델 호출이 일시 오류로 실패하면 지수 백오프로 다시 시도한다. 그 밖의 오류는 그대로 올린다."""

    def __init__(self, max_attempts: int = RETRY_MAX_ATTEMPTS, base_delay: float = RETRY_BASE_DELAY,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__()
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self._sleep = sleep
        self.last_attempts = 0

    def wrap_model_call(self, request, handler):
        for attempt in range(1, self.max_attempts + 1):
            self.last_attempts = attempt
            try:
                return handler(request)
            except Exception as error:  # noqa: BLE001 - 일시 오류인지 판별한 뒤 다시 올린다
                if attempt == self.max_attempts or not is_transient(error):
                    raise
                self._sleep(self.base_delay * (2 ** (attempt - 1)))
        raise RuntimeError("unreachable")


def _overrides(middleware: AgentMiddleware, hook: str) -> bool:
    return getattr(type(middleware), hook) is not getattr(AgentMiddleware, hook)


class MiddlewareChain:
    """AgentMiddleware 목록을 직접 만든 그래프 노드 안에서 실행한다(create_agent와 같은 순서 규칙).

    - before_model: 등록 순서대로 실행하고, 각 훅이 돌려준 메시지 갱신을 다음 훅과 모델 입력에 반영한다.
    - wrap_model_call: 먼저 등록한 미들웨어가 바깥쪽을 감싼다.
    """

    def __init__(self, middlewares: list[AgentMiddleware]):
        self.middlewares = list(middlewares)

    def before_model(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], list, list[dict]]:
        """(모델에 줄 메시지, 상태에 반영할 메시지 갱신, trace용 이벤트)를 돌려준다."""
        current = list(messages)
        updates: list = []
        events: list[dict] = []
        for mw in self.middlewares:
            if not _overrides(mw, "before_model"):
                continue
            result = mw.before_model({"messages": current}, None)
            if not result or not result.get("messages"):
                continue
            before = len(current)
            current = add_messages(current, result["messages"])
            updates.extend(result["messages"])
            events.append({"middleware": type(mw).__name__, "messages_before": before, "messages_after": len(current)})
        return current, updates, events

    def call_model(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        wrapped = handler
        for mw in reversed(self.middlewares):
            if _overrides(mw, "wrap_model_call"):
                wrapped = (lambda m, inner: lambda req: m.wrap_model_call(req, inner))(mw, wrapped)
        return wrapped(request)

    def retry_attempts(self) -> int:
        return max((mw.last_attempts for mw in self.middlewares if isinstance(mw, RetryMiddleware)), default=1)


def build_middleware(llm, *, trigger_messages: int = SUMMARY_TRIGGER_MESSAGES,
                     keep_messages: int = SUMMARY_KEEP_MESSAGES) -> MiddlewareChain:
    """이 프로젝트의 기본 미들웨어 묶음: 재시도(바깥) → 비밀값 마스킹 → 이력 요약."""
    return MiddlewareChain([
        RetryMiddleware(),
        SecretMaskingMiddleware(),
        SummarizationMiddleware(model=llm, trigger=("messages", trigger_messages), keep=("messages", keep_messages)),
    ])


def anonymize_for_trace(payload: Any) -> Any:
    """LangSmith로 나가는 입력·출력에서 비밀값을 가린다."""
    from langsmith.anonymizer import create_anonymizer
    return create_anonymizer([{"pattern": SECRET_RE, "replace": REDACTED}])(payload)


def masked_tracing():
    """LangSmith 추적이 켜져 있으면, 비밀값을 가리는 클라이언트로 추적하는 컨텍스트 매니저를 돌려준다.

    추적이 꺼져 있거나 키가 없으면 아무 일도 하지 않는 컨텍스트를 돌려준다.
    """
    from contextlib import nullcontext

    enabled = os.environ.get("LANGSMITH_TRACING", os.environ.get("LANGCHAIN_TRACING_V2", "")).lower() == "true"
    if not enabled or not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        return nullcontext()
    from langchain_core.tracers.context import tracing_v2_enabled
    from langsmith import Client
    from langsmith.anonymizer import create_anonymizer

    client = Client(anonymizer=create_anonymizer([{"pattern": SECRET_RE, "replace": REDACTED}]))
    return tracing_v2_enabled(client=client)
