"""제출 가능 상태 검사: README 절·핵심 코드 위치·추가 패턴 목록, requirements, Dockerfile, 커밋 금지 파일."""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

# import 이름 -> requirements.txt 패키지 이름
IMPORT_TO_PKG = {
    "langchain_core": "langchain-core", "langchain_aws": "langchain-aws", "langgraph": "langgraph",
    "pydantic": "pydantic", "fastapi": "fastapi", "rank_bm25": "rank-bm25", "numpy": "numpy",
    "dotenv": "python-dotenv", "boto3": "boto3", "uvicorn": "uvicorn", "langsmith": "langsmith", "httpx": "httpx",
}


def test_readme_sections():
    for title in ["무엇을 푸나", "활용한 패턴", "아키텍처", "실행 방법", "RAGAS 평가 결과",
                  "인-아웃 세트 통과율", "트라이앤에러 회고", "핵심 코드 위치"]:
        assert re.search(rf"^## .*{re.escape(title)}", README, re.M), title
    for d in ["1차", "2차", "개선"]:
        assert d in README


def test_core_code_locations_point_to_real_lines():
    rows = re.findall(r"`([\w/\.]+\.py):(\d+)`\s*\|\s*`(\w+)`", README)
    assert len(rows) >= 10
    for path, line, symbol in rows:
        f = ROOT / path
        assert f.exists(), path
        lines = f.read_text(encoding="utf-8").splitlines()
        assert 1 <= int(line) <= len(lines), (path, line)
        assert symbol in lines[int(line) - 1], (path, line, symbol)


def test_extra_pattern_list_matches_files():
    section = README.split("## 2.")[1].split("## 3.")[0]
    mods = {"middleware": "미들웨어", "mcp_server": "MCP", "supervisor": "Supervisor"}
    exists = {m: (ROOT / "src" / f"{m}.py").exists() for m in mods}
    order = list(mods)
    for i, m in enumerate(order):
        if exists[m]:  # 뒤 순서 모듈이 있으면 앞 순서 모듈도 있어야 한다
            assert all(exists[p] for p in order[:i]), m
        line = next(l for l in section.splitlines() if f"src/{m}.py" in l)
        if exists[m]:
            assert "미구현" not in line, line
        else:
            assert "미구현" in line or "제외" in line, line


def _third_party_imports():
    found = set()
    std = set(sys.stdlib_module_names)
    for base in ["src", "evaluation", "scripts"]:
        for p in (ROOT / base).glob("*.py"):
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module]
                for name in names:
                    top = name.split(".")[0]
                    if top not in std and top not in {"src", "rubric", "__future__"}:
                        found.add(top)
    return found


def test_requirements_cover_imports():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    req_lines = [l.strip().lower() for l in reqs.splitlines() if l.strip() and not l.startswith("#")]
    assert "langgraph-checkpoint-sqlite" in req_lines  # 주석 줄에 붙어 빠지지 않았는지
    eval_reqs = (ROOT / "evaluation/requirements-eval.txt").read_text(encoding="utf-8").lower()
    for top in _third_party_imports():
        if top == "ragas":
            assert "ragas" in eval_reqs
            continue
        assert top in IMPORT_TO_PKG, f"알 수 없는 패키지 {top}"
        pkg = IMPORT_TO_PKG[top]
        assert any(l == pkg or l.startswith(pkg + "=") or l.startswith(pkg + ">") for l in req_lines), pkg
    for pkg in ["fastapi", "uvicorn", "langsmith", "pytest"]:
        assert pkg in req_lines


def test_dockerfile_and_unverified_notice():
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM " in d and "uvicorn" in d
    assert "Docker가 없어" in README and "검증" in README


def test_no_secrets_or_index_committed_and_db_committed():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not [t for t in tracked if Path(t).name == ".env" or t.startswith(("index/", "state/"))]
    assert not [t for t in tracked if t.endswith((".faiss", ".npy", ".pkl"))]
    assert "data/incidents.db" in tracked
    ign = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ign and "index/" in ign
