"""MCP 서버 연동 (Day 4): 운영 데이터(장애 이력, 서비스 상태)를 MCP 서버로 노출하고 에이전트가 MCP로 호출한다.

- 서버: `python -m src.mcp_server` (stdio 전송). FastMCP로 도구 두 개를 노출한다.
  데이터 경로는 환경 변수 RUNBOOK_INCIDENTS_DB, RUNBOOK_STATUS_FILE로 바꿀 수 있다.
- 클라이언트: OpsMcpClient가 langchain-mcp-adapters의 MultiServerMCPClient로 서버에 붙어, 동기 코드에서 도구를 호출한다.
  서버 프로세스와 세션은 한 번 열어 다음 호출에 다시 쓰고, 끊기면 새로 연다.

도구 이름과 반환 형식은 src/tools.py의 로컬 구현과 같다. 그래서 에이전트의 도구 이름(search_incidents,
get_service_status)과 trace는 MCP를 쓰든 안 쓰든 달라지지 않는다.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import os
import sys
import threading
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
def _parse_result(result: Any) -> dict:
    if getattr(result, "isError", False):
        raise RuntimeError("MCP 도구 오류: " + " ".join(getattr(c, "text", "") for c in result.content))
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # 반환값이 객체가 아니면 FastMCP가 {"result": ...}로 감싼다.
        return structured["result"] if set(structured) == {"result"} and isinstance(structured["result"], dict) else structured
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text)


class _SessionWorker:
    """MCP 세션 하나를 전용 스레드의 이벤트 루프에서 열어 두고, 요청을 큐로 받아 차례로 처리한다.

    stdio 세션은 연 작업(task) 안에서 닫아야 하므로(anyio 취소 범위), 세션을 여는 코루틴이 끝날 때까지 큐를 돌며 요청을 처리한다.
    서버 프로세스가 죽으면 그 세션의 요청은 예외로 끝나고 워커는 멈춘다. 다음 호출이 새 워커를 띄운다.
    """

    def __init__(self, connections: dict, timeout: float):
        self.timeout = timeout
        self._connections = connections
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._loop = asyncio.new_event_loop()
        self._queue: asyncio.Queue | None = None
        self._thread = threading.Thread(target=self._run, name="ops-mcp-session", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.timeout):
            self.close()
            raise TimeoutError("MCP 서버 세션을 여는 데 시간이 너무 걸렸다")
        if self._start_error is not None:
            raise RuntimeError(f"MCP 서버 세션을 열지 못했다: {self._start_error}") from self._start_error

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as error:  # noqa: BLE001 - 시작 실패는 생성자에서, 그 뒤 실패는 alive=False로 알린다
            if not self._ready.is_set():
                self._start_error = error
        finally:
            self._ready.set()
            # 세션이 끝난 뒤 큐에 남은 요청은 기다리지 않게 바로 실패시킨다(호출한 쪽이 새 세션으로 다시 시도한다).
            while self._queue is not None and not self._queue.empty():
                item = self._queue.get_nowait()
                if item is not None and item[1].set_running_or_notify_cancel():
                    item[1].set_exception(ConnectionError("MCP 세션이 닫혔다"))
            self._loop.close()

    async def _serve(self) -> None:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self._queue = asyncio.Queue()
        client = MultiServerMCPClient(self._connections)
        async with client.session("ops") as session:
            self._ready.set()
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                fn, future = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(await asyncio.wait_for(fn(session), timeout=self.timeout))
                except asyncio.TimeoutError as error:
                    future.set_exception(TimeoutError(f"MCP 도구 호출이 {self.timeout}초 안에 끝나지 않았다"))
                    raise error  # 응답이 없는 세션은 버린다
                except Exception as error:  # noqa: BLE001 - 도구 오류는 호출한 쪽에 돌려주고 세션은 계속 쓴다
                    future.set_exception(error)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and not self._loop.is_closed()

    def submit(self, fn) -> Any:
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (fn, future))
        except RuntimeError as error:  # 루프가 이미 닫혔다
            raise ConnectionError("MCP 세션이 닫혔다") from error
        try:
            return future.result(timeout=self.timeout + 5)
        except concurrent.futures.CancelledError as error:
            raise ConnectionError("MCP 세션이 닫혀 호출이 취소됐다") from error

    def close(self) -> None:
        if self.alive and self._queue is not None:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            except RuntimeError:
                pass
        self._thread.join(timeout=10)


class OpsMcpClient:
    """운영 데이터 MCP 서버를 stdio로 띄워 도구를 호출하는 동기 클라이언트.

    서버 프로세스와 세션은 첫 호출 때 한 번 열고 다음 호출에 다시 쓴다(호출마다 프로세스를 띄우면 건당 3초쯤 걸린다).
    세션이 끊겼으면 한 번 새로 열어 다시 시도한다. close()나 프로세스 종료 때 서버를 내린다.
    """

    def __init__(self, incidents_db: Path = INCIDENTS_DB, status_file: Path = STATUS_FILE,
                 timeout: float = CALL_TIMEOUT_SECONDS):
        self.timeout = timeout
        self._worker: _SessionWorker | None = None
        self._lock = threading.Lock()
        self.sessions_opened = 0
        atexit.register(self.close)
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

    def _ensure_worker(self) -> _SessionWorker:
        if self._worker is None or not self._worker.alive:
            self._worker = _SessionWorker(self.connections, self.timeout)
            self.sessions_opened += 1
        return self._worker

    def _with_session(self, fn):
        with self._lock:
            worker = self._ensure_worker()
            try:
                return worker.submit(fn)
            except ConnectionError:
                # 세션이 중간에 끊겼다(서버 프로세스 종료 등): 새 세션으로 한 번만 다시 시도한다.
                worker.close()
                return self._ensure_worker().submit(fn)
            finally:
                if self._worker is not None and not self._worker.alive:
                    self._worker = None

    def close(self) -> None:
        """열어 둔 MCP 세션을 닫고 서버 프로세스를 내린다. 다시 호출하면 새로 연다."""
        with self._lock:
            if self._worker is not None:
                self._worker.close()
                self._worker = None

    def list_tools(self) -> list[str]:
        async def fn(session):
            from langchain_mcp_adapters.tools import load_mcp_tools
            return [t.name for t in await load_mcp_tools(session)]

        return self._with_session(fn)

    def call(self, name: str, args: dict) -> dict:
        if name not in MCP_TOOL_NAMES:
            raise ValueError(f"MCP 서버에 없는 도구: {name}")

        async def fn(session):
            return _parse_result(await session.call_tool(name, args))

        return self._with_session(fn)


if __name__ == "__main__":
    main()
