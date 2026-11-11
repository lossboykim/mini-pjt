"""도메인 도구: 런북 검색(RAG), 장애 이력(SQLite), 서비스 상태(가짜 API), 가짜 조치(승인 후에만 실행).

모든 도구 출력은 비밀값을 가린 뒤 돌려준다. LLM도 비밀값을 보지 못한다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from langchain_core.tools import tool

from src.retriever import ROOT, mask_secrets, tokenize

DATA_DIR = ROOT / "data"
INCIDENTS_DB = DATA_DIR / "incidents.db"
STATUS_FILE = DATA_DIR / "service_status.json"

ACTION_TOOLS = {"restart_service"}
READ_TOOLS = {"retrieve_runbook", "search_incidents", "get_service_status"}


# ---------------- 가짜 서비스 상태 API ----------------
def fake_status_api(service: str, status_file: Path = STATUS_FILE) -> dict:
    data = json.loads(Path(status_file).read_text(encoding="utf-8"))
    key = (service or "").strip().lower()
    if key == "all":
        return {"found": True, "service": "all", "services": {k: v for k, v in data.items()}}
    if key in data:
        return {"found": True, "service": key, **data[key]}
    return {"found": False, "service": key, "message": f"서비스 '{service}'를 찾을 수 없다"}


# ---------------- 장애 이력 ----------------
def query_incidents(query: str, db_path: Path = INCIDENTS_DB, limit: int = 3) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM incidents")]
    finally:
        con.close()
    words = {t for t in tokenize(query) if len(t) >= 2}
    scored = []
    for r in rows:
        hay = " ".join(str(v) for v in r.values()).lower()
        hits = sum(1 for w in words if w in hay)
        if r["service"].lower() in query.lower():
            hits += 3
        if hits:
            scored.append((hits, r["occurred_at"], r))
    scored.sort(key=lambda x: -x[0])  # 안정 정렬: 일치 수가 같으면 DB 순서(오래된 순)
    return [{k: mask_secrets(str(v)) for k, v in r.items()} for _, _, r in scored[:limit]]


# ---------------- 가짜 조치 실행 로그 ----------------
class ActionLog:
    """가짜 조치의 실행 로그(JSON Lines). 평가 스크립트가 이 로그로 실행 여부를 검증한다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def has_thread(self, thread_id: str) -> bool:
        return any(e.get("thread_id") == thread_id for e in self.read())

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def execute_restart(service: str, log: ActionLog, status_file: Path = STATUS_FILE, thread_id: str = "") -> dict:
    """가짜 재시작. 승인 노드에서만 호출한다. 실제로 실행했을 때만 로그 한 줄({thread_id, action, target, executed_at})을 남긴다."""
    info = fake_status_api(service, status_file)
    if not info["found"]:
        return {"executed": False, "action": "restart_service", "service": service,
                "message": "존재하지 않는 서비스라 실행하지 않았다"}
    log.append({"thread_id": thread_id, "action": "restart_service", "target": info["service"],
                "executed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return {"executed": True, "action": "restart_service", "service": info["service"],
            "message": f"{info['service']} 롤링 재시작을 (가짜로) 실행했다"}


# ---------------- LLM에 노출하는 도구 ----------------
def make_tools(retriever, incidents_db: Path = INCIDENTS_DB, status_file: Path = STATUS_FILE, ops_call=None) -> list:
    """LLM에 바인딩할 도구 목록. restart_service는 스키마만 있고, 실행은 승인 노드가 맡는다.

    ops_call(name, args, local)을 주면 운영 데이터 도구 두 개는 그 함수를 거친다(MCP 서버 경유용).
    local은 같은 결과를 내는 로컬 구현이며, 도구 이름과 반환 형식은 어느 쪽이든 같다.
    """

    def _ops(name: str, args: dict, local) -> dict:
        return local() if ops_call is None else ops_call(name, args, local)

    @tool
    def retrieve_runbook(query: str) -> dict:
        """장애 증상으로 운영 런북을 검색한다. 증상, 진단 절차, 조치 절차를 돌려준다."""
        docs, steps = retriever.retrieve_traced(query)
        return {"documents": [{"doc_id": d.doc_id, "text": d.text} for d in docs], "retrieval_trace": steps}

    @tool
    def search_incidents(query: str) -> dict:
        """과거 장애 이력을 증상이나 서비스 이름으로 조회한다."""
        return _ops("search_incidents", {"query": query}, lambda: {"incidents": query_incidents(query, incidents_db)})

    @tool
    def get_service_status(service: str) -> dict:
        """서비스의 현재 상태(파드, 에러율, 지연)를 조회한다. service는 서비스 이름(예: payment-api). 서비스를 특정할 수 없으면 "all"로 전체 요약을 본다."""
        return _ops("get_service_status", {"service": service}, lambda: fake_status_api(service, status_file))

    @tool
    def restart_service(service: str) -> dict:
        """서비스를 재시작한다. 위험 조치이며 사람 승인 뒤에만 실행된다."""
        raise RuntimeError("restart_service는 승인 노드에서만 실행된다")

    return [retrieve_runbook, search_incidents, get_service_status, restart_service]
