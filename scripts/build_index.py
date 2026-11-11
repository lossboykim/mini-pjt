"""런북 벡터 색인을 다시 만든다(색인은 커밋하지 않는다). 사용: python scripts/build_index.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent import default_embeddings  # noqa: E402
from src.retriever import RunbookRetriever  # noqa: E402

if __name__ == "__main__":
    path = RunbookRetriever(default_embeddings()).build_index()
    print(f"색인 생성: {path}")
