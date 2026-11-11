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
