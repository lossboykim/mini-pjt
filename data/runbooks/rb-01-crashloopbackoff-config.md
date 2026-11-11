# RB-01 CrashLoopBackOff: 설정·환경변수 오류로 파드가 반복 재시작

- 대상 서비스: web-frontend, user-service 등 일반 웹 워크로드
- 증상 키워드: CrashLoopBackOff, 파드 재시작 반복, 컨테이너 시작 직후 종료

## 증상
- `kubectl get pods`에서 STATUS가 CrashLoopBackOff, RESTARTS 값이 계속 증가한다.
- 컨테이너가 시작 직후(수 초 내) 종료 코드 1로 죽는다. OOMKilled(종료 코드 137)가 아니다.

## 진단 절차
1. `kubectl describe pod <pod>`로 Last State의 Exit Code와 Reason을 확인한다. Exit Code 1이면 이 런북을 따른다.
2. `kubectl logs <pod> --previous`로 직전 컨테이너 로그에서 설정 누락, 환경변수 오류, 연결 실패 메시지를 찾는다.
3. `kubectl get configmap <name> -o yaml`과 `kubectl get deploy <name> -o yaml`의 env 항목이 최근 배포와 일치하는지 비교한다.

## 조치 절차
1. 잘못된 ConfigMap 또는 환경변수를 직전 정상 값으로 되돌린다.
2. `kubectl rollout undo deployment/<name>`으로 직전 리비전으로 롤백한다.
3. 롤백 후 `kubectl rollout status deployment/<name>`으로 파드가 Running이 되는지 확인한다.

## 주의
- 원인을 모른 채 파드를 반복 삭제하지 않는다. 로그 확인이 먼저다.
