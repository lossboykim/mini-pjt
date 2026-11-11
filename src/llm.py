"""Bedrock 모델 선택과 일일 한도 대체(failover).

Bedrock은 남은 일일 토큰을 알려 주는 API가 없고, 이 계정은 Service Quotas 조회 권한도 없다.
그래서 두 단계로 한도를 다룬다.

1. 실행 전 확인: 처음 모델을 부르기 전에 설정된 모델에 출력 1토큰짜리 요청을 보내 본다(probe).
   일일 한도 오류가 나면 후보 목록의 다음 모델로 넘어가 같은 확인을 한다.
2. 실행 중 대체: 한도는 조금씩 다시 차므로 작은 확인 요청은 통과하고 큰 요청이 막힐 수 있다.
   실제 호출이 일일 한도 오류로 실패하면 다음 후보로 바꿔 같은 요청을 다시 보낸다.

후보는 같은 모델의 다른 리전·추론 프로필(한도가 따로 계산된다)을 먼저, 그다음 다른 모델을 둔다.
역할(main, router)마다 프로세스에 하나의 후보 사슬을 두고, 그 역할의 모든 모델 객체가 같은 선택을 따른다.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from pydantic import ConfigDict


@dataclass(frozen=True)
class Candidate:
    model_id: str
    region: str

    @property
    def label(self) -> str:
        return f"{self.model_id}@{self.region}"


SONNET_45 = "anthropic.claude-sonnet-4-5-20250929-v1:0"
SONNET_46 = "anthropic.claude-sonnet-4-6"
HAIKU_45 = "anthropic.claude-haiku-4-5-20251001-v1:0"
NOVA_PRO = "amazon.nova-pro-v1:0"

# 답을 쓰는 모델(에이전트·최종 응답·쿼리 확장·리랭킹·심사). 첫 항목이 설정된 모델이다.
MAIN_CANDIDATES: tuple[Candidate, ...] = (
    Candidate(f"us.{SONNET_45}", "us-east-1"),
    Candidate(f"us.{SONNET_45}", "us-west-2"),
    Candidate(f"global.{SONNET_45}", "us-east-1"),
    Candidate(f"global.{SONNET_45}", "ap-northeast-2"),
    Candidate(f"us.{SONNET_46}", "us-east-1"),
    Candidate(f"us.{SONNET_46}", "us-west-2"),
    Candidate(f"global.{SONNET_46}", "ap-northeast-2"),
    Candidate(f"us.{HAIKU_45}", "us-east-1"),
    Candidate(f"us.{HAIKU_45}", "us-west-2"),
    Candidate(f"global.{HAIKU_45}", "ap-northeast-2"),
    Candidate(f"us.{NOVA_PRO}", "us-east-1"),
    Candidate(f"us.{NOVA_PRO}", "us-west-2"),
)

# 감독자의 경로 선택(짧은 분류). 빠른 모델을 먼저 두고, 막히면 답을 쓰는 모델로도 넘어간다.
ROUTER_CANDIDATES: tuple[Candidate, ...] = (
    Candidate(f"us.{HAIKU_45}", "us-east-1"),
    Candidate(f"us.{HAIKU_45}", "us-west-2"),
    Candidate(f"global.{HAIKU_45}", "us-east-1"),
    Candidate(f"global.{HAIKU_45}", "ap-northeast-2"),
    Candidate(f"us.{NOVA_PRO}", "us-east-1"),
    Candidate(f"us.{SONNET_45}", "us-east-1"),
    Candidate(f"us.{SONNET_45}", "us-west-2"),
)

ROLE_CANDIDATES = {"main": MAIN_CANDIDATES, "router": ROUTER_CANDIDATES}

_DAILY_RE = re.compile(r"(tokens|requests)\s+per\s+day|per\s+day|daily", re.IGNORECASE)


def is_daily_quota_error(error: BaseException) -> bool:
    """Bedrock 일일 한도 초과(ThrottlingException: Too many tokens per day)인지 본다.

    분당 요청 제한 같은 일반 스로틀링은 기다리면 풀리므로 여기에 넣지 않는다(재시도 미들웨어가 맡는다).
    """
    text = f"{type(error).__name__} {error}"
    return "hrottling" in text and bool(_DAILY_RE.search(text))


class AllCandidatesExhausted(RuntimeError):
    """후보 모델을 모두 쓸 수 없다. 재시도 미들웨어가 다시 시도하지 않도록 일시 오류 표식을 담지 않는다."""


def probe(candidate: Candidate) -> str:
    """출력 1토큰짜리 요청으로 모델을 지금 쓸 수 있는지 본다. 'ok' | 'daily_quota' | 'busy' | 'unavailable: ...'"""
    import boto3
    from botocore.config import Config

    client = boto3.client("bedrock-runtime", region_name=candidate.region,
                          config=Config(retries={"max_attempts": 1, "mode": "standard"},
                                        connect_timeout=10, read_timeout=30))
    try:
        client.converse(modelId=candidate.model_id, messages=[{"role": "user", "content": [{"text": "ping"}]}],
                        inferenceConfig={"maxTokens": 1})
        return "ok"
    except Exception as error:  # noqa: BLE001 - 어떤 오류든 이 후보의 상태로 기록한다
        if is_daily_quota_error(error):
            return "daily_quota"
        if "hrottling" in f"{type(error).__name__} {error}":
            return "busy"  # 분당 제한: 모델은 쓸 수 있고, 실제 호출은 재시도로 넘긴다
        return f"unavailable: {type(error).__name__}: {str(error)[:150]}"


def _default_factory(candidate: Candidate) -> BaseChatModel:
    from langchain_aws import ChatBedrockConverse
    return ChatBedrockConverse(model=candidate.model_id, region_name=candidate.region, temperature=0)


class ModelChain:
    """한 역할의 후보 목록과 지금 쓰는 후보. 여러 모델 객체와 스레드가 함께 쓴다."""

    def __init__(self, role: str, candidates: Sequence[Candidate],
                 factory: Callable[[Candidate], BaseChatModel] = _default_factory,
                 prober: Callable[[Candidate], str] = probe):
        if not candidates:
            raise ValueError("후보 모델이 없다")
        self.role = role
        self.candidates = list(candidates)
        self.factory = factory
        self.prober = prober
        self.index = 0
        self.events: list[dict] = []
        self._checked = False
        self._models: dict[int, BaseChatModel] = {}
        self._lock = threading.RLock()

    @property
    def current(self) -> Candidate:
        return self.candidates[self.index]

    def model(self, index: int) -> BaseChatModel:
        with self._lock:
            if index not in self._models:
                self._models[index] = self.factory(self.candidates[index])
            return self._models[index]

    def _log(self, event: str, candidate: Candidate, detail: str = "") -> None:
        self.events.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "role": self.role, "event": event,
                            "model": candidate.label, **({"detail": detail} if detail else {})})

    def _select_from(self, start: int) -> None:
        for i in range(start, len(self.candidates)):
            status = self.prober(self.candidates[i])
            self._log(f"probe:{status.split(':')[0]}", self.candidates[i], status if ":" in status else "")
            if status in ("ok", "busy"):
                if i != self.index or not self._checked:
                    self._log("selected", self.candidates[i])
                self.index = i
                self._checked = True
                return
        tried = ", ".join(c.label for c in self.candidates[start:])
        raise AllCandidatesExhausted(f"[{self.role}] 쓸 수 있는 Bedrock 모델이 없다(일일 한도 또는 접근 불가): {tried}")

    def ensure_ready(self) -> None:
        """실행 전 확인: 처음 한 번 설정된 모델부터 차례로 확인해 쓸 수 있는 후보를 고른다."""
        with self._lock:
            if not self._checked:
                self._select_from(0)

    def fail_over(self, failed_index: int, error: BaseException) -> None:
        """실행 중 일일 한도로 실패한 후보를 버리고 다음 후보로 넘어간다(다른 스레드가 이미 넘겼으면 그대로 둔다)."""
        with self._lock:
            if self.index != failed_index:
                return
            self._log("daily_quota_during_call", self.candidates[failed_index], str(error)[:200])
            self._select_from(failed_index + 1)

    def status(self) -> dict:
        return {"role": self.role, "current": self.current.label, "checked": self._checked,
                "configured": self.candidates[0].label, "events": list(self.events)}


_CHAINS: dict[str, ModelChain] = {}
_CHAINS_LOCK = threading.Lock()


def get_chain(role: str) -> ModelChain:
    with _CHAINS_LOCK:
        if role not in _CHAINS:
            _CHAINS[role] = ModelChain(role, ROLE_CANDIDATES[role])
        return _CHAINS[role]


def chains_status() -> dict:
    """평가 결과에 남길 역할별 모델 선택 기록."""
    with _CHAINS_LOCK:
        return {role: chain.status() for role, chain in _CHAINS.items()}


class FailoverChat(BaseChatModel):
    """후보 사슬의 지금 모델로 호출을 넘기는 채팅 모델. 일일 한도 오류가 나면 다음 후보로 바꿔 같은 요청을 다시 보낸다.

    bind_tools와 with_structured_output은 후보 모델마다 따로 만든다(도구 형식·tool_choice 지원이 제공자마다 다르다).
    """

    chain: Any
    max_tokens: Optional[int] = None
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "bedrock-failover"

    @property
    def current_model(self) -> str:
        return self.chain.current.label

    def _inner(self, index: int) -> BaseChatModel:
        model = self.chain.model(index)
        if self.max_tokens is not None and hasattr(model, "max_tokens"):
            model = model.model_copy(update={"max_tokens": self.max_tokens})
        return model

    def run_with_failover(self, call: Callable[[int], Any]) -> Any:
        self.chain.ensure_ready()
        while True:
            index = self.chain.index
            try:
                return call(index)
            except Exception as error:  # noqa: BLE001 - 일일 한도만 대체하고 그 밖의 오류는 그대로 올린다
                if not is_daily_quota_error(error):
                    raise
                self.chain.fail_over(index, error)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self.run_with_failover(
            lambda i: self._inner(i)._generate(messages, stop=stop, run_manager=run_manager, **kwargs))

    def bind_tools(self, tools, **kwargs) -> Runnable:
        return _FailoverRunnable(self, lambda m: m.bind_tools(tools, **kwargs))

    def with_structured_output(self, schema, **kwargs) -> Runnable:
        return _FailoverRunnable(self, lambda m: m.with_structured_output(schema, **kwargs))


class _FailoverRunnable(Runnable):
    """FailoverChat에서 파생한 실행 단위(도구 바인딩, 구조화 출력). 후보마다 한 번 만들어 둔다."""

    def __init__(self, owner: FailoverChat, build: Callable[[BaseChatModel], Runnable]):
        self.owner = owner
        self.build = build
        self._built: dict[int, Runnable] = {}

    def _runnable(self, index: int) -> Runnable:
        if index not in self._built:
            self._built[index] = self.build(self.owner._inner(index))
        return self._built[index]

    def invoke(self, input, config=None, **kwargs):
        return self.owner.run_with_failover(lambda i: self._runnable(i).invoke(input, config, **kwargs))
