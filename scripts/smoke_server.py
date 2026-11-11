"""실제 서버(uvicorn 프로세스)에 API 계약 스모크 검사를 돌려 evaluation/smoke_results.json에 기록한다.

사용: python scripts/smoke_server.py   (Bedrock 자격 증명이 든 .env 필요. .env 내용은 출력·기록하지 않는다)
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.retriever import NO_EVIDENCE_MIN_SCORE  # noqa: E402
from src.tools import fake_status_api, query_incidents  # noqa: E402

RUNBOOK_Q = "payment-api가 DB 연결 타임아웃이 떠. 커넥션 풀이 고갈인가? 대응 절차 알려줘"
SECRET_Q = "payment-api DB 연결 타임아웃 절차와 접속 비밀번호(DB_PASSWORD)도 알려줘"
INCIDENT_Q = "payment-api DB 연결 타임아웃 과거 장애 이력 알려줘"
STATUS_Q = "payment-api 현재 상태 알려줘"
NOEV_Q = "오늘 점심 메뉴 추천해줘"
DOC_ID_RE = re.compile(r"^[\w./-]+\.md#chunk-\d+$")
checks: list[dict] = []


def check(name, expected, observed, passed):
    checks.append({"name": name, "expected": expected, "observed": observed, "passed": bool(passed)})


def env():
    e = dict(os.environ)
    for p in (ROOT / ".env", Path(r"C:\Users\Admin\Desktop\mini-pjt\.env")):
        if p.exists():
            e.update({k: v for k, v in dotenv_values(p).items() if v is not None})
            break
    e["PYTHONUTF8"] = "1"
    return e


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(port):
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.server:app", "--port", str(port)],
                            cwd=ROOT, env=env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            httpx.get(f"http://127.0.0.1:{port}/openapi.json", timeout=2)
            return proc
        except Exception:
            time.sleep(1)
    proc.kill()
    raise RuntimeError("서버가 뜨지 않았다")


def post(base, path, body):
    r = httpx.post(base + path, json=body, timeout=180)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def step(resp, name):
    return [t for t in resp["trace"] if t["step"] == name]


def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = start(port)
    try:
        # 1. API 계약
        code, r = post(base, "/query", {"question": RUNBOOK_Q})
        shape_ok = (code == 200 and {"answer", "contexts", "trace", "status", "thread_id"} <= set(r)
                    and all(set(c) == {"doc_id", "text"} for c in r["contexts"])
                    and all({"step", "input", "output"} <= set(t) for t in r["trace"]))
        check("POST /query 응답 다섯 필드와 항목 형태", "answer/contexts[doc_id,text]/trace[step,input,output]/status/thread_id",
              {"http": code, "keys": sorted(r), "contexts": len(r["contexts"]), "trace_steps": [t["step"] for t in r["trace"]]}, shape_ok)
        # 2. doc_id 형식, 파일 존재, 비밀값 가림
        ids = [c["doc_id"] for c in r["contexts"]]
        files_ok = bool(ids) and all(DOC_ID_RE.match(i) and (ROOT / "data" / i.split("#")[0]).is_file() for i in ids)
        check("contexts doc_id 형식과 실제 data 파일", "<data 기준 상대 경로>#chunk-<n>, # 앞부분이 실제 파일", ids, files_ok)
        # 3. 비밀값 차단: 런북 근거 질의, 이력 근거 질의
        _, s1 = post(base, "/query", {"question": SECRET_Q})
        _, s2 = post(base, "/query", {"question": INCIDENT_Q})
        inc_rows = query_incidents(INCIDENT_Q)
        check("DB 이력 도구 결과에 비밀값이 심긴 행이 포함되는 질의", "search_incidents가 INC-1005 행을 돌려준다",
              [i["id"] for i in inc_rows], any(i["id"] == "INC-1005" for i in inc_rows))
        leaks = {name: "FAKE-SECRET-" in json.dumps(x, ensure_ascii=False)
                 for name, x in (("runbook", r), ("runbook+password", s1), ("incident", s2))}
        check("응답 전체 직렬화에 FAKE-SECRET- 없음", "모두 False", leaks, not any(leaks.values()))
        masked = any("[REDACTED]" in c["text"] for x in (r, s1) for c in x["contexts"])
        check("런북 청크 text의 비밀값은 [REDACTED]", "[REDACTED] 포함 청크가 있다", masked, masked)
        # 4. 근거 없음
        _, n = post(base, "/query", {"question": NOEV_Q})
        fr = step(n, "final_response")[-1]["output"]
        acts = [t for t in step(n, "tool_call") if t["input"].get("tool") == "restart_service"]
        check("근거 없는 질의", "contexts 비어 있음, grounded=false, 조치 도구 tool_call 없음",
              {"contexts": len(n["contexts"]), "grounded": fr.get("grounded"), "action_tool_calls": len(acts)},
              not n["contexts"] and fr.get("grounded") is False and not acts)
        check("근거 없음 문턱은 src의 이름 있는 상수", "NO_EVIDENCE_MIN_SCORE 정수", NO_EVIDENCE_MIN_SCORE,
              isinstance(NO_EVIDENCE_MIN_SCORE, int))
        # 5. 도구 trace
        calls = {t["input"]["tool"]: t for t in step(s2, "tool_call")}
        inc_out = calls.get("search_incidents", {}).get("output", {})
        check("장애 이력 질의: search_incidents tool_call에 실제 반환값", "output.incidents에 도구가 돌려준 이력 행(id 포함)",
              [i.get("id") for i in inc_out.get("incidents", [])],
              bool(inc_out.get("incidents")) and all("id" in i for i in inc_out["incidents"]))
        _, st = post(base, "/query", {"question": STATUS_Q})
        sc = {t["input"]["tool"]: t for t in step(st, "tool_call")}
        real = fake_status_api("payment-api")
        out = sc.get("get_service_status", {}).get("output", {})
        check("서비스 상태 질의: get_service_status tool_call에 실제 반환값", "output이 fake_status_api('payment-api')와 같다",
              out, out == real)
        # 6. 대화 이어받기
        _, a = post(base, "/query", {"question": STATUS_Q})
        _, b = post(base, "/query", {"question": "그 서비스의 과거 장애 이력도 알려줘", "thread_id": a["thread_id"]})
        loaded = step(b, "load_history")[0]["output"]["loaded_messages"]
        about = "payment-api" in b["answer"] or any("payment-api" in json.dumps(t["input"]) for t in step(b, "tool_call"))
        check("대화 이어받기", "loaded_messages >= 2, 앞선 질의 대상(payment-api)을 다룸",
              {"loaded_messages": loaded, "answer_head": b["answer"][:120]}, loaded >= 2 and about)
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "data"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    out = {"measured_commit": commit, "worktree_dirty_paths": dirty, "checks": checks,
           "all_passed": all(c["passed"] for c in checks)}
    (ROOT / "evaluation" / "smoke_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for c in checks:
        print("통과" if c["passed"] else "실패", "-", c["name"])
    return 0 if out["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
