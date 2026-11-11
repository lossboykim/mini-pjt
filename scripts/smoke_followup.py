"""대화 이어받기 스모크 테스트(평가 세트 밖). 사용: python scripts/smoke_followup.py

첫 질의의 thread_id로, 지시어("그 서비스")로 앞선 질의를 가리키는 두 번째 질의를 보내
(1) load_history 단계 output의 이전 메시지 수가 2 이상인지, (2) answer가 앞선 질의의 대상을 다루는지 확인한다.
실제 Bedrock 모델이 필요하다(.env의 자격 증명). 종료 코드 0이면 통과.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from src.server import create_app  # noqa: E402

TARGET = "payment-api"


def main() -> int:
    client = TestClient(create_app())
    first = client.post("/query", json={"question": f"{TARGET} 현재 상태 알려줘"}).json()
    second = client.post("/query", json={"question": "그 서비스의 과거 장애 사례도 알려줘",
                                         "thread_id": first["thread_id"]}).json()
    loaded = next(t for t in second["trace"] if t["step"] == "load_history")["output"]["loaded_messages"]
    checks = {
        "같은 thread_id": second["thread_id"] == first["thread_id"],
        "load_history 이전 메시지 수 >= 2": loaded >= 2,
        f"answer가 앞선 대상({TARGET})을 다룸": TARGET in second["answer"]
        or any(TARGET in str(t["input"]) for t in second["trace"] if t["step"] == "tool_call"),
    }
    for name, ok in checks.items():
        print(("통과" if ok else "실패"), "-", name)
    print("loaded_messages =", loaded)
    print("answer:", second["answer"][:300])
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
