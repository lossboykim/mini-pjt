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
    assert any(t["step"] == "approval_wait" for t in r["trace"])


def test_approve_executes_exactly_once(client, agent):
    r = ask(client)
    done = decide(client, r["thread_id"], "approve")
    assert set(done) == FIVE and done["status"] == "completed"
    assert done["thread_id"] == r["thread_id"]
    log = agent.action_log.read()
    assert len(log) == 1 and log[0]["executed"] is True
    assert log[0]["action"] == "restart_service" and log[0]["service"] == "order-worker"
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
    out = next(t["output"] for t in done["trace"] if t["step"] == "tool:restart_service")
    assert out["executed"] is False and out["decision"] == "reject"


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
