"""MCP 서버 연동 (Day 4): 운영 데이터(장애 이력, 서비스 상태)를 MCP 서버로 노출하고 에이전트가 MCP로 호출한다.

- 서버: `python -m src.mcp_server` (stdio 전송). FastMCP로 도구 두 개를 노출한다.
  데이터 경로는 환경 변수 RUNBOOK_INCIDENTS_DB, RUNBOOK_STATUS_FILE로 바꿀 수 있다.
- 클라이언트: OpsMcpClient가 langchain-mcp-adapters의 MultiServerMCPClient로 서버에 붙어, 동기 코드에서 도구를 호출한다.

도구 이름과 반환 형식은 src/tools.py의 로컬 구현과 같다. 그래서 에이전트의 도구 이름(search_incidents,
get_service_status)과 trace는 MCP를 쓰든 안 쓰든 달라지지 않는다.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
from pathlib import Path
from typing import Any

from src.retriever import ROOT
from src.tools import INCIDENTS_DB, STATUS_FILE, fake_status_api, query_incidents

SERVER_NAME = "runbook-ops"
MCP_TOOL_NAMES = ("search_incidents", "get_service_status")
ENV_INCIDENTS_DB = "RUNBOOK_INCIDENTS_DB"
ENV_STATUS_FILE = "RUNBOOK_STATUS_FILE"
CALL_TIMEOUT_SECONDS = 60.0


# ---------------- 서버 ----------------
def build_server():
    from fastmcp import FastMCP

    incidents_db = Path(os.environ.get(ENV_INCIDENTS_DB) or INCIDENTS_DB)
    status_file = Path(os.environ.get(ENV_STATUS_FILE) or STATUS_FILE)
    mcp = FastMCP(SERVER_NAME)

    @mcp.tool()
    def search_incidents(query: str) -> dict:
        """과거 장애 이력을 증상이나 서비스 이름으로 조회한다."""
        return {"incidents": query_incidents(query, incidents_db)}

    @mcp.tool()
    def get_service_status(service: str) -> dict:
        """서비스의 현재 상태(파드, 에러율, 지연)를 조회한다. 서비스를 특정할 수 없으면 "all"로 전체 요약을 본다."""
        return fake_status_api(service, status_file)

    return mcp


def main() -> None:
    build_server().run(transport="stdio", show_banner=False)


# ---------------- 클라이언트 ----------------
def _run_sync(coro):
    """동기 코드에서 코루틴을 끝까지 실행한다. 이미 이벤트 루프가 도는 스레드에서는 별도 스레드를 쓴다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _parse_result(result: Any) -> dict:
    if getattr(result, "isError", False):
        raise RuntimeError("MCP 도구 오류: " + " ".join(getattr(c, "text", "") for c in result.content))
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # 반환값이 객체가 아니면 FastMCP가 {"result": ...}로 감싼다.
        return structured["result"] if set(structured) == {"result"} and isinstance(structured["result"], dict) else structured
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text)


class OpsMcpClient:
    """운영 데이터 MCP 서버를 stdio로 띄워 도구를 호출하는 동기 클라이언트. 호출마다 새 세션을 연다."""

    def __init__(self, incidents_db: Path = INCIDENTS_DB, status_file: Path = STATUS_FILE,
                 timeout: float = CALL_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.connections = {
            "ops": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "src.mcp_server"],
                "cwd": str(ROOT),
                "env": {**os.environ, ENV_INCIDENTS_DB: str(incidents_db), ENV_STATUS_FILE: str(status_file),
                        "PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "ignore"},
            }
        }

    async def _with_session(self, fn):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(self.connections)
        async with client.session("ops") as session:
            return await asyncio.wait_for(fn(session), timeout=self.timeout)

    def list_tools(self) -> list[str]:
        async def fn(session):
            from langchain_mcp_adapters.tools import load_mcp_tools
            return [t.name for t in await load_mcp_tools(session)]

        return _run_sync(self._with_session(fn))

    def call(self, name: str, args: dict) -> dict:
        if name not in MCP_TOOL_NAMES:
            raise ValueError(f"MCP 서버에 없는 도구: {name}")

        async def fn(session):
            return _parse_result(await session.call_tool(name, args))

        return _run_sync(self._with_session(fn))


if __name__ == "__main__":
    main()
