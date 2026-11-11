# RB-07 Pod Pending: 노드 CPU·메모리 리소스 부족

- 대상 서비스: 신규 배포 또는 스케일아웃되는 모든 파드
- 증상 키워드: Pod Pending, 스케줄링 실패, 노드 메모리 부족, Insufficient memory

## 증상
- 파드가 Pending에서 넘어가지 않는다.
- `kubectl describe pod`의 Events에 `0/N nodes are available: Insufficient memory`(또는 cpu)가 있다.

## 진단 절차
1. `kubectl describe pod <pod>`의 Events에서 Insufficient memory/cpu 메시지를 확인한다.
2. `kubectl describe nodes`의 Allocated resources에서 노드별 requests 합계를 본다.
3. 파드의 `resources.requests`가 과하게 큰지 확인한다.

## 조치 절차
1. 파드의 requests가 과하면 실제 사용량에 맞춰 낮추는 변경을 요청한다.
2. 노드가 부족하면 클러스터 오토스케일러의 노드 증설 상태를 확인하고, 증설이 안 되면 인프라 담당자에게 노드 추가를 요청한다.
3. 우선순위가 낮은 워크로드의 스케일다운은 담당 팀과 협의한 뒤에만 진행한다.

## 주의
- PVC 바인딩 문제로 인한 Pending은 RB-08을 본다.
