# RB-03 api-gateway 502 간헐 발생: 업스트림 파드 readiness 실패

- 대상 서비스: api-gateway와 그 뒤의 업스트림 서비스
- 증상 키워드: 502 에러, 응답 지연, 간헐적 오류, 게이트웨이

## 증상
- api-gateway 응답이 느리고 502 Bad Gateway가 간헐적으로 난다.
- 업스트림 Deployment의 Ready 파드 수가 요청 파드 수보다 적다.

## 진단 절차
1. `kubectl get endpoints <upstream-service>`로 엔드포인트에 Ready 파드가 몇 개 있는지 본다.
2. `kubectl describe pod <upstream-pod>`의 Events에서 Readiness probe failed 메시지를 찾는다.
3. 업스트림 파드 로그에서 기동 지연이나 헬스체크 엔드포인트 오류를 확인한다.

## 조치 절차
1. readiness probe의 `initialDelaySeconds`와 `timeoutSeconds`가 앱 기동 시간에 맞는지 점검하고 조정을 요청한다.
2. 비정상 파드가 한두 개이면 해당 파드만 교체되도록 담당자에게 요청한다. 서비스 전체 재시작은 하지 않는다.
3. 조치 후 `kubectl get endpoints`에서 Ready 파드 수가 정상으로 돌아왔는지 확인한다.

## 주의
- 이 런북은 업스트림 파드 상태가 원인일 때만 쓴다. 게이트웨이 타임아웃 설정 문제는 RB-04를 본다.
