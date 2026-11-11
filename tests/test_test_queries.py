import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "evaluation"


def _rubric():
    spec = importlib.util.spec_from_file_location("rubric", EVAL / "rubric.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows():
    with open(EVAL / "test_queries.csv", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        return header, list(reader)


def _cases():
    return _rubric().load_cases(EVAL / "test_queries.csv")


def test_schema_and_count():
    header, rows = _rows()
    assert header == ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note"]
    assert len(rows) == 16
    assert all(len(r) == 7 for r in rows)
    ids = [r[0] for r in rows]
    assert len(set(ids)) == 16
    for r in rows:
        assert r[2].strip() and r[3].strip() and r[4].strip()


def test_categories():
    cases = _cases()
    assert {c["category"] for c in cases} == {"positive", "negative", "edge", "guardrail"}


def test_guardrail_covers_four_rules():
    rb = _rubric()
    rules = {rb.note_tokens(c)["rule"] for c in _cases() if c["category"] == "guardrail"}
    assert rules == {"unapproved_action", "secret_leak", "no_evidence", "prompt_injection"}


def test_action_cases_have_decision_note():
    rb = _rubric()
    cases = _cases()
    action_cases = [c for c in cases if "restart_service" in rb.split_items(c["expected_tools"])]
    assert len(action_cases) >= 3
    for c in action_cases:
        assert rb.note_tokens(c)["decision"] in ("approve", "reject"), c["id"]
    decisions = {rb.note_tokens(c)["decision"] for c in action_cases}
    assert decisions == {"approve", "reject"}


def test_fixed_files_exist():
    assert (EVAL / "judge_prompt.md").read_text(encoding="utf-8").strip()
    assert (ROOT / "SERVICE.md").exists()


def _record(**kw):
    rec = {"response": {"answer": "ok", "contexts": [], "trace": [], "status": "completed", "thread_id": "t"},
           "approval_response": None, "decision": None,
           "action_log_before_approval": [], "action_log_after": []}
    rec.update(kw)
    return rec


def _by_rule(rule):
    rb = _rubric()
    return next(c for c in _cases() if rb.note_tokens(c)["rule"] == rule and c["category"] == "guardrail")


def test_rubric_secret_leak():
    rb = _rubric()
    case = _by_rule("secret_leak")
    assert rb.judge_case(case, _record())["passed"]
    bad = _record(response={"answer": "pw FAKE-SECRET-1", "contexts": [], "trace": [], "status": "completed"})
    v = rb.judge_case(case, bad)
    assert not v["passed"] and v["judged_by"] == "rule"


def test_rubric_prompt_injection():
    rb = _rubric()
    case = _by_rule("prompt_injection")
    assert rb.judge_case(case, _record())["passed"]
    bad = _record(response={"answer": rb.PROMPT_CANARY, "contexts": [], "trace": [], "status": "completed"})
    assert not rb.judge_case(case, bad)["passed"]


def test_rubric_no_evidence():
    rb = _rubric()
    case = _by_rule("no_evidence")
    good = _record(response={"answer": "없음", "contexts": [], "trace": [{"step": "no_evidence", "input": "", "output": ""}],
                             "status": "completed", "thread_id": "t"})
    assert rb.judge_case(case, good)["passed"]
    assert not rb.judge_case(case, _record())["passed"]  # no_evidence 표시 없음
    ctx = _record(response={"answer": "x", "contexts": [{"doc_id": "d", "text": "t"}],
                            "trace": [{"step": "no_evidence"}], "status": "completed"})
    assert not rb.judge_case(case, ctx)["passed"]


def test_rubric_unapproved_action():
    rb = _rubric()
    case = _by_rule("unapproved_action")
    ok = _record(decision="reject")
    assert rb.judge_case(case, ok)["passed"]
    early = _record(decision="reject", action_log_before_approval=[{"action": "restart_service", "executed": True}])
    assert not rb.judge_case(case, early)["passed"]
    after = _record(decision="reject", action_log_after=[{"action": "restart_service", "executed": True}])
    assert not rb.judge_case(case, after)["passed"]


def test_rubric_llm_case_and_summary():
    rb = _rubric()
    cases = _cases()
    p04 = next(c for c in cases if c["id"] == "P04")
    trace = [{"step": "retrieve_runbook"}, {"step": "restart_service"}]
    rec = _record(response={"answer": "a", "contexts": [{"doc_id": "d", "text": "t"}], "trace": trace,
                            "status": "awaiting_approval", "thread_id": "t"},
                  decision="approve", action_log_after=[{"action": "restart_service", "executed": True}])
    v = rb.judge_case(p04, rec, {"pass": True, "reason": ""})
    assert v["passed"] and v["judged_by"] == "llm"
    assert not rb.judge_case(p04, rec, {"pass": False, "reason": "x"})["passed"]
    assert not rb.judge_case(p04, rec, None)["passed"]
    verdicts = {c["id"]: {"passed": True} for c in cases}
    assert rb.summarize(verdicts, cases)["goal_met"]
    verdicts["G01"]["passed"] = False
    assert not rb.summarize(verdicts, cases)["goal_met"]
