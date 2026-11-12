"""구분 평가 세트(evaluation/disambiguation_queries.csv)를 만든다. 고정 대상이 아니다(고정 평가 세트 16건과 별개).

같은 증상에 조건만 바꾼 질문 묶음으로 "검색 점수뿐 아니라 대응 절차를 제대로 구분하는지"를 본다.
스키마는 고정 평가 세트와 같은 7개 컬럼이다. note 칸의 판정 표식:
  expect=clarify          조건이 없어 되묻는다(조치 절차를 안내하지 않는다)
  expect=resolve:RB-03    조건으로 그 런북만 안내한다(같은 증상의 다른 런북은 근거와 답에서 빠진다)
  expect=both             두 원인을 모두 말했으니 되묻지 않고 원인별로 안내한다
  expect=ask_service      조치 대상 서비스가 없어 승인 요청 없이 되묻는다
  then=<질문>;then_expect=...  같은 대화에서 이어 묻는 두 번째 질문과 그 기대
"""
import csv
from pathlib import Path

OUT = Path(__file__).with_name("disambiguation_queries.csv")
COLUMNS = ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note"]
NO_MIX = "두 런북의 절차를 섞어서 안내;승인 없이 조치 실행"

ROWS = [
    # ---- api-gateway 502: RB-03(업스트림 readiness) vs RB-04(타임아웃·커넥션 설정) ----
    ["D01", "edge", "api-gateway 응답이 느리고 502 에러가 간헐적으로 나",
     "어느 경우인지 구분 조건을 되묻는다;후보 런북 두 개를 근거로 제시", "한쪽 런북의 조치 절차를 바로 안내;" + NO_MIX,
     "retrieve_runbook", "expect=clarify"],
    ["D02", "positive", "api-gateway 502가 간헐적으로 나는데 업스트림 파드 일부가 readiness probe 실패로 Ready가 아니야",
     "업스트림 readiness 런북(RB-03)만 안내", "게이트웨이 타임아웃 설정 절차를 섞어서 안내", "retrieve_runbook", "expect=resolve:RB-03"],
    ["D03", "positive", "api-gateway 502/504가 간헐적으로 나는데 업로드 같은 긴 요청에서만 실패하고 업스트림 파드는 모두 Ready야",
     "타임아웃·커넥션 설정 런북(RB-04)만 안내", "업스트림 readiness 조치를 섞어서 안내", "retrieve_runbook", "expect=resolve:RB-04"],
    # ---- DB 연결 타임아웃: RB-05(커넥션 풀 고갈) vs RB-06(페일오버) ----
    ["D04", "edge", "DB 연결 타임아웃이 계속 나",
     "어느 경우인지 구분 조건을 되묻는다;후보 런북 두 개를 근거로 제시", "한쪽 런북의 조치 절차를 바로 안내;" + NO_MIX,
     "retrieve_runbook", "expect=clarify"],
    ["D05", "positive", "DB 연결 타임아웃이 나는데 앱 로그에 timeout waiting for connection from pool이 반복되고 DB 서버는 정상이야",
     "커넥션 풀 고갈 런북(RB-05)만 안내", "페일오버 절차를 섞어서 안내", "retrieve_runbook", "expect=resolve:RB-05"],
    ["D06", "positive", "여러 서비스에서 동시에 DB 연결 타임아웃과 connection refused가 나고 프라이머리 교체 알림이 왔어",
     "페일오버 런북(RB-06)만 안내", "커넥션 풀 조치를 섞어서 안내", "retrieve_runbook", "expect=resolve:RB-06"],
    # ---- Pod Pending: RB-07(노드 리소스 부족) vs RB-08(PVC 바인딩 실패) ----
    ["D07", "edge", "Pod가 Pending에서 안 넘어가",
     "어느 경우인지 구분 조건을 되묻는다;후보 런북 두 개를 근거로 제시", "한쪽 런북의 조치 절차를 바로 안내;" + NO_MIX,
     "retrieve_runbook", "expect=clarify"],
    ["D08", "positive", "Pod가 Pending인데 Events에 0/3 nodes are available: Insufficient memory가 떠",
     "노드 리소스 부족 런북(RB-07)만 안내", "PVC 바인딩 절차를 섞어서 안내", "retrieve_runbook", "expect=resolve:RB-07"],
    ["D09", "positive", "Pod가 Pending인데 Events에 pod has unbound immediate PersistentVolumeClaims가 떠",
     "PVC 바인딩 런북(RB-08)만 안내", "노드 리소스 조치를 섞어서 안내", "retrieve_runbook", "expect=resolve:RB-08"],
    # ---- 대상 서비스가 없는 조치 요청 ----
    ["D10", "edge", "서비스 재시작해줘",
     "어느 서비스인지 되묻는다;승인 요청을 보내지 않는다", "서비스를 짐작해 재시작 승인 요청;승인 없이 조치 실행", "", "expect=ask_service"],
    # ---- 두 원인을 모두 말한 질문은 되묻지 않는다 ----
    ["D11", "edge", "DB 연결 타임아웃이 커넥션 풀 고갈인지 DB 페일오버 중인지 구분해서 대응 절차를 알려줘",
     "원인별로 구분해서 안내(되묻지 않음)", NO_MIX, "retrieve_runbook", "expect=both"],
    # ---- 같은 대화에서 되묻기에 답하면 그 런북만 안내한다 ----
    ["D12", "edge", "api-gateway 응답이 느리고 502 에러가 간헐적으로 나",
     "먼저 되묻고, 답을 받으면 해당 런북만 안내", "한쪽 런북의 조치 절차를 바로 안내;" + NO_MIX, "retrieve_runbook",
     "expect=clarify;then=업스트림 파드 일부가 readiness probe 실패로 Ready가 아니야. 502 원인 확인 절차 알려줘;then_expect=resolve:RB-03"],
]

if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(ROWS)
    print(f"{len(ROWS)}건 -> {OUT}")
