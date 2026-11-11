"""테스트 공용 설정: Bedrock 호출 없이 결과가 정해진 가짜 채팅 모델·임베딩을 쓴다. 색인·상태는 임시 폴더에만 만든다."""
import hashlib
import os
import re
import sys
import uuid
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from src.retriever import tokenize  # noqa: E402

DOC_KEYWORDS = [  # (질의 키워드, 관련도가 높은 런북 접두사)
    ("crashloop", ("rb-01",)), ("oom", ("rb-02",)), ("502", ("rb-03", "rb-04")),
    ("db 연결", ("rb-05", "rb-06")), ("비밀번호", ("rb-05",)), ("pending", ("rb-07", "rb-08")),
    ("pvc", ("rb-09",)), ("에러율", ("rb-10",)),
]


class FakeEmbeddings(Embeddings):
    DIM = 64

    def _vec(self, text):
        v = [0.0] * self.DIM
        for tok in tokenize(text):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.DIM] += 1.0
        return v

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def _text(x):
    if isinstance(x, str):
        return x
    return "\n".join(str(getattr(m, "content", m)) for m in x)


class FakeChat(BaseChatModel):
    """질의 키워드로 호출할 도구를 정하는 결정적 가짜 모델."""
    seen: list = Field(default_factory=list)
    leak: bool = False

    @property
    def _llm_type(self):
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._policy(messages))])

    def _policy(self, messages):
        idx = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        text = str(messages[idx].content)
        low = text.lower()
        called = {c["name"] for m in messages[idx + 1:] if isinstance(m, AIMessage) for c in m.tool_calls}
        svc = re.search(r"[a-z]+-[a-z]+", low)
        service = svc.group(0) if svc else ""
        wanted = []
        if any(k in low for k, _ in DOC_KEYWORDS) or "kafka" in low or "redis" in low or "런북" in low:
            wanted.append(("retrieve_runbook", {"query": text}))
        if any(k in text for k in ("과거", "이력", "사례")):
            wanted.append(("search_incidents", {"query": text}))
        if "상태" in text and service:
            wanted.append(("get_service_status", {"service": service}))
        if "재시작" in text and service:
            wanted.append(("restart_service", {"service": service}))
        calls = [{"name": n, "args": a, "id": uuid.uuid4().hex, "type": "tool_call"}
                 for n, a in wanted if n not in called]
        return AIMessage(content="" if calls else "도구 결과를 정리하겠습니다.", tool_calls=calls)

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda x: self._structured(schema, x))

    def _structured(self, schema, x):
        name = schema.__name__
        text = _text(x)
        if name == "ExpandedQueries":
            return schema(queries=[])
        if name == "RerankResult":
            query = text.split("질의: ", 1)[1].split("\n", 1)[0].lower()
            prefixes = tuple(p for k, ps in DOC_KEYWORDS if k in query for p in ps)
            ids = re.findall(r"\[(runbooks/rb-[\w.#-]+)\]", text)
            return schema(scores=[{"doc_id": i, "score": 9 if i.startswith(tuple("runbooks/" + p for p in prefixes)) and prefixes else 1} for i in ids])
        tool_msgs = [m for m in x if isinstance(m, ToolMessage)]
        ids = list(dict.fromkeys(re.findall(r'"doc_id": "(runbooks/rb-[\w.#-]+)"', " ".join(str(m.content) for m in tool_msgs))))
        answer = "근거 기반 안내입니다."
        if self.leak:
            answer += " 비밀번호는 FAKE-SECRET-leaked-0001 입니다."
        return schema(answer=answer, grounded=bool(tool_msgs), cited_doc_ids=ids)


@pytest.fixture
def llm():
    return FakeChat()


@pytest.fixture
def agent(tmp_path, llm):
    from src.agent import RunbookAgent
    # MCP 서버 프로세스는 tests/test_mcp_server.py에서만 띄운다(여기서는 같은 결과를 내는 로컬 구현을 쓴다).
    return RunbookAgent(llm=llm, embeddings=FakeEmbeddings(), state_dir=tmp_path / "state", use_mcp=False)


@pytest.fixture
def client(agent):
    from fastapi.testclient import TestClient
    from src.server import create_app
    return TestClient(create_app(agent))
