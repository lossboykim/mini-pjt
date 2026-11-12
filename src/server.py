"""FastAPI 서버: POST /query, POST /approve, GET /actions(가짜 조치 실행 로그), GET /services(서비스 상태),
POST·GET /feedback(답변 피드백), GET /(웹 화면)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"


class QueryRequest(BaseModel):
    question: str
    thread_id: Optional[str] = None


class ApproveRequest(BaseModel):
    thread_id: str
    decision: Literal["approve", "reject"]


class FeedbackRequest(BaseModel):
    """답변 피드백: 결론이 맞았는지와, 틀렸다면 실제 원인이나 빠진 점."""
    thread_id: str = Field(min_length=1, max_length=100)
    verdict: Literal["accurate", "inaccurate"]
    note: str = Field(default="", max_length=1000)
    question: str = Field(default="", max_length=1000)


def create_app(agent=None) -> FastAPI:
    app = FastAPI(title="런북 대응 어시스턴트")
    holder = {"agent": agent, "feedback": None}

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

    # ---------- 웹 화면용 API(조사 단계 스트리밍, 장애 이력, 모델 상태) ----------
    @app.post("/query/stream")
    def query_stream(req: QueryRequest):
        """/query와 같은 일을 하되 조사 단계를 JSON 한 줄씩(NDJSON) 흘려보낸다.
        이벤트: start{thread_id} → step{step} … → result{response(/query와 같은 응답)} | error{message}"""
        return StreamingResponse(_ndjson(get_agent().query_stream(req.question, req.thread_id)),
                                 media_type="application/x-ndjson")

    @app.post("/approve/stream")
    def approve_stream(req: ApproveRequest):
        """/approve와 같은 일을 단계별로 흘려보낸다. 승인할 수 없는 대화면 /approve처럼 409."""
        try:
            events = get_agent().approve_stream(req.thread_id, req.decision)
        except LookupError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return StreamingResponse(_ndjson(events), media_type="application/x-ndjson")

    @app.get("/incidents")
    def incidents(limit: int = 20):
        """최근 장애 이력(발생일 내림차순, 비밀값은 가린다)."""
        from src.tools import list_incidents
        return {"incidents": list_incidents(get_agent().incidents_db, max(1, min(limit, 100)))}

    @app.get("/models")
    def models():
        """역할별로 지금 쓰는 Bedrock 모델과 일일 한도 대체 기록. 확인 요청을 새로 보내지 않는다."""
        from src.llm import ROLE_CANDIDATES, chains_status
        status = chains_status()
        return {role: status.get(role, {"role": role, "current": cands[0].label, "configured": cands[0].label,
                                        "checked": False, "events": []})
                for role, cands in ROLE_CANDIDATES.items()}

    @app.post("/feedback")
    def feedback(req: FeedbackRequest):
        """답변 피드백을 실행 로그 옆 feedback.jsonl에 한 줄로 남긴다. 비밀값은 가려서 저장한다."""
        import time
        from src.retriever import mask_secrets
        entry = {"thread_id": req.thread_id, "verdict": req.verdict, "note": mask_secrets(req.note.strip()),
                 "question": mask_secrets(req.question.strip()), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        feedback_log().append(entry)
        return {"saved": True, "feedback": entry}

    @app.get("/feedback")
    def feedback_list(limit: int = 50):
        """최근 피드백(최신순)과 정확·부정확 개수."""
        items = feedback_log().read()
        counts = {v: sum(1 for e in items if e.get("verdict") == v) for v in ("accurate", "inaccurate")}
        return {"counts": counts, "feedback": items[::-1][: max(1, min(limit, 200))]}

    def feedback_log():
        if holder["feedback"] is None:
            from src.tools import ActionLog
            holder["feedback"] = ActionLog(get_agent().action_log.path.parent / "feedback.jsonl")
        return holder["feedback"]

    @app.get("/", include_in_schema=False)
    def ui():
        return FileResponse(UI_FILE, media_type="text/html; charset=utf-8")

    return app


def _ndjson(events):
    """스트리밍 이벤트를 JSON 한 줄씩 보낸다."""
    import json
    for event in events:
        yield json.dumps(event, ensure_ascii=False) + "\n"


app = create_app()
