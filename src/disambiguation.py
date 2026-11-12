"""되묻기 게이트: 비슷한 문서가 검색됐다는 것만으로 조치를 고르지 않는다.

1) 같은 증상·다른 조치 런북 묶음(data/runbook_groups.json): 묶음의 런북이 검색됐는데 사용자 질문과 확인된 서비스 상태에
   어느 쪽 원인인지 가리키는 단서가 없으면, 한쪽을 고르지 않고 구분 조건을 되묻는다. 단서가 한쪽만 있으면 그 런북만
   안내하고, 양쪽 다 있으면(사용자가 두 원인을 비교해 달라고 한 경우) 원인별로 나눠 안내한다.
2) 조치 대상 서비스: 조치(재시작) 요청인데 대상 서비스가 사용자 말에 없으면 승인 요청을 보내지 않고 되묻는다.

단서 비교는 소문자 부분 문자열이다. 되묻기 답은 런북 원문(증상, 진단 절차 첫 단계)만 옮겨 코드가 만든다: 모델이 없는
절차를 지어낼 수 없다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GROUPS_FILE = ROOT / "data" / "runbook_groups.json"
REDACTED = "[REDACTED]"

AMBIGUOUS, RESOLVED, BOTH = "ambiguous", "resolved", "both"
_RB_RE = re.compile(r"rb-(\d+)", re.IGNORECASE)
CIRCLED = "①②③④⑤⑥⑦⑧⑨"


@dataclass(frozen=True)
class Group:
    id: str
    symptom: str
    symptom_cues: tuple[str, ...]
    members: dict[str, tuple[str, ...]]  # 런북 번호("RB-03") -> 원인 단서
    incident_cues: tuple[str, ...] = ()  # 비슷한 과거 장애를 고르는 단서(비면 symptom_cues)


@dataclass
class Decision:
    group: str
    kind: str                    # AMBIGUOUS | RESOLVED | BOTH
    members: list[str]           # 묶음의 모든 런북 번호
    resolved: list[str] = field(default_factory=list)   # 단서로 정해진 런북 번호(RESOLVED, BOTH)
    matched: dict[str, list[str]] = field(default_factory=dict)  # 런북 번호 -> 맞은 단서


def load_groups(path: Path | None = None) -> list[Group]:
    data = json.loads(Path(path or GROUPS_FILE).read_text(encoding="utf-8"))
    return [Group(g["id"], g["symptom"], tuple(c.lower() for c in g["symptom_cues"]),
                  {rid: tuple(c.lower() for c in m["cues"]) for rid, m in g["members"].items()},
                  tuple(c.lower() for c in g.get("incident_cues", g["symptom_cues"])))
            for g in data["groups"]]


def runbook_id(doc_id: str) -> str | None:
    """'runbooks/rb-03-...md#chunk-0' -> 'RB-03'. 런북 조각이 아니면 None."""
    if not doc_id.startswith("runbooks/"):
        return None
    m = _RB_RE.search(doc_id)
    return f"RB-{m.group(1)}" if m else None


def _hits(text: str, cues) -> list[str]:
    low = text.lower()
    return [c for c in cues if c in low]


def decide(groups: list[Group], retrieved_doc_ids: list[str], user_text: str, status_text: str = "") -> list[Decision]:
    """검색된 런북과 질문·서비스 상태 단서로 묶음마다 결정한다(해당 없는 묶음은 결과에 없다).

    묶음이 쓰이는 조건: 질문에 묶음의 공통 증상이 있고, 묶음의 런북이 하나라도 검색됐다.
    단서는 사용자 말과 사용자가 말한 서비스의 현재 상태에서만 찾는다(과거 장애 이력은 지금 원인을 정하지 못한다).
    """
    retrieved = {runbook_id(d) for d in retrieved_doc_ids} - {None}
    evidence = f"{user_text}\n{status_text}"
    out: list[Decision] = []
    for g in groups:
        if not retrieved & set(g.members) or not _hits(user_text, g.symptom_cues):
            continue
        matched = {rid: h for rid, cues in g.members.items() if (h := _hits(evidence, cues))}
        kind = AMBIGUOUS if not matched else RESOLVED if len(matched) == 1 else BOTH
        out.append(Decision(g.id, kind, list(g.members), list(matched), matched))
    return out


# ---------- 런북 원문에서 되묻기 답 만들기 ----------
def _section(text: str, title: str) -> list[str]:
    """'## <title>' 절의 목록 항목(줄 앞의 '- ' 또는 '1. ')을 원문 그대로 돌려준다."""
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return []
    return [re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", ln).strip() for ln in m.group(1).splitlines()
            if re.match(r"^\s*(?:[-*]|\d+\.)\s+", ln)]


def runbook_text(docs, rid: str) -> tuple[str, list]:
    """검색기가 가진 조각(docs)에서 런북 하나의 전문과 조각 목록을 돌려준다."""
    chunks = [d for d in docs if runbook_id(d.doc_id) == rid]
    chunks.sort(key=lambda d: int(d.doc_id.rsplit("-", 1)[-1]))
    return "".join(d.text for d in chunks), chunks


def _title(text: str) -> str:
    m = re.search(r"^# (.+)$", text, flags=re.M)
    return m.group(1).strip() if m else ""


def _contexts_for(chunks: list, quoted: list[str]) -> list[dict]:
    """답에 옮긴 줄이 들어 있는 조각만 근거로 남긴다. 비밀값을 가린 줄은 뺀다."""
    out, seen = [], set()
    for line in quoted:
        for c in chunks:
            if line in c.text and c.doc_id not in seen:
                seen.add(c.doc_id)
                out.append({"doc_id": c.doc_id,
                            "text": "\n".join(ln for ln in c.text.splitlines() if REDACTED not in ln)})
    return out


def _fields(text: str) -> dict[str, str]:
    """'라벨: 값' 줄을 사전으로. 장애 이력·서비스 상태 근거 본문용."""
    out = {}
    for ln in text.splitlines():
        label, sep, value = ln.partition(": ")
        if sep and label.strip() and not label.startswith("["):
            out.setdefault(label.strip(), value.strip())
    return out


def ops_reference(ops_contexts: list[dict], user_text: str, symptom_cues=(), member_cues=None) -> tuple[list[str], list[dict]]:
    """이미 조회한 운영 데이터를 되묻는 답의 참고로 만든다: 사용자가 말한 서비스의 현재 상태, 비슷한 과거 장애(증상과 원인).

    과거 장애의 조치는 쓰지 않는다: 지금 원인이 정해지기 전에 과거에 한 조치를 따라 고르게 하지 않기 위해서다.
    symptom_cues가 있으면 그 증상이 나온 과거 장애만 고른다(검색이 느슨하게 돌려준 관련 없는 장애를 빼기 위해서다).
    member_cues(런북 번호 -> 원인 단서)가 있으면, 과거 장애의 증상·원인이 후보 중 한 곳의 단서에만 맞을 때 "과거에는 ②번 경우였다"를
    붙인다. 과거 사례가 어느 후보 원인이었는지 알려 구분을 돕는 것이고, 지금의 원인을 정하는 것은 아니다.
    """
    lines: list[str] = []
    used: list[dict] = []
    for c in ops_contexts:
        if c["doc_id"].startswith("status/") and c["doc_id"] != "status/all":
            f = _fields(c["text"])
            service = c["doc_id"].split("/", 1)[1]
            if service.lower() in user_text.lower():
                bits = [f"{k} {f[k]}" for k in ("상태", "에러율(%)", "비고") if f.get(k)]
                lines.append(f"- 현재 상태 · {service}: " + ", ".join(bits))
                used.append(c)
    def matches(c: dict) -> bool:  # 서비스 이름 줄이 아니라 증상·원인 줄에서 찾는다(api-gateway 장애는 모두 'gateway'가 있다)
        f = _fields(c["text"])
        body = f"{f.get('증상', '')} {f.get('원인', '')}".lower()
        return not symptom_cues or any(cue in body for cue in symptom_cues)

    incidents = [c for c in ops_contexts if c["doc_id"].startswith("incidents/") and matches(c)][:3]
    for c in incidents:
        f = _fields(c["text"])
        what = " / ".join(f"{k} {f[k]}" for k in ("증상", "원인") if f.get(k))
        link = ""
        if member_cues:
            body = f"{f.get('증상', '')} {f.get('원인', '')}".lower()
            hit = [(n, rid) for n, (rid, cues) in enumerate(member_cues.items()) if any(cue in body for cue in cues)]
            if len(hit) == 1:  # 한 후보의 단서에만 맞을 때만 연결한다(둘 다 맞거나 둘 다 아니면 연결하지 않는다)
                link = f" → 과거에는 {CIRCLED[hit[0][0]]} {hit[0][1]} 쪽이었다"
        lines.append(f"- 비슷한 과거 장애 · {c['doc_id'].split('/', 1)[1]}({f.get('발생일', '')}): {what}{link}")
        used.append(c)
    return lines, used


def build_clarification(decision: Decision, docs, user_names_service: bool, ops_contexts=None,
                        user_text: str = "", symptom_cues=(), member_cues=None) -> tuple[str, list[dict], list[str]]:
    """구분 조건을 되묻는 답, 근거(contexts), 인용한 doc_id를 만든다. 런북의 조치 절차는 쓰지 않는다."""
    ref, used = ops_reference(ops_contexts or [], user_text, symptom_cues, member_cues)
    intro = ("증상은 같지만 원인에 따라 조치가 다른 런북이 있어, 지금 말씀하신 내용만으로는 하나를 고를 수 없습니다. "
             "어느 쪽인지 먼저 확인해 주세요. 조치 절차는 원인이 정해진 뒤에 안내합니다.")
    links = [(re.search(r"INC-\d+", ln).group(0), re.search(r"→ 과거에는 (\S)", ln).group(1)) for ln in ref if "→ 과거에는" in ln]
    if links:  # 과거 사례를 답 본문에서 직접 말한다: 같은 증상이 과거에 어느 원인이었는지가 구분의 단서가 된다
        labels = list(dict.fromkeys(lab for _, lab in links))
        which = f"{labels[0]}이었습니다" if len(labels) == 1 else f"{', '.join(labels)} 경우가 모두 있었습니다"
        intro += f" 이 증상은 과거에도 있었고({', '.join(i for i, _ in links)}), 그때 원인은 {which}."
    lines = [intro, "", "**어느 경우인가요?**"]
    contexts: list[dict] = []
    for n, rid in enumerate(decision.members, 1):
        text, chunks = runbook_text(docs, rid)
        symptoms = _section(text, "증상")
        checks = _section(text, "진단 절차")
        head = _title(text) or rid
        lines.append(f"**{CIRCLED[n - 1]} {head}**")
        quoted = []
        if symptoms:
            lines.append("- 이런 경우: " + " ".join(symptoms))
            quoted += symptoms
        if checks:
            lines.append(f"- 먼저 확인: {checks[0]}")
            quoted.append(checks[0])
        for c in _contexts_for(chunks, quoted):
            if all(x["doc_id"] != c["doc_id"] for x in contexts):
                contexts.append(c)
    if ref:
        lines += ["", "**비슷한 과거 장애와 현재 상태**", *ref]
        contexts += used
    lines += ["", "해당하는 쪽의 확인 결과(또는 화면에 나온 오류 메시지)를 알려 주시면 그 런북의 절차만 안내하겠습니다."]
    if not user_names_service:
        lines.append("대상 서비스 이름도 함께 알려 주세요.")
    return "\n".join(lines), contexts, [c["doc_id"] for c in contexts]


_SERVICE_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")


def unknown_services(question: str, known: list[str]) -> list[str]:
    """질문에 서비스 이름처럼 쓰인 말(a-b 꼴) 중 등록되지 않은 것."""
    low = {k.lower() for k in known}
    return list(dict.fromkeys(t for t in _SERVICE_TOKEN_RE.findall(question.lower()) if t not in low))


def missing_target_answer(known_services: list[str], unknown: list[str] | None = None) -> str:
    names = ", ".join(known_services) if known_services else "대상 서비스"
    head = (f"{', '.join(unknown)}은(는) 등록된 서비스가 아닙니다. " if unknown else "") + "어느 서비스를 재시작할지 알려 주세요."
    return (f"{head} 대상 서비스가 정해지기 전에는 승인 요청을 보내지 않습니다. (현재 등록된 서비스: {names}) "
            "재시작은 위험 조치라 이 화면에서 승인한 뒤에만 실행됩니다.")


def answers_pending(pend: dict, reply: str, groups: list[Group], known_services: list[str]) -> bool:
    """이번 말이 앞 턴의 되묻기에 대한 답인가. 새 질문을 앞 질문과 섞지 않도록 짧고 단서가 있는 말만 답으로 본다."""
    if len(reply) > 300:
        return False
    low = reply.lower()
    if pend.get("kind") == "missing_target":
        return any(s.lower() in low for s in known_services)
    group = next((g for g in groups if g.id == pend.get("group")), None)
    return bool(group) and any(c in low for cues in group.members.values() for c in cues)


def target_named(target: str, user_text: str) -> bool:
    """조치 대상 서비스 이름이 사용자 말에 있는가."""
    t = (target or "").strip().lower()
    return bool(t) and t != "all" and t in user_text.lower()
