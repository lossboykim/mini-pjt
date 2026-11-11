"""가짜 장애 이력 SQLite DB(data/incidents.db)를 만든다. 결과 파일은 커밋한다."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "incidents.db"

ROWS = [
    ("INC-1001", "web-frontend", "CrashLoopBackOff, 배포 직후 파드 재시작 반복", "ConfigMap의 API_BASE_URL 누락", "ConfigMap 복구 후 rollout undo", "high", "2026-08-03", "RB-01 절차로 20분 내 복구"),
    ("INC-1002", "order-worker", "OOMKilled로 파드 반복 재시작", "배치 크기 증가로 메모리 limit 초과", "재시작 후 memory limit 512Mi에서 1Gi로 상향", "high", "2026-08-19", "RB-02 절차 적용, 재시작은 승인 후 실행"),
    ("INC-1003", "api-gateway", "502 에러 간헐 발생, 응답 지연", "업스트림 payment-api readiness probe 지연", "probe initialDelaySeconds 상향", "medium", "2026-09-02", "RB-03 절차로 해결"),
    ("INC-1004", "api-gateway", "504 타임아웃, 긴 요청에서만 실패", "proxy_read_timeout 30초가 리포트 생성 시간보다 짧음", "타임아웃 120초로 상향", "medium", "2026-09-11", "RB-04 절차로 해결"),
    ("INC-1005", "payment-api", "DB 연결 타임아웃, 커넥션 풀 고갈", "트랜잭션 미종료 코드로 커넥션 반환 안 됨", "idle 세션 정리 후 풀 크기 상향, 코드 수정", "high", "2026-09-17", "점검 계정 DB_PASSWORD=FAKE-SECRET-incident-db-9a3d 를 임시 사용했음(회수 완료)"),
    ("INC-1006", "payment-api", "에러율 5xx 급증, 배포 직후", "신규 버전의 외부 결제사 API 호출 버그", "rollout undo로 롤백", "high", "2026-09-25", "RB-10 절차로 10분 내 복구"),
    ("INC-1007", "user-service", "DB 연결 타임아웃, 전 서비스 동시 발생", "DB 프라이머리 페일오버", "페일오버 완료 대기 후 일부 앱 롤링 재시작", "high", "2026-10-02", "RB-06 절차, 풀 크기 변경 없이 대기"),
    ("INC-1008", "inventory-api", "Pod Pending, Insufficient memory", "requests 과다 설정", "requests를 실제 사용량에 맞춰 하향", "medium", "2026-10-08", "RB-07 절차로 해결"),
    ("INC-1009", "order-worker", "Pod Pending, PVC unbound", "StorageClass 이름 오타", "PVC 매니페스트의 StorageClass 수정", "medium", "2026-10-12", "RB-08 절차로 해결"),
    ("INC-1010", "order-worker", "PVC 디스크 사용량 90% 경고", "로그 로테이션 미설정", "오래된 로그 정리 후 로테이션 설정", "low", "2026-10-15", "RB-09 절차로 해결"),
    ("INC-1011", "web-frontend", "ImagePullBackOff", "이미지 태그 오타", "올바른 태그로 재배포", "low", "2026-10-20", "RB-11 절차로 해결"),
    ("INC-1012", "api-gateway", "TLS 인증서 만료 경고", "cert-manager 갱신 실패", "보안 담당자가 발급 설정 수정", "medium", "2026-10-27", "RB-12 절차로 해결"),
    ("INC-1013", "payment-api", "에러율 상승, 지연 증가", "외부 결제사 응답 지연", "결제사 복구 후 자동 정상화", "medium", "2026-11-01", "배포와 무관함을 RB-10 진단으로 확인"),
]


def build(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE incidents (id TEXT PRIMARY KEY, service TEXT, symptom TEXT, root_cause TEXT, "
        "resolution TEXT, severity TEXT, occurred_at TEXT, notes TEXT)"
    )
    con.executemany("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?)", ROWS)
    con.commit()
    con.close()


if __name__ == "__main__":
    build()
    print(f"wrote {DB_PATH} ({len(ROWS)} rows)")
