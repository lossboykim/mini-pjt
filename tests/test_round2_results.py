import subprocess
"""커밋된 2차 평가 결과 파일을 읽어 판정한다. 평가는 다시 돌리지 않는다."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RESULTS = json.loads((ROOT / "evaluation/round2_results.json").read_text(encoding="utf-8"))
REPORT = (ROOT / "evaluation/round2_report.md").read_text(encoding="utf-8")
FROZEN = ["evaluation/test_queries.csv", "evaluation/judge_prompt.md", "evaluation/rubric.py"]


def test_passes_at_least_13_of_16():
    cases = RESULTS["cases"]
    assert len(cases) == 16
    assert sum(1 for c in cases if c["passed"]) >= 13
    assert RESULTS["summary"]["passed"] == sum(1 for c in cases if c["passed"])


def test_all_guardrail_cases_pass_and_are_rule_judged():
    g = [c for c in RESULTS["cases"] if c["category"] == "guardrail"]
    assert len(g) == 4
    assert all(c["passed"] and c["judged_by"] == "rule" for c in g)
    assert all(c["judged_by"] == "llm" for c in RESULTS["cases"] if c["category"] != "guardrail")


def test_results_cover_frozen_cases():
    import sys
    sys.path.insert(0, str(ROOT / "evaluation"))
    import rubric
    assert [c["id"] for c in RESULTS["cases"]] == [c["id"] for c in rubric.load_cases()]


def test_frozen_hashes_match_files_and_commit_recorded():
    for p in [*FROZEN, "evaluation/ragas_references.json"]:
        assert RESULTS["frozen_file_hashes"][p] == subprocess.run(["git", "rev-parse", "HEAD:" + p], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert len(RESULTS["measured_commit"]) == 40


def test_report_has_counts_and_ragas_metrics():
    assert "14 / 16" in REPORT
    for m in ("context_recall", "context_precision", "faithfulness", "answer_relevancy"):
        assert m in REPORT
    assert "ragas" in REPORT and "실제 ragas" in REPORT
    ragas = json.loads((ROOT / "evaluation/ragas_round2.json").read_text(encoding="utf-8"))
    assert ragas["implementation"] == "ragas"
    for m, v in ragas["mean"].items():
        assert v is not None and f"{v:.3f}" in REPORT
