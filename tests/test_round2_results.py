"""커밋된 2차 결과 파일을 읽어 판정한다(평가를 다시 돌리지 않는다)."""
import json
from pathlib import Path

EV = Path(__file__).resolve().parent.parent / "evaluation"


def load(r):
    return json.loads((EV / f"round{r}_results.json").read_text(encoding="utf-8"))


def test_round2_meets_goal():
    d = load(2)
    assert d["summary"]["total"] == 16 and d["summary"]["passed"] >= 13
    g = [c for c in d["cases"] if c["category"] == "guardrail"]
    assert g and all(c["passed"] and c["judged_by"] == "rule" for c in g)


def test_round2_report_has_counts_and_ragas():
    t = (EV / "round2_report.md").read_text(encoding="utf-8")
    for k in ("context_recall", "context_precision", "faithfulness", "answer_relevancy", "ragas", "개선폭"):
        assert k in t
