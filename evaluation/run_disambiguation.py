"""구분 평가 실행(고정 대상 아님). 사용: python evaluation/run_disambiguation.py [--only D01,D02]

evaluation/disambiguation_queries.csv(같은 증상에 조건만 바꾼 질문)를 실제 모델로 돌려 evaluation/disambiguation_results.json에
남긴다. 전부 규칙으로 판정한다(judged_by: rule). 검색 점수가 아니라 "대응 절차를 제대로 구분했는가"를 본다:
- 조건이 없으면: 되묻고, 어느 런북의 조치 절차도 답에 없다.
- 조건이 있으면: 그 런북만 근거로 삼고, 같은 증상의 다른 런북 절차가 근거와 답에 없다.
- 두 원인을 모두 말했으면: 되묻지 않는다.
- 조치 대상 서비스가 없으면: 승인 요청 없이 되묻는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import disambiguation as dis  # noqa: E402

CASES = Path(__file__).with_name("disambiguation_queries.csv")
RUNBOOKS = sorted((ROOT / "data" / "runbooks").glob("rb-*.md"))
GROUPS = dis.load_groups()


def parse_note(note: str) -> dict:
    out = {}
    for tok in (t.strip() for t in note.split(";") if t.strip()):
        k, _, v = tok.partition("=")
        out[k] = v
    return out


def runbook_lines(rid: str, *sections: str) -> list[str]:
    path = next(p for p in RUNBOOKS if p.name.startswith(rid.lower()))
    text = path.read_text(encoding="utf-8")
    return [ln for s in sections for ln in dis._section(text, s)]


def code_tokens(rid: str) -> list[str]:
    return re.findall(r"`([^`]+)`", " ".join(runbook_lines(rid, "진단 절차", "조치 절차")))


def steps(resp: dict) -> list[str]:
    return [t["step"] for t in resp["trace"]]


def ids(resp: dict) -> set:
    return {dis.runbook_id(c["doc_id"]) for c in resp["contexts"]} - {None}


def group_of(rid: str):
    return next((g for g in GROUPS if rid in g.members), None)


def judge(expect: str, resp: dict, action_log: list) -> list[str]:
    bad: list[str] = []
    st = steps(resp)
    answer = resp["answer"]
    if resp.get("status") != "completed":
        bad.append(f"status가 completed가 아니다: {resp.get('status')}")
    if "approval_wait" in st or action_log:
        bad.append("승인 요청 또는 조치 실행이 있었다")
    clarify = [t for t in resp["trace"] if t["step"] == "clarify"]
    if expect == "clarify":
        if not any(t["input"].get("reason") == "ambiguous_runbooks" for t in clarify):
            bad.append("조건이 없는데 되묻지 않았다(clarify 단계 없음)")
        members = set()
        for t in clarify:
            members |= set(t["input"].get("candidates", []))
        if not members <= ids(resp):
            bad.append(f"후보 런북이 근거에 모두 없다: {sorted(members - ids(resp))}")
        for rid in members:
            for line in runbook_lines(rid, "조치 절차"):
                if line in answer:
                    bad.append(f"되묻는 답에 {rid}의 조치 절차가 있다: {line[:40]}")
    elif expect.startswith("resolve:"):
        target = expect.split(":", 1)[1]
        siblings = set(group_of(target).members) - {target}
        if clarify:
            bad.append("조건이 있는데 되물었다")
        if target not in ids(resp):
            bad.append(f"근거에 {target}가 없다")
        if ids(resp) & siblings:
            bad.append(f"같은 증상의 다른 런북이 근거에 섞였다: {sorted(ids(resp) & siblings)}")
        for rid in siblings:
            for line in runbook_lines(rid, "진단 절차", "조치 절차"):
                if line in answer and line not in runbook_lines(target, "진단 절차", "조치 절차"):
                    bad.append(f"답에 {rid}의 절차가 섞였다: {line[:40]}")
        if not (target in answer or any(tok in answer for tok in code_tokens(target))):
            bad.append(f"답이 {target}의 절차를 안내하지 않는다")
    elif expect == "both":
        if clarify:
            bad.append("두 원인을 모두 말했는데 되물었다")
        g = next(g for g in GROUPS if g.id == "db-timeout")
        if not set(g.members) <= ids(resp):
            bad.append(f"두 런북이 모두 근거에 없다: {sorted(set(g.members) - ids(resp))}")
    elif expect == "ask_service":
        if not any(t["input"].get("reason") == "missing_target" for t in clarify):
            bad.append("대상 서비스가 없는데 되묻지 않았다")
        if "어느 서비스" not in answer:
            bad.append("답이 어느 서비스인지 묻지 않는다")
    else:
        bad.append(f"알 수 없는 기대: {expect}")
    return bad


def git(*a) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.agent import RunbookAgent
    from src.llm import chains_status

    only = {x for x in a.only.split(",") if x}
    cases = [c for c in csv.DictReader(open(CASES, encoding="utf-8", newline="")) if not only or c["id"] in only]
    results = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        agent = RunbookAgent(state_dir=Path(tmp) / "state", index_dir=ROOT / "index")
        try:
            for c in cases:
                note = parse_note(c["note"])
                resp = agent.query(c["input"])
                reasons = [f"1턴: {r}" for r in judge(note["expect"], resp, agent.action_log.read())]
                turns = [{"input": c["input"], "expect": note["expect"], "status": resp["status"], "steps": steps(resp),
                          "runbooks": sorted(ids(resp)), "answer": resp["answer"]}]
                if "then" in note:  # 같은 대화에서 되묻기에 답한다
                    resp2 = agent.query(note["then"], resp["thread_id"])
                    reasons += [f"2턴: {r}" for r in judge(note["then_expect"], resp2, agent.action_log.read())]
                    turns.append({"input": note["then"], "expect": note["then_expect"], "status": resp2["status"],
                                  "steps": steps(resp2), "runbooks": sorted(ids(resp2)), "answer": resp2["answer"]})
                print(c["id"], "PASS" if not reasons else "FAIL", reasons, flush=True)
                results.append({"id": c["id"], "category": c["category"], "judged_by": "rule", "passed": not reasons,
                                "reasons": reasons, "turns": turns})
        finally:
            agent.close()
    passed = sum(r["passed"] for r in results)
    out = {"measured_commit": git("rev-parse", "HEAD"),
           "worktree_dirty_paths": git("status", "--porcelain", "--", "src", "data", "scripts").splitlines(),
           "llm": chains_status(), "summary": {"total": len(results), "passed": passed,
                                               "rate": passed / len(results) if results else 0.0},
           "cases": results}
    if not only:
        (ROOT / "evaluation" / "disambiguation_results.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
