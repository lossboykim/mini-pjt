"""웹 화면(GET /)과 화면 옆 패널용 서비스 상태(GET /services)."""


def test_root_serves_web_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    for path in ("/query", "/approve", "/actions", "/services"):
        assert path in body


def test_services_lists_all_fake_statuses(client):
    r = client.get("/services")
    assert r.status_code == 200
    data = r.json()
    assert data["payment-api"]["error_rate_pct"] == 8.4
    assert data["order-worker"]["status"] == "unhealthy"
    assert not any("FAKE-SECRET-" in str(v) for v in data.values())
