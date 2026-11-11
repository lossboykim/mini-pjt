"""AC7: 쿼리 확장·하이브리드 검색·리랭킹 trace, 다중 도구 호출, 구조화 최종 응답 검증."""
import json

from src.agent import FinalAnswer

MULTI_Q = "payment-api DB 연결 타임아웃 런북 절차 알려주고 과거 이력도 보여줘. payment-api 상태도 확인해줘"


def post(client, q):
    r = client.post("/query", json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()


def steps(resp):
    return [t["step"] for t in resp["trace"]]


def test_retrieval_stages_recorded_in_order(client):
    r = post(client, "payment-api가 DB 연결 타임아웃이 나. 대응 절차 알려줘")
    s = steps(r)
    assert "query_expansion" in s and "hybrid_search" in s and "rerank" in s
    assert s.index("query_expansion") < s.index("hybrid_search") < s.index("rerank") < s.index("tool:retrieve_runbook")
    by = {t["step"]: t for t in r["trace"]}
    assert by["query_expansion"]["output"]["queries"]
    hs = by["hybrid_search"]
    assert set(hs["input"]["methods"]) == {"bm25", "vector"}
    assert hs["output"]["candidates"]
    assert by["rerank"]["output"]["kept"]


def test_multiple_distinct_tools_in_one_query(client):
    r = post(client, MULTI_Q)
    tools = {s for s in steps(r) if s.startswith("tool:")}
    assert len(tools) >= 2, tools
    assert {"tool:retrieve_runbook", "tool:search_incidents", "tool:get_service_status"} <= tools


def test_final_answer_validated_by_pydantic(client):
    for q in (MULTI_Q, "오늘 점심 메뉴 추천해줘"):
        r = post(client, q)
        finals = [t for t in r["trace"] if t["step"] == "final_response"]
        assert len(finals) == 1
        assert steps(r).index("final_response") > max(
            (i for i, s in enumerate(steps(r)) if s.startswith("tool:")), default=-1)
        m = FinalAnswer.model_validate(finals[0]["output"])
        assert isinstance(m.grounded, bool) and isinstance(m.cited_doc_ids, list)
    assert "retrieval_trace" not in json.dumps(post(client, MULTI_Q))
