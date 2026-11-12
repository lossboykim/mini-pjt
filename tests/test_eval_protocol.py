"""평가 세트 고정 검사: 고정 파일이 src/보다 먼저 커밋됐고, 그 뒤로 바뀌지 않았다.

평가 결과 파일(round*_results.json)과 리포트는 2026-11-12에 삭제해, 결과에 기대던 검사(해시 기록, 1차 측정 커밋
구성, 리포트 수치)도 함께 뺐다."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ["evaluation/test_queries.csv", "evaluation/judge_prompt.md", "evaluation/rubric.py"]


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def commit_order():
    return git("log", "--reverse", "--format=%H").splitlines()


def first_commit(path):
    out = git("log", "--reverse", "--format=%H", "--", path).splitlines()
    return out[0] if out else None


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


def test_round_results_share_frozen_hashes_and_round1_has_no_extra_modules():
    import json
    r1 = json.loads((ROOT / "evaluation/round1_results.json").read_text(encoding="utf-8"))
    r2 = json.loads((ROOT / "evaluation/round2_results.json").read_text(encoding="utf-8"))
    for p in FROZEN:
        assert r1["frozen_file_hashes"][p] == r2["frozen_file_hashes"][p] == git("rev-parse", "HEAD:" + p), p
    files = git("ls-tree", "-r", "--name-only", r1["measured_commit"]).splitlines()
    for f in ("src/middleware.py", "src/mcp_server.py", "src/supervisor.py"):
        assert f not in files, f


def test_reports_state_counts_and_improvement():
    for r in (1, 2):
        assert "/ 16" in (ROOT / f"evaluation/round{r}_report.md").read_text(encoding="utf-8")
    assert "개선폭" in (ROOT / "evaluation/round2_report.md").read_text(encoding="utf-8")
