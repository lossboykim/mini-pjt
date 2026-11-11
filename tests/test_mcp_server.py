"""MCP 서버 연동 테스트: 실제로 stdio MCP 서버를 띄워 도구를 호출한다. 네트워크와 Bedrock은 쓰지 않는다."""
import pytest

from src.mcp_server import MCP_TOOL_NAMES, OpsMcpClient
from src.tools import INCIDENTS_DB, STATUS_FILE, fake_status_api, query_incidents


@pytest.fixture(scope="module")
def mcp_client():
    return OpsMcpClient(INCIDENTS_DB, STATUS_FILE)


def test_server_exposes_the_two_ops_tools(mcp_client):
    assert sorted(mcp_client.list_tools()) == sorted(MCP_TOOL_NAMES)


def test_service_status_over_mcp_matches_local(mcp_client):
    assert mcp_client.call("get_service_status", {"service": "all"}) == fake_status_api("all", STATUS_FILE)
    missing = mcp_client.call("get_service_status", {"service": "no-such-service"})
    assert missing["found"] is False


def test_incident_search_over_mcp_matches_local_and_masks_secrets(mcp_client):
    query = "DB 연결 타임아웃"
    over_mcp = mcp_client.call("search_incidents", {"query": query})
    assert over_mcp == {"incidents": query_incidents(query, INCIDENTS_DB)}
    assert "FAKE-SECRET-" not in str(over_mcp)


def test_unknown_tool_is_rejected_before_calling_the_server(mcp_client):
    with pytest.raises(ValueError):
        mcp_client.call("restart_service", {"service": "payment-api"})


# ---------------- 에이전트에 연결된 상태에서의 동작 ----------------
@pytest.fixture
def mcp_agent(tmp_path, llm):
    from src.agent import RunbookAgent
    from tests.conftest import FakeEmbeddings
    return RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state", use_mcp=True)


def test_agent_calls_ops_tools_through_mcp_and_keeps_trace_contract(mcp_agent):
    res = mcp_agent.query("payment-api 현재 상태와 과거 장애 이력 사례 알려줘")
    assert res["status"] == "completed"
    mcp_steps = [s for s in res["trace"] if s["step"] == "mcp_call"]
    assert {s["input"]["tool"] for s in mcp_steps} == {"search_incidents", "get_service_status"}
    assert all(s["output"]["transport"] == "mcp" and s["input"]["server"] == "runbook-ops" for s in mcp_steps)
    tool_calls = {s["input"]["tool"] for s in res["trace"] if s["step"] == "tool_call"}
    assert {"search_incidents", "get_service_status"} <= tool_calls  # 도구 이름 계약은 MCP와 무관하게 같다
    status = next(s["output"] for s in res["trace"] if s["step"] == "tool:get_service_status")
    assert status == fake_status_api("payment-api", STATUS_FILE)
    assert "_transport" not in str(res)


def test_agent_falls_back_to_local_when_mcp_server_fails(mcp_agent):
    def broken(name, args):
        raise ConnectionError("MCP server down")

    mcp_agent.mcp.call = broken
    res = mcp_agent.query("payment-api 현재 상태 알려줘")
    assert res["status"] == "completed"
    step = next(s for s in res["trace"] if s["step"] == "mcp_call")
    assert step["output"]["transport"] == "local_fallback" and "MCP server down" in step["output"]["error"]
    status = next(s["output"] for s in res["trace"] if s["step"] == "tool:get_service_status")
    assert status == fake_status_api("payment-api", STATUS_FILE)
