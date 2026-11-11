"""FastAPI 서버: POST /query, POST /approve, GET /actions(가짜 조치 실행 로그)."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


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

    return app


app = create_app()
