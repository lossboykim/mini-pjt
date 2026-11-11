"""평가 실행 스크립트(고정 대상 아님). 사용: python evaluation/run_eval.py --round 2

고정 파일(test_queries.csv, judge_prompt.md, rubric.py)을 그대로 읽어 케이스를 돌리고
evaluation/round{N}_results.json 에 측정한 커밋, 고정 파일 해시, 케이스별 판정(judged_by)을 기록한다.
에이전트는 임시 state 폴더에서 새로 만들어 실행 로그(action_log)를 케이스마다 비교한다.
RAGAS 입력은 evaluation/ragas_inputs_round{N}.json 으로 남긴다(산출은 evaluation/run_ragas.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

import rubric  # noqa: E402

FROZEN = ["evaluation/test_queries.csv", "evaluation/judge_prompt.md", "evaluation/rubric.py"]
ENV_PATHS = [ROOT / ".env", Path(r"C:\Users\Admin\Desktop\mini-pjt\.env")]


def sha256(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def load_env():
    from dotenv import load_dotenv
    for p in ENV_PATHS:
        if p.exists():
            load_dotenv(p)
            return


def llm_judge(llm, case: dict, resp: dict) -> dict:
    prompt = (ROOT / "evaluation/judge_prompt.md").read_text(encoding="utf-8")
    trace = [{"step": t["step"], "output": t.get("output")} for t in resp.get("trace", [])]
    filled = (prompt.replace("{input}", case["input"]).replace("{expected_traits}", case["expected_traits"])
              .replace("{forbidden}", case["forbidden"]).replace("{answer}", str(resp.get("answer", "")))
              .replace("{contexts}", json.dumps(resp.get("contexts", []), ensure_ascii=False))
              .replace("{trace}", json.dumps(trace, ensure_ascii=False)))
    text = llm.invoke(filled).content
    if isinstance(text, list):
        text = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in text)
    s, e = text.find("{"), text.rfind("}")
    try:
        v = json.loads(text[s:e + 1])
        return {"pass": bool(v.get("pass")), "reason": str(v.get("reason", ""))}
    except Exception:
        return {"pass": False, "reason": "심사 출력 파싱 실패: " + text[:200]}


def run_case(agent, case: dict) -> dict:
    decision = rubric.note_tokens(case)["decision"]
    resp = agent.query(case["input"])
    before = list(agent.action_log.read())
    approval = None
    if resp["status"] == "awaiting_approval" and decision:
        approval = agent.approve(resp["thread_id"], decision)
    return {"response": resp, "approval_response": approval, "decision": decision,
            "action_log_before_approval": before, "action_log_after": list(agent.action_log.read())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    load_env()
    from src.agent import RunbookAgent, default_llm

    cases = rubric.load_cases()
    only = {x for x in a.only.split(",") if x}
    judge_llm = default_llm()
    verdicts, records, ragas_rows = {}, {}, []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for case in cases:
            if only and case["id"] not in only:
                continue
            agent = RunbookAgent(state_dir=Path(tmp) / case["id"], index_dir=Path(tmp) / "index")
            rec = run_case(agent, case)
            final = rec["approval_response"] or rec["response"]
            llm_v = None if case["category"] == "guardrail" else llm_judge(judge_llm, case, final)
            v = rubric.judge_case(case, rec, llm_v)
            v.update({"category": case["category"], "input": case["input"],
                      "status": final["status"], "tools_called": rubric.tools_called(final["trace"]),
                      "answer": final["answer"], "llm_verdict": llm_v})
            verdicts[case["id"]] = v
            records[case["id"]] = rec
            ragas_rows.append({"id": case["id"], "category": case["category"], "question": case["input"],
                               "answer": final["answer"], "contexts": [c["text"] for c in final["contexts"]]})
            print(case["id"], "PASS" if v["passed"] else "FAIL", v["reasons"], flush=True)
    summary = rubric.summarize(verdicts, [c for c in cases if c["id"] in verdicts])
    out = {
        "round": a.round,
        "measured_commit": git("rev-parse", "HEAD"),
        "worktree_dirty_paths": git("status", "--porcelain", "--", "src", "data", "scripts").splitlines(),
        "frozen_file_hashes": {p: sha256(p) for p in FROZEN},
        "summary": summary,
        "cases": [{"id": cid, "judged_by": v["judged_by"], "passed": v["passed"], "category": v["category"],
                   "reasons": v["reasons"], "tools_called": v["tools_called"], "status": v["status"],
                   "llm_verdict": v["llm_verdict"], "answer": v["answer"]} for cid, v in verdicts.items()],
    }
    (ROOT / f"evaluation/round{a.round}_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / f"evaluation/ragas_inputs_round{a.round}.json").write_text(
        json.dumps(ragas_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
