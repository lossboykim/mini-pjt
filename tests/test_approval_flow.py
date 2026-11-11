"""승인 흐름: 위험 조치는 /query에서 awaiting_approval로 멈추고, /approve 결과로만 실행된다."""
import pytest

FIVE = {"answer", "contexts", "trace", "status", "thread_id"}
ASK = "order-worker OOM 으로 죽어. 런북대로 order-worker 재시작해줘"


def ask(client, q=ASK):
    r = client.post("/query", json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()


def decide(client, tid, decision):
    r = client.post("/approve", json={"thread_id": tid, "decision": decision})
    assert r.status_code == 200, r.text
    return r.json()


def test_query_waits_for_approval_without_executing(client, agent):
    r = ask(client)
    assert r["status"] == "awaiting_approval" and r["thread_id"]
    assert FIVE <= set(r)
    assert agent.action_log.read() == []
    assert client.get("/actions").json()["actions"] == []
    wait = [t for t in r["trace"] if t["step"] == "approval_wait"]
    assert wait and wait[0]["input"]["tool"] == "restart_service" and "args" in wait[0]["input"]
    assert not any(t["step"] == "tool_call" and t["input"].get("tool") == "restart_service" for t in r["trace"])


def test_approve_executes_exactly_once(client, agent):
    r = ask(client)
    done = decide(client, r["thread_id"], "approve")
    assert set(done) == FIVE and done["status"] == "completed"
    assert done["thread_id"] == r["thread_id"]
    log = agent.action_log.read()
    assert len(log) == 1 and set(log[0]) == {"thread_id", "action", "target", "executed_at"}
    assert log[0]["thread_id"] == r["thread_id"]
    assert log[0]["action"] == "restart_service" and log[0]["target"] == "order-worker"
    call = [t for t in done["trace"] if t["step"] == "tool_call" and t["input"]["tool"] == "restart_service"]
    assert len(call) == 1 and call[0]["output"]["executed"] is True
    # 이미 처리된 대화에 다시 승인하면 거절되고 재실행되지 않는다
    again = client.post("/approve", json={"thread_id": r["thread_id"], "decision": "approve"})
    assert again.status_code == 409
    assert len(agent.action_log.read()) == 1


def test_reject_does_not_execute(client, agent):
    r = ask(client, "payment-api 재시작해줘")
    assert r["status"] == "awaiting_approval"
    done = decide(client, r["thread_id"], "reject")
    assert set(done) == FIVE and done["status"] == "completed"
    assert agent.action_log.read() == []
    assert not any(t["step"] in ("tool_call", "tool:restart_service") and
                   (t["input"].get("tool") == "restart_service" or t["step"].startswith("tool:"))
                   for t in done["trace"] if "restart" in str(t["input"]))
    assert client.post("/approve", json={"thread_id": r["thread_id"], "decision": "reject"}).status_code == 409


def test_approve_blocked_when_log_line_exists(client, agent):
    """로그에 이미 thread_id 줄이 있으면 대기 중이어도 실행하지 않고 409."""
    r = ask(client)
    agent.action_log.append({"thread_id": r["thread_id"], "action": "restart_service",
                             "target": "order-worker", "executed_at": "x"})
    assert client.post("/approve", json={"thread_id": r["thread_id"], "decision": "approve"}).status_code == 409
    assert len(agent.action_log.read()) == 1


def test_409_survives_restart(tmp_path, llm):
    from fastapi.testclient import TestClient
    from src.agent import RunbookAgent
    from src.server import create_app
    from tests.conftest import FakeEmbeddings
    mk = lambda: TestClient(create_app(RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state")))
    c1 = mk()
    r = ask(c1)
    decide(c1, r["thread_id"], "approve")
    c2 = mk()  # 서버 재시작: 새 에이전트가 같은 저장소를 읽는다
    assert c2.post("/approve", json={"thread_id": r["thread_id"], "decision": "approve"}).status_code == 409
    log = tmp_path / "state" / "logs" / "action_log.jsonl"
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    # 재시작 뒤에도 대기 상태가 유지된다
    p = ask(c2, "payment-api 재시작해줘")
    c3 = mk()
    assert decide(c3, p["thread_id"], "approve")["status"] == "completed"


def test_followup_query_while_pending_does_not_execute(client, agent):
    r = ask(client)
    again = client.post("/query", json={"question": "재시작해줘", "thread_id": r["thread_id"]}).json()
    assert again["status"] == "awaiting_approval"
    assert agent.action_log.read() == []


def test_approve_unknown_thread_and_bad_decision(client, agent):
    assert client.post("/approve", json={"thread_id": "nope", "decision": "approve"}).status_code == 409
    r = ask(client)
    assert client.post("/approve", json={"thread_id": r["thread_id"], "decision": "yes"}).status_code == 422
    assert agent.action_log.read() == []
