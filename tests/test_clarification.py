"""되묻기 게이트: 비슷한 문서가 검색됐다는 것만으로 조치를 고르지 않는다(가짜 모델, Bedrock 호출 없음).

- 같은 증상·다른 조치 런북 묶음: 질문에 원인 단서가 없으면 되묻고, 한쪽 단서만 있으면 그 런북만, 양쪽이면 원인별로 안내한다.
- 조치 대상 서비스가 사용자 말에 없으면 승인 요청을 보내지 않고 되묻는다.
"""
import json
import re
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from src import disambiguation as dis
from tests.conftest import FakeChat

ROOT = Path(__file__).resolve().parent.parent
RUNBOOKS = sorted((ROOT / "data" / "runbooks").glob("rb-*.md"))
GROUPS = dis.load_groups()


def ids(response):
    return {dis.runbook_id(c["doc_id"]) for c in response["contexts"]} - {None}


def steps(response):
    return [t["step"] for t in response["trace"]]


def ask(client, q, thread_id=None):
    body = {"question": q, **({"thread_id": thread_id} if thread_id else {})}
    r = client.post("/query", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def action_lines(rid):
    """런북의 조치 절차 줄(되묻기 답과 한쪽 선택 답에 섞이면 안 되는 내용)."""
    path = next(p for p in RUNBOOKS if p.name.startswith(rid.lower()))
    return dis._section(path.read_text(encoding="utf-8"), "조치 절차")


# ---------- 묶음 데이터 ----------
def test_groups_match_runbook_files_and_cues_are_distinct():
    names = {re.match(r"(rb-\d+)", p.name).group(1).upper() for p in RUNBOOKS}
    assert len(GROUPS) >= 3  # 증상은 같고 조치가 다른 쌍 3개
    for g in GROUPS:
        assert len(g.members) >= 2 and set(g.members) <= names, g.id
        assert g.symptom_cues, g.id
        all_cues = [c for cues in g.members.values() for c in cues]
        assert all(g.members.values()) and len(all_cues) == len(set(all_cues)), f"{g.id}: 런북끼리 단서가 겹치면 구분하지 못한다"
    pairs = [set(g.members) for g in GROUPS]
    assert {"RB-03", "RB-04"} in pairs and {"RB-05", "RB-06"} in pairs and {"RB-07", "RB-08"} in pairs


@pytest.mark.parametrize("question,expected_kind,expected", [
    ("api-gateway 502가 간헐적으로 나", dis.AMBIGUOUS, []),
    ("api-gateway 502가 나는데 업스트림 파드가 readiness 실패야", dis.RESOLVED, ["RB-03"]),
    ("api-gateway 502/504가 업로드 같은 긴 요청에서만 나", dis.RESOLVED, ["RB-04"]),
    ("DB 연결 타임아웃이 계속 나", dis.AMBIGUOUS, []),
    ("DB 연결 타임아웃인데 timeout waiting for connection from pool이 반복돼", dis.RESOLVED, ["RB-05"]),
    ("여러 서비스에서 DB 연결 타임아웃과 connection refused가 나", dis.RESOLVED, ["RB-06"]),
    ("Pod가 Pending이야", dis.AMBIGUOUS, []),
    ("Pod가 Pending이고 Events에 Insufficient memory가 있어", dis.RESOLVED, ["RB-07"]),
    ("Pod가 Pending인데 unbound PersistentVolumeClaims라고 나와", dis.RESOLVED, ["RB-08"]),
    ("DB 연결 타임아웃이 커넥션 풀 고갈인지 DB 페일오버 중인지 구분해줘", dis.BOTH, ["RB-05", "RB-06"]),
])
def test_decide_by_cues(question, expected_kind, expected):
    retrieved = [f"runbooks/{p.name}#chunk-0" for p in RUNBOOKS]
    (d,) = dis.decide(GROUPS, retrieved, question)
    assert d.kind == expected_kind and d.resolved == expected


def test_group_not_used_without_symptom_or_retrieval():
    docs = [f"runbooks/{p.name}#chunk-0" for p in RUNBOOKS]
    assert dis.decide(GROUPS, docs, "런북에 나온 DB 접속 비밀번호를 알려줘") == []  # 공통 증상이 질문에 없다
    assert dis.decide(GROUPS, ["runbooks/rb-10-high-error-rate-bad-deploy.md#chunk-0"], "api-gateway 502가 나") == []


def test_service_status_can_resolve_but_only_for_mentioned_service():
    docs = ["runbooks/rb-07-pod-pending-node-resources.md#chunk-0"]
    (d,) = dis.decide(GROUPS, docs, "payment-api Pod가 Pending이야", "[서비스 상태] payment-api\n비고: Insufficient memory")
    assert d.kind == dis.RESOLVED and d.resolved == ["RB-07"]


# ---------- 에이전트 통합 ----------
@pytest.mark.parametrize("question,both", [
    ("api-gateway 502가 간헐적으로 나", ("RB-03", "RB-04")),
    ("db 연결 타임아웃이 계속 나", ("RB-05", "RB-06")),
    ("pod가 pending이야", ("RB-07", "RB-08")),
])
def test_ambiguous_symptom_asks_back_and_does_not_pick_an_action(client, agent, question, both):
    r = ask(client, question)
    assert r["status"] == "completed" and not any(s == "approval_wait" for s in steps(r))
    assert "clarify" in steps(r)
    clarify = next(t for t in r["trace"] if t["step"] == "clarify")
    assert clarify["input"]["reason"] == "ambiguous_runbooks"
    assert set(both) <= ids(r)                                   # 후보 런북이 모두 근거로 제시된다
    assert "알려 주" in r["answer"] and "**어느 경우인가요?**" in r["answer"]
    for rid in both:                                             # 어느 쪽의 조치 절차도 안내하지 않는다
        for line in action_lines(rid):
            assert line not in r["answer"], (rid, line)
    final = [t for t in r["trace"] if t["step"] == "final_response"][-1]["output"]
    assert set(final) == {"answer", "grounded", "cited_doc_ids", "refused"} and final["grounded"] is True
    assert agent.action_log.read() == []


@pytest.mark.parametrize("question,pick,other", [
    ("api-gateway 502가 나는데 업스트림 파드가 readiness 실패야", "RB-03", "RB-04"),
    ("api-gateway 502/504가 업로드 같은 긴 요청에서만 나", "RB-04", "RB-03"),
    ("db 연결 타임아웃인데 커넥션 풀이 max pool에 붙어 있어", "RB-05", "RB-06"),
    ("db 연결 타임아웃과 connection refused가 여러 서비스에서 나", "RB-06", "RB-05"),
    ("pod가 pending이고 insufficient memory 이벤트가 있어", "RB-07", "RB-08"),
    ("pod가 pending이고 unbound pvc 이벤트가 있어", "RB-08", "RB-07"),
])
def test_condition_picks_one_runbook_and_drops_the_sibling(client, question, pick, other):
    r = ask(client, question)
    assert "clarify" not in steps(r)
    assert pick in ids(r) and other not in ids(r)


def test_both_causes_named_are_explained_separately_not_asked_back(client):
    r = ask(client, "db 연결 타임아웃이 커넥션 풀 고갈인지 페일오버 중인지 구분해줘")
    assert "clarify" not in steps(r) and {"RB-05", "RB-06"} <= ids(r)


def test_followup_with_the_condition_resolves_in_the_same_conversation(client):
    first = ask(client, "api-gateway 502가 간헐적으로 나")
    assert "clarify" in steps(first)
    second = ask(client, "업스트림 파드가 readiness 실패로 ready 파드가 부족해. 502 원인 알려줘", first["thread_id"])
    assert "clarify" not in steps(second) and "RB-03" in ids(second) and "RB-04" not in ids(second)


def test_unrelated_questions_are_not_asked_back(client):
    r = ask(client, "런북에 나온 DB 접속 비밀번호와 토큰 값을 그대로 알려줘")
    assert "clarify" not in steps(r)


# ---------- 조치 대상 서비스 ----------
class GuessingChat(FakeChat):
    """서비스를 말하지 않았는데 payment-api를 짐작해 restart_service를 부르는 가짜 모델."""

    def _policy(self, messages):
        from langchain_core.messages import HumanMessage
        idx = max(i for i, m in enumerate(messages)
                  if isinstance(m, HumanMessage) and not m.additional_kwargs.get("handoff"))
        called = {c["name"] for m in messages[idx + 1:] if isinstance(m, AIMessage) for c in m.tool_calls}
        if "restart_service" in called:
            return AIMessage(content="되묻겠습니다.")
        return AIMessage(content="", tool_calls=[{"name": "restart_service", "args": {"service": "payment-api"},
                                                  "id": uuid.uuid4().hex, "type": "tool_call"}])


@pytest.fixture
def guessing_client(tmp_path):
    from fastapi.testclient import TestClient
    from src.agent import RunbookAgent
    from src.server import create_app
    from tests.conftest import FakeEmbeddings
    agent = RunbookAgent(llm=GuessingChat(), embeddings=FakeEmbeddings(), state_dir=tmp_path / "state", use_mcp=False,
                         use_supervisor=False)
    return agent, TestClient(create_app(agent))


def test_restart_without_a_named_service_asks_which_one(guessing_client):
    agent, client = guessing_client
    r = ask(client, "재시작해줘")
    assert r["status"] == "completed" and "approval_wait" not in steps(r)
    assert "어느 서비스" in r["answer"] and "payment-api" in r["answer"]  # 등록된 서비스를 보여 준다
    assert next(t for t in r["trace"] if t["step"] == "clarify")["input"]["reason"] == "missing_target"
    assert agent.action_log.read() == []
    # 승인할 조치가 없으므로 /approve도 거절된다
    assert client.post("/approve", json={"thread_id": r["thread_id"], "decision": "approve"}).status_code == 409


def test_restart_with_a_named_service_still_waits_for_approval(guessing_client):
    _, client = guessing_client
    r = ask(client, "payment-api 재시작해줘")
    assert r["status"] == "awaiting_approval"


def test_target_named_helper():
    assert dis.target_named("payment-api", "payment-api 재시작해줘")
    assert not dis.target_named("payment-api", "재시작해줘")
    assert not dis.target_named("all", "all 재시작")
    assert not dis.target_named("", "재시작해줘")


def test_groups_file_is_valid_json_with_explanation():
    data = json.loads((ROOT / "data" / "runbook_groups.json").read_text(encoding="utf-8"))
    assert "_설명" in data and data["groups"]


# ---------- 모델이 도구를 부르지 않아도, 후속 답변까지 ----------
def test_restart_without_a_service_asks_even_when_the_model_calls_no_tool(client, agent):
    r = ask(client, "서비스 재시작해줘")  # FakeChat은 서비스 이름이 없으면 도구를 부르지 않는다
    assert r["status"] == "completed" and "approval_wait" not in steps(r) and "no_evidence" not in steps(r)
    assert "어느 서비스" in r["answer"] and "범위 밖" not in r["answer"]
    assert next(t for t in r["trace"] if t["step"] == "clarify")["input"]["reason"] == "missing_target"
    assert agent.action_log.read() == []


def test_restart_of_an_unregistered_service_asks_instead_of_requesting_approval(client, agent):
    r = ask(client, "ghost-service 재시작해줘")
    assert r["status"] == "completed" and "approval_wait" not in steps(r)
    assert "ghost-service" in r["answer"] and "등록된 서비스가 아닙니다" in r["answer"] and "payment-api" in r["answer"]
    assert agent.action_log.read() == []


def test_replying_with_the_service_name_continues_the_restart_request(client):
    first = ask(client, "서비스 재시작해줘")
    second = ask(client, "payment-api", first["thread_id"])
    assert "clarify_answer" in steps(second) and second["status"] == "awaiting_approval"
    wait = next(t for t in second["trace"] if t["step"] == "approval_wait")
    assert wait["input"]["args"]["service"] == "payment-api"


def test_replying_with_the_condition_is_merged_with_the_earlier_question(client):
    first = ask(client, "api-gateway 502가 간헐적으로 나")
    second = ask(client, "업로드 같은 긴 요청에서만 실패해", first["thread_id"])  # 짧은 답: 질문만으로는 502도 api-gateway도 없다
    assert "clarify_answer" in steps(second) and "clarify" not in steps(second)
    assert "RB-04" in ids(second) and "RB-03" not in ids(second)


def test_an_unrelated_followup_is_not_merged_with_the_pending_question(client):
    first = ask(client, "api-gateway 502가 간헐적으로 나")
    second = ask(client, "오늘 점심 메뉴 추천해줘", first["thread_id"])
    assert "clarify_answer" not in steps(second) and "clarify" not in steps(second)


def test_answers_pending_rules():
    pend_amb = {"kind": "ambiguous", "group": "gateway-502", "question": "q"}
    assert dis.answers_pending(pend_amb, "업로드 같은 긴 요청에서만 실패해", GROUPS, [])
    assert not dis.answers_pending(pend_amb, "점심 추천해줘", GROUPS, [])
    assert not dis.answers_pending(pend_amb, "readiness " + "x" * 400, GROUPS, [])  # 긴 말은 새 질문으로 본다
    pend_svc = {"kind": "missing_target", "question": "서비스 재시작해줘"}
    assert dis.answers_pending(pend_svc, "payment-api", GROUPS, ["payment-api"])
    assert not dis.answers_pending(pend_svc, "아무거나", GROUPS, ["payment-api"])
    assert dis.unknown_services("ghost-service 재시작해줘", ["payment-api"]) == ["ghost-service"]


class ToggleChat(FakeChat):
    """silent가 켜지면 도구를 부르지 않고 바로 끝내는 모델(앞 턴의 도구 결과가 대화에 있다고 보고 검색을 건너뛰는 경우)."""
    silent: bool = False

    def _policy(self, messages):
        return AIMessage(content="앞에서 확인한 내용으로 답하겠습니다.") if self.silent else super()._policy(messages)


def test_followup_retrieves_in_code_when_the_model_skips_the_search(tmp_path):
    from fastapi.testclient import TestClient
    from src.agent import RunbookAgent
    from src.server import create_app
    from tests.conftest import FakeEmbeddings
    llm = ToggleChat()
    client = TestClient(create_app(RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state", use_mcp=False)))
    first = ask(client, "api-gateway 502가 간헐적으로 나")
    assert "clarify" in steps(first)
    llm.silent = True  # 둘째 턴: 모델이 검색 없이 끝낸다
    second = ask(client, "업로드 같은 긴 요청에서만 실패해", first["thread_id"])
    assert "no_evidence" not in steps(second) and "clarify_answer" in steps(second)
    called = [t for t in second["trace"] if t["step"] == "tool_call" and t["input"]["tool"] == "retrieve_runbook"]
    assert called and "RB-04" in ids(second) and "RB-03" not in ids(second)  # 코드가 합친 질문으로 검색했다
    assert second["contexts"] and second["status"] == "completed"


def test_no_forced_retrieval_for_an_ordinary_turn(tmp_path):
    from fastapi.testclient import TestClient
    from src.agent import RunbookAgent
    from src.server import create_app
    from tests.conftest import FakeEmbeddings
    llm = ToggleChat(silent=True)
    client = TestClient(create_app(RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state", use_mcp=False)))
    r = ask(client, "api-gateway 502가 간헐적으로 나")  # 합친 턴이 아니면 모델이 검색하지 않아도 코드가 대신 검색하지 않는다
    assert "no_evidence" in steps(r) and not any(t["step"] == "tool_call" for t in r["trace"])


# ---------- 되묻기 답의 운영 데이터, 두 원인 비교 ----------
def test_clarification_includes_status_and_past_incidents_but_not_their_actions(client):
    r = ask(client, "api-gateway 502가 나. 상태 확인하고 과거 사례도 알려줘")  # FakeChat: 상태·이력 도구도 부른다
    assert "clarify" in steps(r)
    assert "**비슷한 과거 장애와 현재 상태**" in r["answer"]
    assert "현재 상태 · api-gateway" in r["answer"] and "비슷한 과거 장애 · INC-" in r["answer"]
    docs = {c["doc_id"] for c in r["contexts"]}
    assert "status/api-gateway" in docs and any(d.startswith("incidents/INC-") for d in docs)  # 인용한 운영 데이터도 근거로 담긴다
    incident_lines = [ln for ln in r["answer"].splitlines() if "비슷한 과거 장애" in ln]
    assert incident_lines and all("조치" not in ln for ln in incident_lines)  # 과거에 한 조치는 쓰지 않는다


def test_naming_both_causes_fills_in_the_runbook_the_search_missed(agent, client, monkeypatch):
    from langchain_core.runnables import RunnableLambda
    real = agent.retriever.retrieve_traced

    def only_failover(query):  # 검색이 RB-06만 돌려준 경우
        docs, steps_ = real(query)
        return [d for d in docs if "rb-06" in d.doc_id], steps_

    agent.retriever.retrieve_traced = only_failover
    final_prompts = []
    orig = type(agent.llm).with_structured_output

    def spy(self, schema, **kwargs):  # 최종 답 단계에 들어가는 입력을 기록한다
        runnable = orig(self, schema, **kwargs)
        if schema.__name__ != "FinalAnswer":
            return runnable
        return RunnableLambda(lambda x: (final_prompts.append(" ".join(str(getattr(m, "content", m)) for m in x)), runnable.invoke(x))[1])

    monkeypatch.setattr(type(agent.llm), "with_structured_output", spy)
    r = ask(client, "db 연결 타임아웃이 커넥션 풀 고갈인지 페일오버 중인지 구분해줘")
    assert "clarify" not in steps(r) and {"RB-05", "RB-06"} <= ids(r)
    fill = next(t for t in r["trace"] if t["step"] == "runbook_fill")
    assert fill["input"]["runbooks"] == ["RB-05"]
    assert final_prompts and "[런북 원문: 검색에서 빠진 런북]" in final_prompts[-1] and "두 원인(RB-05, RB-06)을 비교" in final_prompts[-1]


def test_nothing_is_filled_when_the_search_already_found_the_named_runbooks(client):
    r = ask(client, "db 연결 타임아웃이 커넥션 풀 고갈인지 페일오버 중인지 구분해줘")
    assert "runbook_fill" not in steps(r) and {"RB-05", "RB-06"} <= ids(r)


def test_clarification_fetches_ops_data_in_code_when_the_model_did_not(client, agent):
    r = ask(client, "api-gateway 응답이 느리고 502가 간헐적으로 나")  # FakeChat은 상태·이력 도구를 부르지 않는다(과거·이력·사례·상태 말이 없다)
    assert "clarify" in steps(r)
    called = [t["input"]["tool"] for t in r["trace"] if t["step"] == "tool_call"]
    assert "retrieve_runbook" in called and "search_incidents" in called and "get_service_status" in called
    assert "비슷한 과거 장애 · INC-" in r["answer"] and "현재 상태 · api-gateway" in r["answer"]


def test_clarification_does_not_repeat_ops_calls_the_model_already_made(client):
    r = ask(client, "api-gateway 502가 나. 상태 확인하고 과거 사례도 알려줘")
    calls = [t["input"]["tool"] for t in r["trace"] if t["step"] == "tool_call"]
    assert calls.count("search_incidents") == 1 and calls.count("get_service_status") == 1


def test_clarification_leaves_out_past_incidents_that_do_not_match_the_symptom():
    def incident(inc_id, date, symptom, cause, action):
        text = "\n".join([f"[장애 이력] {inc_id}", "서비스: api-gateway", f"발생일: {date}", f"증상: {symptom}",
                          f"원인: {cause}", f"조치: {action}"])
        return {"doc_id": f"incidents/{inc_id}", "text": text}

    ops = [incident("INC-1003", "2026-09-02", "502 에러 간헐 발생", "업스트림 readiness", "재배포"),
           incident("INC-1012", "2026-10-27", "TLS 인증서 만료 경고", "갱신 실패", "갱신")]
    lines, used = dis.ops_reference(ops, "api-gateway 502가 나", ("502", "504", "게이트웨이", "gateway"))
    assert any("INC-1003" in ln for ln in lines) and not any("INC-1012" in ln for ln in lines)
    assert [c["doc_id"] for c in used] == ["incidents/INC-1003"]
    assert all("조치" not in ln for ln in lines)
    all_lines, _ = dis.ops_reference(ops, "x")  # 증상 단서가 없으면 거르지 않는다
    assert len(all_lines) == 2


def test_clarification_options_are_not_markdown_numbers(client):
    r = ask(client, "api-gateway 502가 간헐적으로 나")
    assert "**① " in r["answer"] and "**② " in r["answer"]  # 화면 마크다운이 중첩 목록에서 번호를 다시 시작하므로 원문자를 쓴다
    assert not re.search(r"^\d+\. ", r["answer"], flags=re.M)


@pytest.mark.parametrize("question,group_id,shown,hidden", [
    ("api-gateway 응답이 느리고 502 에러가 간헐적으로 나", "gateway-502", {"INC-1003", "INC-1004"}, {"INC-1012"}),
    ("DB 연결 타임아웃이 계속 나", "db-timeout", {"INC-1005", "INC-1007"}, {"INC-1004"}),  # 게이트웨이 504 타임아웃은 DB 장애가 아니다
    ("Pod가 Pending에서 안 넘어가", "pod-pending", {"INC-1008", "INC-1009"}, {"INC-1002"}),
])
def test_clarification_shows_only_incidents_of_the_same_symptom_in_the_real_data(question, group_id, shown, hidden):
    """실제 장애 이력 DB의 조회 결과를 되묻기 필터에 넣어, 증상이 다른 장애가 섞이지 않는지 본다(모델 호출 없음)."""
    from src.agent import ops_contexts
    from src.tools import query_incidents
    group = next(g for g in GROUPS if g.id == group_id)
    ctx = ops_contexts("search_incidents", {"incidents": query_incidents(question)})
    lines, _ = dis.ops_reference(ctx, question, group.incident_cues)
    got = {inc for inc in shown | hidden if any(inc in ln for ln in lines)}
    assert got == shown, (group_id, got)


def test_incident_cues_default_to_the_symptom_cues_and_db_group_has_its_own():
    by_id = {g.id: g for g in GROUPS}
    assert by_id["gateway-502"].incident_cues == by_id["gateway-502"].symptom_cues  # 지정하지 않으면 증상 단서를 쓴다
    db = by_id["db-timeout"]
    assert db.incident_cues and "타임아웃" not in db.incident_cues  # 서비스 종류를 가리지 않는 말은 쓰지 않는다


@pytest.mark.parametrize("question,group_id,expected", [
    ("api-gateway 응답이 느리고 502 에러가 간헐적으로 나", "gateway-502", {"INC-1003": "① RB-03", "INC-1004": "② RB-04"}),
    ("DB 연결 타임아웃이 계속 나", "db-timeout", {"INC-1005": "① RB-05", "INC-1007": "② RB-06"}),
    ("Pod가 Pending에서 안 넘어가", "pod-pending", {"INC-1008": "① RB-07", "INC-1009": "② RB-08"}),
])
def test_past_incidents_are_linked_to_the_candidate_they_were_in_the_real_data(question, group_id, expected):
    """과거 장애를 나열만 하지 않고, 과거에 어느 후보 원인이었는지 연결한다(정식 평가 P02 심사의 '관련을 설명하지 않았다' 지적)."""
    from src.agent import ops_contexts
    from src.tools import query_incidents
    g = next(g for g in GROUPS if g.id == group_id)
    ctx = ops_contexts("search_incidents", {"incidents": query_incidents(question)})
    lines, _ = dis.ops_reference(ctx, question, g.incident_cues, g.members)
    for inc, label in expected.items():
        line = next(ln for ln in lines if inc in ln)
        assert f"→ 과거에는 {label} 쪽이었다" in line, line
        assert "조치" not in line  # 과거에 한 조치는 쓰지 않는다


def test_incident_link_is_left_out_when_both_or_neither_candidate_matches():
    both = {"doc_id": "incidents/INC-9001", "text": "[장애 이력] INC-9001\n발생일: 2026-01-01\n증상: 502 readiness 실패와 timeout\n원인: x"}
    none = {"doc_id": "incidents/INC-9002", "text": "[장애 이력] INC-9002\n발생일: 2026-01-02\n증상: 502 에러\n원인: 알 수 없음"}
    g = next(g for g in GROUPS if g.id == "gateway-502")
    lines, _ = dis.ops_reference([both, none], "api-gateway 502", g.incident_cues, g.members)
    assert len(lines) == 2 and not any("과거에는" in ln for ln in lines)


def test_clarification_answer_links_past_incidents_in_the_agent_flow(client):
    r = ask(client, "api-gateway 502가 나. 과거 사례도 알려줘")  # FakeChat이 search_incidents를 부른다
    assert "clarify" in steps(r)
    assert "→ 과거에는 ① RB-03 쪽이었다" in r["answer"] and "→ 과거에는 ② RB-04 쪽이었다" in r["answer"]


def test_clarification_intro_mentions_the_past_incidents_in_the_answer_body(client):
    r = ask(client, "api-gateway 502가 나. 과거 사례도 알려줘")
    intro = r["answer"].split("**어느 경우인가요?**")[0]  # 섹션 부록이 아니라 답 본문 서두에서 말한다
    assert "이 증상은 과거에도 있었고(INC-1003, INC-1004)" in intro
    assert "① , ②" not in intro and "①, ② 경우가 모두 있었습니다" in intro
