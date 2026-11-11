# RB-02 OOMKilled: 메모리 한도 초과로 파드가 반복 재시작

- 대상 서비스: order-worker, payment-api 등 메모리를 많이 쓰는 워크로드
- 증상 키워드: OOMKilled, 파드 재시작 반복, 메모리 부족으로 컨테이너 종료

## 증상
- `kubectl describe pod <pod>`의 Last State가 Terminated, Reason이 OOMKilled, Exit Code 137이다.
- 메모리 사용량이 limit에 닿은 직후 컨테이너가 재시작된다.

## 진단 절차
1. `kubectl top pod <pod> --containers`로 현재 메모리 사용량과 limit을 비교한다.
2. 최근 배포 또는 트래픽 증가 시점과 재시작 시작 시점이 겹치는지 확인한다.
3. 메모리가 시간에 따라 계속 증가하는지(누수) 대시보드의 메모리 그래프로 본다.

## 조치 절차
1. 누적된 메모리를 비우기 위해 해당 워크로드를 재시작한다. 재시작은 위험 조치이므로 반드시 사람 승인을 받은 뒤에만 실행한다.
2. 재시작 후에도 반복되면 Deployment의 `resources.limits.memory`를 한 단계(예: 512Mi에서 1Gi) 올리는 변경을 요청한다.
3. 재시작 후 5분간 `kubectl get pods`로 RESTARTS가 늘지 않는지 확인한다.

## 주의
- 메모리 limit은 담당 팀 승인 없이 두 단계 이상 올리지 않는다.
