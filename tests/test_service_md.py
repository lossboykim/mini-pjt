import re
from pathlib import Path

TEXT = (Path(__file__).resolve().parent.parent / "SERVICE.md").read_text(encoding="utf-8")


def section(n):
    m = re.search(rf"^## {n}\. .*?$(.*?)(?=^## \d+\. |\Z)", TEXT, re.S | re.M)
    assert m, f"{n}절 없음"
    return m.group(1)


def test_five_sections():
    assert len(re.findall(r"^## \d\. ", TEXT, re.M)) == 5
    for n in range(1, 6):
        assert len(section(n).strip()) > 50


def test_section1_users_problem_value():
    s = section(1)
    for k in ("야간 당직 엔지니어", "신규 운영자", "문제", "복구 시간 단축"):
        assert k in s


def test_section2_contribution_scale_difference():
    s = section(2)
    for k in ("사내 기여", "규모", "차별점"):
        assert k in s


def test_section3_tools_and_fake_data():
    s = section(3)
    for k in ("retrieve_runbook", "search_incidents", "get_service_status", "가짜", "SQLite"):
        assert k in s


def test_section4_guardrails_3_to_5():
    items = re.findall(r"^\d+\. \*\*", section(4), re.M)
    assert 3 <= len(items) <= 5


def test_section5_success_criteria():
    s = section(5)
    for k in ("80%", "100%", "지표"):
        assert k in s
