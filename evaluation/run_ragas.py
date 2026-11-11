"""RAGAS 4지표 산출(고정 대상 아님). 평가 전용 venv(.venv-eval, Python 3.12)에서 실제 ragas로 돈다.
사용: .venv-eval/Scripts/python.exe evaluation/run_ragas.py --round 2 [--run K]
입력: evaluation/ragas_inputs_round{N}.json(평가 실행 산출물), evaluation/ragas_references.json(기준 답안)
LLM·임베딩은 Bedrock(Claude Sonnet 4.5, Titan v2)을 LangChain 래퍼로 연결한다.
"""
import argparse
import json
import math
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
ENV = Path(r"C:\Users\Admin\Desktop\mini-pjt\.env")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--run", type=int, default=0)
    a = ap.parse_args()
    tag = f"{a.round}_run{a.run}" if a.run else str(a.round)
    from dotenv import load_dotenv
    load_dotenv(ENV)
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    import ragas

    refs = json.loads((ROOT / "evaluation/ragas_references.json").read_text(encoding="utf-8"))
    rows = json.loads((ROOT / f"evaluation/ragas_inputs_round{tag}.json").read_text(encoding="utf-8"))
    rows = [r for r in rows if r["id"] in refs and r["contexts"]]
    ds = EvaluationDataset.from_list([{
        "user_input": r["question"], "response": r["answer"],
        "retrieved_contexts": r["contexts"], "reference": refs[r["id"]]} for r in rows])
    llm = LangchainLLMWrapper(ChatBedrockConverse(
        model="us.anthropic.claude-sonnet-4-5-20250929-v1:0", region_name="us-east-1", temperature=0, max_tokens=4096))
    emb = LangchainEmbeddingsWrapper(BedrockEmbeddings(model_id="amazon.titan-embed-text-v2:0", region_name="us-east-1"))
    res = evaluate(ds, metrics=[context_recall, context_precision, faithfulness, answer_relevancy],
                   llm=llm, embeddings=emb, show_progress=False)
    df = res.to_pandas()
    names = ["context_recall", "context_precision", "faithfulness", "answer_relevancy"]
    per_case = []
    for r, (_, row) in zip(rows, df.iterrows()):
        per_case.append({"id": r["id"], **{n: (None if math.isnan(row[n]) else float(row[n])) for n in names}})
    mean = {}
    for n in names:
        vals = [c[n] for c in per_case if c[n] is not None]
        mean[n] = sum(vals) / len(vals) if vals else None
    out = {"round": a.round, "run": a.run or None, "implementation": "ragas", "ragas_version": ragas.__version__,
           "python": "3.12 (.venv-eval)", "cases_scored": [r["id"] for r in rows], "mean": mean, "per_case": per_case}
    (ROOT / f"evaluation/ragas_round{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(mean, ensure_ascii=False))


if __name__ == "__main__":
    main()
