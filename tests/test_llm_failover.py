"""Bedrock 일일 한도 대체: 실행 전 확인과 실행 중 대체. Bedrock은 부르지 않고 가짜 모델·가짜 확인 함수를 쓴다."""
import threading

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from src.llm import (MAIN_CANDIDATES, ROUTER_CANDIDATES, AllCandidatesExhausted, Candidate, FailoverChat,
                     ModelChain, is_daily_quota_error)
from tests.conftest import FakeChat

DAILY = "ThrottlingException: An error occurred (ThrottlingException) when calling the Converse operation " \
        "(reached max retries: 4): Too many tokens per day, please wait before trying again."


class ThrottlingException(Exception):
    """botocore가 올리는 예외와 같은 이름(판별은 이름과 메시지로 한다)."""


class Recorder(FakeChat):
    """호출을 기록하고, 정해진 횟수만큼 일일 한도 오류를 내는 가짜 모델."""
    name_: str = ""
    fail_times: int = 0
    calls: object = None  # 여러 가짜 모델이 같은 리스트에 기록한다(pydantic이 복사하지 않게 object로 둔다)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(self.name_)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ThrottlingException(DAILY)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


C = [Candidate("m0", "r"), Candidate("m1", "r"), Candidate("m2", "r")]


def make_chain(probe_results: dict, fail_times: dict | None = None):
    calls, probes = [], []
    fail_times = fail_times or {}

    def factory(c):
        return Recorder(name_=c.model_id, fail_times=fail_times.get(c.model_id, 0), calls=calls)

    def prober(c):
        probes.append(c.model_id)
        return probe_results.get(c.model_id, "ok")

    return ModelChain("main", C, factory=factory, prober=prober), calls, probes


def test_daily_quota_error_is_told_apart_from_rate_limit():
    assert is_daily_quota_error(ThrottlingException(DAILY))
    assert not is_daily_quota_error(ThrottlingException("Too many requests, please wait before trying again."))
    assert not is_daily_quota_error(ValueError("tokens per day"))  # 스로틀링이 아닌 오류


def test_preflight_skips_models_whose_daily_quota_is_used_up():
    chain, calls, probes = make_chain({"m0": "daily_quota", "m1": "ok"})
    llm = FailoverChat(chain=chain)
    assert isinstance(llm.invoke([HumanMessage("payment-api 상태")]), AIMessage)
    assert probes == ["m0", "m1"] and calls == ["m1"]  # 한도를 다 쓴 설정 모델은 부르지 않는다
    assert chain.current.model_id == "m1"
    events = [e["event"] for e in chain.status()["events"]]
    assert events == ["probe:daily_quota", "probe:ok", "selected"]
    llm.invoke([HumanMessage("다시")])
    assert probes == ["m0", "m1"]  # 확인은 처음 한 번만 한다


def test_unavailable_models_are_skipped_and_busy_counts_as_usable():
    chain, calls, _ = make_chain({"m0": "unavailable: AccessDeniedException: no", "m1": "busy"})
    FailoverChat(chain=chain).invoke([HumanMessage("q")])
    assert calls == ["m1"]


def test_call_that_hits_daily_quota_mid_run_is_resent_to_next_model():
    chain, calls, probes = make_chain({}, fail_times={"m0": 1})
    out = FailoverChat(chain=chain).invoke([HumanMessage("payment-api 상태")])
    assert isinstance(out, AIMessage)
    assert calls == ["m0", "m1"] and probes == ["m0", "m1"]
    assert chain.current.model_id == "m1"
    assert any(e["event"] == "daily_quota_during_call" and e["model"] == "m0@r" for e in chain.status()["events"])


def test_tools_and_structured_output_follow_the_switch_and_copies_share_the_choice():
    class Pick(BaseModel):
        name: str = "x"

    chain, calls, _ = make_chain({}, fail_times={"m0": 1})
    llm = FailoverChat(chain=chain)
    capped = llm.model_copy(update={"max_tokens": 400})  # 전문 에이전트용 사본
    tools_runnable = capped.bind_tools([])
    tools_runnable.invoke([HumanMessage("payment-api 상태")])
    assert calls == ["m0", "m1"]
    llm.invoke([HumanMessage("다시")])  # 원본도 이미 바뀐 후보를 쓴다
    assert calls[-1] == "m1"
    assert capped.max_tokens == 400 and llm.max_tokens is None


def test_error_other_than_daily_quota_is_raised_without_switching():
    chain, calls, _ = make_chain({})

    class Boom(Recorder):
        def _generate(self, *a, **k):
            raise ValueError("bad request")

    chain.factory = lambda c: Boom(name_=c.model_id, calls=calls)
    with pytest.raises(ValueError):
        FailoverChat(chain=chain).invoke([HumanMessage("q")])
    assert chain.current.model_id == "m0"


def test_all_candidates_exhausted_gives_a_clear_non_retryable_error():
    from src.middleware import is_transient
    chain, _, _ = make_chain({"m0": "daily_quota", "m1": "daily_quota", "m2": "daily_quota"})
    with pytest.raises(AllCandidatesExhausted) as info:
        FailoverChat(chain=chain).invoke([HumanMessage("q")])
    assert "m0@r" in str(info.value) and not is_transient(info.value)  # 재시도 미들웨어가 다시 시도하지 않는다


def test_concurrent_failures_switch_only_once():
    chain, calls, probes = make_chain({})
    chain.ensure_ready()
    barrier = threading.Barrier(4)

    def hit():
        barrier.wait()
        chain.fail_over(0, ThrottlingException(DAILY))

    threads = [threading.Thread(target=hit) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert chain.current.model_id == "m1" and probes == ["m0", "m1"]


def test_candidate_lists_start_with_the_configured_models_and_come_from_the_available_list():
    assert MAIN_CANDIDATES[0] == Candidate("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "us-east-1")
    assert ROUTER_CANDIDATES[0] == Candidate("us.anthropic.claude-haiku-4-5-20251001-v1:0", "us-east-1")
    for c in MAIN_CANDIDATES + ROUTER_CANDIDATES:
        assert c.region in {"us-east-1", "us-west-2", "ap-northeast-2"}
        if c.region == "ap-northeast-2":
            assert c.model_id.startswith("global.")  # 서울 리전은 global 프로필만 있다
    assert len(set(MAIN_CANDIDATES)) == len(MAIN_CANDIDATES)
