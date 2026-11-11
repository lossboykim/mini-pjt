"""실행 전 Bedrock 모델 한도 점검. 사용: python scripts/check_bedrock.py

역할(main: 답을 쓰는 모델, router: 감독자의 경로 선택)마다 후보 모델에 출력 1토큰짜리 요청을 보내
지금 쓸 수 있는지(ok), 일일 한도를 다 썼는지(daily_quota), 분당 제한에 걸렸는지(busy), 쓸 수 없는지 보여 준다.
에이전트는 실행할 때 같은 확인을 스스로 하고, 한도에 걸린 모델은 목록의 다음 후보로 바꿔 쓴다.
역할마다 쓸 수 있는 후보가 하나도 없으면 종료 코드 1로 끝난다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from src.llm import ROLE_CANDIDATES, probe  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    ok_roles = 0
    for role, candidates in ROLE_CANDIDATES.items():
        print(f"[{role}]")
        chosen = None
        for i, c in enumerate(candidates):
            status = probe(c)
            mark = "설정값" if i == 0 else f"대체 {i}"
            print(f"  {mark:6} {status.split(':')[0]:12} {c.label}" + (f"  ({status})" if ":" in status else ""))
            if chosen is None and status in ("ok", "busy"):
                chosen = c
        print(f"  -> 실행 시 사용: {chosen.label if chosen else '없음'}\n")
        ok_roles += chosen is not None
    return 0 if ok_roles == len(ROLE_CANDIDATES) else 1


if __name__ == "__main__":
    sys.exit(main())
