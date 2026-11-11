"""1차→2차 개선폭 신뢰성 검사: 커밋 순서, 고정 파일 불변, 해시 일치, 1차 측정 커밋 구성, 리포트 기록."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ["evaluation/test_queries.csv", "evaluation/judge_prompt.md", "evaluation/rubric.py"]
EXTRA = ["src/middleware.py", "src/mcp_server.py", "src/supervisor.py"]


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def commit_order():
    return git("log", "--reverse", "--format=%H").splitlines()


def first_commit(path):
    out = git("log", "--reverse", "--format=%H", "--", path).splitlines()
    return out[0] if out else None


def load(n):
    return json.loads((ROOT / f"evaluation/round{n}_results.json").read_text(encoding="utf-8"))


def digest(rel):
    return hashlib.sha256((ROOT / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_fixed_files_committed_before_any_src_file():
    order = commit_order()
    src_files = [f for f in git("ls-files", "src").splitlines()]
    src_first = min(order.index(first_commit(f)) for f in src_files)
    for p in ["SERVICE.md", *FROZEN]:
        assert order.index(first_commit(p)) < src_first, p


def test_frozen_files_untouched_since_first_src_commit():
    order = commit_order()
    src_first = min(order.index(first_commit(f)) for f in git("ls-files", "src").splitlines())
    for p in FROZEN:
        for c in git("log", "--format=%H", "--", p).splitlines():
            assert order.index(c) < src_first, (p, c)


def test_hashes_equal_across_rounds_and_current_files():
    r1, r2 = load(1), load(2)
    assert r1["frozen_file_hashes"] == r2["frozen_file_hashes"]
    for p in [*FROZEN, "evaluation/ragas_references.json"]:
        assert r1["frozen_file_hashes"][p] == git("rev-parse", "HEAD:" + p)
    for r in (r1, r2):
        for p in FROZEN:
            assert r["frozen_file_hashes"][p] == git("rev-parse", r["measured_commit"] + ":" + p)


def test_round1_commit_has_no_additional_pattern_files():
    c = load(1)["measured_commit"]
    files = git("ls-tree", "-r", "--name-only", c).splitlines()
    for f in EXTRA:
        assert f not in files
    assert c != load(2)["measured_commit"]
    assert load(1)["round"] == 1 and len(load(1)["cases"]) == 16
    assert all(x["judged_by"] in ("rule", "llm") for x in load(1)["cases"])


def test_reports_state_counts_and_improvement():
    s1, s2 = load(1)["summary"], load(2)["summary"]
    r1 = (ROOT / "evaluation/round1_report.md").read_text(encoding="utf-8")
    r2 = (ROOT / "evaluation/round2_report.md").read_text(encoding="utf-8")
    assert f"{s1['passed']} / 16" in r1
    assert f"{s2['passed']} / 16" in r2
    assert f"{s1['passed']}/16" in r2 and f"{s2['passed']}/16" in r2 and "개선폭" in r2
    assert s2["passed"] - s1["passed"] == 3 and "+3건" in r2
