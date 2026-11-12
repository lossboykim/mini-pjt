"""구분 평가 세트와 판정 규칙 검사(모델 호출 없음). 평가 수치는 다시 재지 않고, 규칙이 좋은 답과 나쁜 답을 가르는지만 본다."""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))

import run_disambiguation as rd  # noqa: E402
from src import disambiguation as dis  # noqa: E402

CASES = list(csv.DictReader(open(ROOT / "evaluation" / "disambiguation_queries.csv", encoding="utf-8", newline="")))
COLUMNS = ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note"]


def test_case_file_follows_the_frozen_schema_and_is_separate_from_the_frozen_set():
    assert list(CASES[0]) == COLUMNS and len({c["id"] for c in CASES}) == len(CASES)
    assert all(c["category"] in {"positive", "negative", "edge", "guardrail"} for c in CASES)
    frozen = list(csv.DictReader(open(ROOT / "evaluation" / "test_queries.csv", encoding="utf-8", newline="")))
    assert not ({c["id"] for c in CASES} & {c["id"] for c in frozen})  # 고정 평가 세트와 겹치지 않는다


def test_every_runbook_pair_is_tested_with_and_without_the_condition():
    expects = [rd.parse_note(c["note"]) for c in CASES]
    for g in rd.GROUPS:
        questions = {c["input"]: rd.parse_note(c["note"])["expect"] for c in CASES}
        resolved = {e["expect"].split(":")[1] for e in expects if e["expect"].startswith("resolve:")} & set(g.members)
        assert resolved == set(g.members), f"{g.id}: 런북마다 조건만 바꾼 질문이 하나씩 있어야 한다"
        assert any(v == "clarify" for v in questions.values())
    assert any(e["expect"] == "both" for e in expects) and any(e["expect"] == "ask_service" for e in expects)
    assert any("then" in e for e in expects)  # 같은 대화에서 이어 묻는 경우


def _resp(runbooks, answer, extra_steps=(), reason="ambiguous_runbooks", members=("RB-03", "RB-04"), status="completed"):
    trace = [{"step": s, "input": {}, "output": ""} for s in extra_steps]
    if "clarify" in extra_steps:
        trace = [{"step": "clarify", "input": {"reason": reason, "candidates": list(members)}, "output": ""}]
    ctx = [{"doc_id": f"runbooks/{next(p.name for p in rd.RUNBOOKS if p.name.startswith(r.lower()))}#chunk-0", "text": "x"}
           for r in runbooks]
    return {"answer": answer, "contexts": ctx, "trace": trace, "status": status}


def test_judge_accepts_a_good_clarification_and_rejects_picking_an_action():
    good = _resp(["RB-03", "RB-04"], "어느 경우인가요?", ["clarify"])
    assert rd.judge("clarify", good, []) == []
    picked = _resp(["RB-03", "RB-04"], "다음을 하세요: " + rd.runbook_lines("RB-03", "조치 절차")[0], ["clarify"])
    assert any("조치 절차" in r for r in rd.judge("clarify", picked, []))
    no_ask = _resp(["RB-03"], "RB-03 절차입니다", [])
    assert any("되묻지 않았다" in r for r in rd.judge("clarify", no_ask, []))
    assert any("승인 요청 또는 조치 실행" in r for r in rd.judge("clarify", good, [{"action": "restart_service"}]))


def test_judge_flags_a_mixed_up_answer_when_the_condition_was_given():
    ok = _resp(["RB-03"], "RB-03 절차: `kubectl get endpoints <upstream-service>`", [])
    assert rd.judge("resolve:RB-03", ok, []) == []
    mixed_ctx = _resp(["RB-03", "RB-04"], "RB-03 절차", [])
    assert any("섞였다" in r for r in rd.judge("resolve:RB-03", mixed_ctx, []))
    sibling_line = rd.runbook_lines("RB-04", "조치 절차")[0]
    mixed_answer = _resp(["RB-03"], "RB-03 절차입니다. " + sibling_line, [])
    assert any("RB-04의 절차가 섞였다" in r for r in rd.judge("resolve:RB-03", mixed_answer, []))
    asked = _resp(["RB-03"], "RB-03", ["clarify"])
    assert any("되물었다" in r for r in rd.judge("resolve:RB-03", asked, []))


def test_judge_for_missing_target_and_both_causes():
    ask = _resp([], "어느 서비스를 재시작할지 알려 주세요", ["clarify"], reason="missing_target")
    assert rd.judge("ask_service", ask, []) == []
    approval = {**ask, "trace": ask["trace"] + [{"step": "approval_wait", "input": {}, "output": ""}], "status": "awaiting_approval"}
    assert rd.judge("ask_service", approval, [])
    both = _resp(["RB-05", "RB-06"], "원인별로 안내", [])
    assert rd.judge("both", both, []) == []
    assert rd.judge("both", _resp(["RB-05"], "x", []), [])


def test_case_questions_resolve_as_expected_by_the_cue_rules():
    """실제 모델 없이도, 각 질문이 단서 규칙에서 기대한 결정(되묻기/한 런북/둘 다)으로 나오는지 확인한다."""
    retrieved = [f"runbooks/{p.name}#chunk-0" for p in rd.RUNBOOKS]
    for c in CASES:
        note = rd.parse_note(c["note"])
        for question, expect in [(c["input"], note["expect"])] + ([(note["then"], note["then_expect"])] if "then" in note else []):
            if expect == "ask_service":
                assert not dis.target_named("", question)
                continue
            text = question if question == c["input"] else f"{c['input']}\n{question}"  # 이어 묻기는 앞 질문도 단서로 쓴다
            (d,) = dis.decide(rd.GROUPS, retrieved, text)
            want = {"clarify": dis.AMBIGUOUS, "both": dis.BOTH}.get(expect, dis.RESOLVED)
            assert d.kind == want, (c["id"], d.kind, expect)
            if expect.startswith("resolve:"):
                assert d.resolved == [expect.split(":")[1]], (c["id"], d.resolved)
