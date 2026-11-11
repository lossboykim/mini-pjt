"""RAG 파이프라인: 쿼리 확장 -> 하이브리드 검색(BM25 + 임베딩, RRF 결합) -> LLM 리랭킹.

런북은 문서 한 개를 한 조각으로 색인한다. 색인과 응답에는 비밀값을 가린 본문만 쓴다.
벡터 색인은 커밋하지 않고 build_index()/scripts/build_index.py로 다시 만든다.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCS_DIR = ROOT / "data" / "runbooks"
DEFAULT_INDEX_DIR = ROOT / "index"

SECRET_RE = re.compile(r"FAKE-SECRET-[A-Za-z0-9_-]*")
REDACTED = "[REDACTED]"


def mask_secrets(text: str) -> str:
    return SECRET_RE.sub(REDACTED, text)


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    """영문/숫자 단어는 그대로, 한글 단어는 단어 + 글자 bigram으로 쪼갠다(조사 변화에 강하게)."""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        out.append(tok)
        if re.match(r"[가-힣]", tok) and len(tok) > 2:
            out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
    return out


@dataclass
class Doc:
    doc_id: str
    text: str  # 비밀값을 가린 파일 전체 내용
    score: float = 0.0


class ExpandedQueries(BaseModel):
    queries: list[str] = Field(description="원 질의와 같은 의미의 대체 검색어(동의어, 영문 용어). 최대 3개")


class DocScore(BaseModel):
    doc_id: str
    score: int = Field(description="질의와의 관련도 0~10. 증상과 원인이 맞을 때만 7 이상")


class RerankResult(BaseModel):
    scores: list[DocScore]


EXPAND_PROMPT = (
    "당신은 쿠버네티스 운영 런북 검색을 돕는다. 아래 질의를 검색에 유리한 대체 표현으로 최대 3개 바꿔라. "
    "서비스 이름과 에러 코드는 그대로 두고 새 사실을 추가하지 마라.\n질의: {query}"
)
RERANK_PROMPT = (
    "아래 질의에 대해 각 런북 후보가 질의의 증상과 원인에 실제로 맞는지 0~10점으로 채점하라. "
    "증상 키워드만 겹치고 원인이나 조치가 다르면 4점 이하를 준다. 질의가 운영 장애와 무관하거나 "
    "후보 중 해당하는 것이 없으면 모두 낮은 점수를 준다.\n질의: {query}\n\n후보:\n{candidates}"
)


class RunbookRetriever:
    def __init__(self, embeddings, llm=None, docs_dir=None, index_dir=None,
                 top_k: int = 3, candidates: int = 6, min_score: int = 6, rrf_k: int = 60):
        self.embeddings = embeddings
        self.llm = llm
        self.docs_dir = Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR
        self.index_dir = Path(index_dir) if index_dir else DEFAULT_INDEX_DIR
        self.top_k, self.candidates, self.min_score, self.rrf_k = top_k, candidates, min_score, rrf_k
        self.docs: list[Doc] = []
        self._vectors: np.ndarray | None = None
        self._bm25: BM25Okapi | None = None

    # ---------- 색인 ----------
    def _load_docs(self) -> list[Doc]:
        files = sorted(self.docs_dir.glob("*.md"))
        return [Doc(p.stem, mask_secrets(p.read_text(encoding="utf-8"))) for p in files]

    @staticmethod
    def _fingerprint(docs: list[Doc]) -> str:
        h = hashlib.sha256()
        for d in docs:
            h.update(d.doc_id.encode() + b"\0" + d.text.encode() + b"\0")
        return h.hexdigest()

    def build_index(self) -> Path:
        docs = self._load_docs()
        vectors = np.array(self.embeddings.embed_documents([d.text for d in docs]), dtype=float)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        np.save(self.index_dir / "vectors.npy", vectors)
        (self.index_dir / "meta.json").write_text(
            json.dumps({"fingerprint": self._fingerprint(docs), "doc_ids": [d.doc_id for d in docs]}),
            encoding="utf-8")
        return self.index_dir

    def load(self) -> None:
        docs = self._load_docs()
        meta_path = self.index_dir / "meta.json"
        fresh = False
        if meta_path.exists() and (self.index_dir / "vectors.npy").exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            fresh = meta.get("fingerprint") == self._fingerprint(docs)
        if not fresh:
            self.build_index()
        self.docs = docs
        self._vectors = np.load(self.index_dir / "vectors.npy")
        self._bm25 = BM25Okapi([tokenize(d.text) for d in docs])

    # ---------- 검색 ----------
    def expand_query(self, query: str) -> list[str]:
        queries = [query]
        if self.llm is None:
            return queries
        try:
            res = self.llm.with_structured_output(ExpandedQueries).invoke(EXPAND_PROMPT.format(query=query))
            for q in res.queries[:3]:
                if q and q not in queries:
                    queries.append(q)
        except Exception:
            pass
        return queries

    def hybrid_search(self, queries: list[str]) -> list[Doc]:
        """질의별 BM25 순위와 벡터 순위를 RRF로 합친다."""
        fused = np.zeros(len(self.docs))
        for q in queries:
            bm = np.array(self._bm25.get_scores(tokenize(q)))
            qv = np.array(self.embeddings.embed_query(q), dtype=float)
            denom = np.linalg.norm(self._vectors, axis=1) * (np.linalg.norm(qv) or 1.0)
            cos = (self._vectors @ qv) / np.where(denom == 0, 1.0, denom)
            for scores in (bm, cos):
                order = np.argsort(-scores)
                for rank, idx in enumerate(order):
                    fused[idx] += 1.0 / (self.rrf_k + rank + 1)
        order = np.argsort(-fused)[: self.candidates]
        return [Doc(self.docs[i].doc_id, self.docs[i].text, float(fused[i])) for i in order]

    def rerank(self, query: str, cands: list[Doc]) -> list[Doc]:
        """LLM이 질의와 맞는 정도를 채점한다. min_score 미만은 버린다(전부 버려지면 근거 없음)."""
        if self.llm is None:
            return cands[: self.top_k]
        listing = "\n\n".join(f"[{d.doc_id}]\n{d.text[:700]}" for d in cands)
        try:
            res = self.llm.with_structured_output(RerankResult).invoke(
                RERANK_PROMPT.format(query=query, candidates=listing))
        except Exception:
            lexical = set(tokenize(query))
            return [d for d in cands if lexical & set(tokenize(d.text))][: self.top_k]
        scores = {s.doc_id: s.score for s in res.scores}
        kept = [Doc(d.doc_id, d.text, float(scores[d.doc_id])) for d in cands
                if scores.get(d.doc_id, 0) >= self.min_score]
        kept.sort(key=lambda d: -d.score)
        return kept[: self.top_k]

    def retrieve(self, query: str) -> list[Doc]:
        if self._bm25 is None:
            self.load()
        queries = self.expand_query(query)
        return self.rerank(query, self.hybrid_search(queries))
