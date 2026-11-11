"""FastAPI 서버: POST /query, POST /approve, GET /actions(가짜 조치 실행 로그), GET /services(서비스 상태), GET /(웹 화면)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"


class QueryRequest(BaseModel):
    question: str
    thread_id: Optional[str] = None


class ApproveRequest(BaseModel):
    thread_id: str
    decision: Literal["approve", "reject"]


def create_app(agent=None) -> FastAPI:
    app = FastAPI(title="런북 대응 어시스턴트")
    holder = {"agent": agent}

    def get_agent():
        if holder["agent"] is None:
            from src.agent import RunbookAgent
            holder["agent"] = RunbookAgent()
        return holder["agent"]

    @app.post("/query")
    def query(req: QueryRequest):
        return get_agent().query(req.question, req.thread_id)

    @app.post("/approve")
    def approve(req: ApproveRequest):
        try:
            return get_agent().approve(req.thread_id, req.decision)
        except LookupError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.get("/actions")
    def actions():
        return {"actions": get_agent().action_log.read()}

    @app.get("/services")
    def services():
        """화면 옆 패널용 서비스 상태 목록. 에이전트를 거치지 않고 가짜 상태 API를 그대로 읽는다."""
        from src.tools import fake_status_api
        return fake_status_api("all")["services"]

    @app.get("/", include_in_schema=False)
    def ui():
        return FileResponse(UI_FILE, media_type="text/html; charset=utf-8")

    return app


app = create_app()
