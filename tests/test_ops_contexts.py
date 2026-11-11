"""contexts 확장: 런북 조각과 함께 장애 이력·서비스 상태도 근거(contexts)로 돌려준다."""
import json

import pytest

from src.agent import RunbookAgent, ops_contexts, pick_contexts
from src.tools import INCIDENTS_DB, STATUS_FILE, fake_status_api, query_incidents
from tests.conftest import FakeEmbeddings

SECRET = "FAKE-SECRET-"


def test_incidents_become_one_context_each_with_masked_text():
    incidents = query_incidents("DB 연결 타임아웃 커넥션 풀", INCIDENTS_DB)
    ctx = ops_contexts("search_incidents", {"incidents": incidents})
    assert [c["doc_id"] for c in ctx] == [f"incidents/{i['id']}" for i in incidents]
    for c, inc in zip(ctx, incidents):
        assert set(c) == {"doc_id", "text"}
        assert inc["root_cause"] in c["text"] and inc["resolution"] in c["text"]
        assert SECRET not in c["text"]


def test_masked_secret_lines_are_left_out_of_contexts():
    # INC-1005 메모와 RB-05 진단 절차에는 비밀값이 심어져 있다. 가린 자리([REDACTED])도 contexts에 남기지 않는다.
    inc = [i for i in query_incidents("payment-api DB 연결 타임아웃 커넥션 풀", INCIDENTS_DB) if i["id"] == "INC-1005"]
    assert inc and "[REDACTED]" in inc[0]["notes"]  # 도구 결과(에이전트가 보는 것)에는 가린 값이 그대로 있다
    (ctx,) = ops_contexts("search_incidents", {"incidents": inc})
    assert "[REDACTED]" not in ctx["text"] and "DB_PASSWORD" not in ctx["text"]
    assert inc[0]["root_cause"] in ctx["text"]  # 다른 줄은 그대로 남는다
    doc = {"doc_id": "runbooks/rb-05.md#chunk-0", "text": "1. 첫 단계\n3. 계정 DB_PASSWORD=[REDACTED] 사용\n4. 다음 단계"}
    (rb,) = ops_contexts("retrieve_runbook", {"documents": [doc]})
    assert rb["text"] == "1. 첫 단계\n4. 다음 단계"


def test_status_contexts_single_all_and_missing():
    one = ops_contexts("get_service_status", fake_status_api("payment-api", STATUS_FILE))
    assert [c["doc_id"] for c in one] == ["status/payment-api"]
    assert "8.4" in one[0]["text"] and "1250" in one[0]["text"]  # 에러율·지연 수치가 그대로 담긴다
    # 전체 요약은 도구 호출 한 번의 결과라 근거 하나로 담고, 서비스마다 한 줄씩 수치를 그대로 둔다
    (every,) = ops_contexts("get_service_status", fake_status_api("all", STATUS_FILE))
    assert every["doc_id"] == "status/all"
    for svc, info in json.loads(STATUS_FILE.read_text(encoding="utf-8")).items():
        line = next(l for l in every["text"].splitlines() if l.startswith(f"- {svc}:"))
        assert str(info["error_rate_pct"]) in line and info["pods_ready"] in line
    assert ops_contexts("get_service_status", fake_status_api("no-such", STATUS_FILE)) == []


def test_citing_a_service_found_only_in_the_summary_keeps_the_summary():
    ctx = [{"doc_id": "runbooks/rb-05.md#chunk-0", "text": "r"}, {"doc_id": "status/all", "text": "s"},
           {"doc_id": "incidents/INC-1005", "text": "i"}]
    picked = [c["doc_id"] for c in pick_contexts(ctx, ["runbooks/rb-05.md#chunk-0", "status/payment-api"])]
    assert picked == ["runbooks/rb-05.md#chunk-0", "status/all"]  # 인용하지 않은 이력은 빠지고 요약은 남는다


def test_action_contexts_record_the_human_decision():
    from src.agent import action_contexts
    (done,) = action_contexts([{"action": "restart_service", "service": "order-worker", "decision": "approve",
                               "executed": True, "message": "order-worker 롤링 재시작을 (가짜로) 실행했다"}])
    assert done["doc_id"] == "actions/restart_service/order-worker"
    assert "사람에게 승인을 요청" in done["text"] and "approve" in done["text"] and "실행했다" in done["text"]
    (no,) = action_contexts([{"action": "restart_service", "service": "payment-api", "decision": "reject", "executed": False}])
    assert "reject" in no["text"] and "실행하지 않았다" in no["text"]
    assert action_contexts([]) == []


def test_pick_keeps_cited_and_falls_back_per_kind():
    ctx = [{"doc_id": "status/a", "text": "s"}, {"doc_id": "runbooks/rb-01.md#chunk-0", "text": "r0"},
           {"doc_id": "runbooks/rb-01.md#chunk-1", "text": "r1"}, {"doc_id": "incidents/INC-1", "text": "i"}]
    ids = lambda cs: [c["doc_id"] for c in cs]  # noqa: E731
    # 인용한 것만, 런북이 먼저
    assert ids(pick_contexts(ctx, ["incidents/INC-1", "runbooks/rb-01.md#chunk-1"])) == [
        "runbooks/rb-01.md#chunk-1", "incidents/INC-1"]
    # 운영 데이터를 하나도 인용하지 않았으면 모은 것을 모두 남긴다
    assert ids(pick_contexts(ctx, ["runbooks/rb-01.md#chunk-0"])) == [
        "runbooks/rb-01.md#chunk-0", "status/a", "incidents/INC-1"]
    assert ids(pick_contexts(ctx, [])) == ["runbooks/rb-01.md#chunk-0", "runbooks/rb-01.md#chunk-1",
                                           "status/a", "incidents/INC-1"]


@pytest.fixture
def agent(tmp_path, llm):
    a = RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state",
                     use_mcp=False, use_supervisor=False)
    yield a
    a.close()


def test_agent_returns_incident_and_status_contexts_matching_tool_results(agent):
    res = agent.query("payment-api DB 연결 타임아웃 커넥션 풀 고갈 과거 장애 이력과 현재 상태 알려줘")
    ids = [c["doc_id"] for c in res["contexts"]]
    assert any(i.startswith("runbooks/") for i in ids)
    inc_step = next(s["output"] for s in res["trace"] if s["step"] == "tool:search_incidents")
    expected = ops_contexts("search_incidents", inc_step)
    assert expected and all(e in res["contexts"] for e in expected)
    assert SECRET not in json.dumps(res, ensure_ascii=False)
    if any(s["step"] == "tool:get_service_status" for s in res["trace"]):
        assert any(i.startswith("status/") for i in ids)


def test_no_evidence_still_returns_empty_contexts(agent):
    res = agent.query("오늘 점심 메뉴 추천해줘")
    assert res["contexts"] == [] and res["trace"][-1]["step"] == "no_evidence"
