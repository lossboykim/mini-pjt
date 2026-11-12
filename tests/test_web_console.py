"""웹 화면이 새 API(조사 단계 스트리밍, 장애 이력, 모델 상태)를 쓰고, 외부 스크립트 없이 한 파일로 동작하는지."""
import re


def test_console_uses_streaming_and_panel_apis(client):
    body = client.get("/").text
    for path in ("/query/stream", "/approve/stream", "/incidents", "/models", "/services", "/actions"):
        assert path in body, path
    for el in ('id="incidents"', 'id="modelLabel"', 'id="svcSummary"'):
        assert el in body, el


def test_console_is_self_contained_and_escapes_model_output(client):
    body = client.get("/").text
    assert not re.search(r"<script[^>]+src=", body)            # 외부 스크립트를 불러오지 않는다
    assert not re.search(r"<link[^>]+stylesheet", body)
    assert "function esc(" in body and "esc(src)" in body       # 모델이 쓴 답은 HTML을 이스케이프한 뒤 그린다
    assert "eval(" not in body


def test_console_has_impact_timeline_report_and_feedback(client):
    body = client.get("/").text
    for el in ('id="impact"', 'id="timeline"', 'id="noteForm"', 'id="copyReport"', 'id="downloadReport"', 'id="changes"'):
        assert el in body, el
    assert "/feedback" in body
    assert "localStorage" in body and "try {" in body     # 확인(ack) 저장은 저장소를 못 써도 화면이 동작해야 한다


def test_feedback_is_saved_masked_and_listed(client, agent):
    r = client.post("/feedback", json={"thread_id": "t-1", "verdict": "inaccurate",
                                        "note": "실제 원인은 FAKE-SECRET-abc 노출", "question": "payment-api 에러율"})
    assert r.status_code == 200 and r.json()["saved"]
    client.post("/feedback", json={"thread_id": "t-1", "verdict": "accurate"})
    data = client.get("/feedback").json()
    assert data["counts"] == {"accurate": 1, "inaccurate": 1}
    assert data["feedback"][0]["verdict"] == "accurate"                    # 최신순
    assert "FAKE-SECRET-" not in data["feedback"][1]["note"]
    saved = (agent.action_log.path.parent / "feedback.jsonl").read_text(encoding="utf-8")
    assert "FAKE-SECRET-" not in saved
    assert agent.action_log.read() == []                                    # 조치 실행 로그와 섞이지 않는다


def test_feedback_rejects_bad_input(client):
    assert client.post("/feedback", json={"thread_id": "t", "verdict": "maybe"}).status_code == 422
    assert client.post("/feedback", json={"thread_id": "", "verdict": "accurate"}).status_code == 422
    assert client.post("/feedback", json={"thread_id": "t", "verdict": "accurate", "note": "x" * 1001}).status_code == 422
