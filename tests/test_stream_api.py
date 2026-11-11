"""웹 화면용 API: 조사 단계 스트리밍(/query/stream, /approve/stream), 장애 이력(/incidents), 모델 상태(/models)."""
import json

SECRET = "FAKE-SECRET-"


def stream(client, path, body):
    with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in r.iter_lines() if line]


def test_query_stream_emits_steps_then_the_same_response_as_query(client):
    events = stream(client, "/query/stream", {"question": "payment-api가 DB 연결 타임아웃이 나. 대응 절차 알려줘"})
    assert events[0]["type"] == "start" and events[0]["thread_id"]
    assert events[-1]["type"] == "result"
    steps = [e["step"] for e in events if e["type"] == "step"]
    resp = events[-1]["response"]
    assert set(resp) >= {"answer", "contexts", "trace", "status", "thread_id"}
    assert resp["thread_id"] == events[0]["thread_id"]
    assert steps == resp["trace"]  # 흘려보낸 단계를 모으면 최종 응답의 trace와 같다(빠짐·중복 없음)
    names = [s["step"] for s in steps]
    assert names[0] == "load_history" and "retrieve_runbook" in " ".join(names) and names[-1] == "final_response"
    assert SECRET not in json.dumps(events, ensure_ascii=False)


def test_follow_up_turn_streams_only_its_own_steps(client):
    first = stream(client, "/query/stream", {"question": "payment-api가 DB 연결 타임아웃이 나. 대응 절차 알려줘"})
    tid = first[0]["thread_id"]
    second = stream(client, "/query/stream", {"question": "과거 장애 이력도 알려줘", "thread_id": tid})
    steps = [e["step"] for e in second if e["type"] == "step"]
    assert steps == second[-1]["response"]["trace"] and steps[0]["step"] == "load_history"


def test_approval_streams_only_new_steps_and_executes_once(client, agent):
    events = stream(client, "/query/stream", {"question": "order-worker OOM 으로 죽어. 런북대로 order-worker 재시작해줘"})
    waiting = events[-1]["response"]
    assert waiting["status"] == "awaiting_approval" and agent.action_log.read() == []
    assert any(e["type"] == "step" and e["step"]["step"] == "approval_wait" for e in events)
    done = stream(client, "/approve/stream", {"thread_id": waiting["thread_id"], "decision": "approve"})
    steps = [e["step"] for e in done if e["type"] == "step"]
    resp = done[-1]["response"]
    assert resp["status"] == "completed" and len(agent.action_log.read()) == 1
    assert resp["trace"][:len(waiting["trace"])] == waiting["trace"]
    assert steps == resp["trace"][len(waiting["trace"]):]  # 승인 뒤에 붙은 단계만 흘려보낸다
    assert "**승인 기록**" in resp["answer"]
    again = client.post("/approve/stream", json={"thread_id": waiting["thread_id"], "decision": "approve"})
    assert again.status_code == 409 and len(agent.action_log.read()) == 1


def test_approve_stream_without_pending_action_is_409(client):
    r = client.post("/approve/stream", json={"thread_id": "no-such-thread", "decision": "approve"})
    assert r.status_code == 409


def test_stream_reports_errors_as_an_event(client, agent):
    def boom(*a, **k):
        raise RuntimeError("model down")

    agent.graph.stream = boom
    events = stream(client, "/query/stream", {"question": "payment-api 상태"})
    assert [e["type"] for e in events] == ["start", "error"] and "model down" in events[-1]["message"]


def test_incidents_are_recent_first_and_masked(client):
    rows = client.get("/incidents", params={"limit": 5}).json()["incidents"]
    assert 1 <= len(rows) <= 5
    dates = [r["occurred_at"] for r in rows]
    assert dates == sorted(dates, reverse=True)
    full = client.get("/incidents", params={"limit": 100}).json()["incidents"]
    assert SECRET not in json.dumps(full, ensure_ascii=False)
    assert any("[REDACTED]" in r["notes"] for r in full)  # 비밀값이 있던 메모는 가려서 보낸다


def test_models_reports_configured_models_without_probing(client):
    m = client.get("/models").json()
    assert set(m) == {"main", "router"}
    assert m["main"]["current"].startswith("us.anthropic.claude-sonnet-4-5")
    assert m["router"]["current"].startswith("us.anthropic.claude-haiku-4-5")
