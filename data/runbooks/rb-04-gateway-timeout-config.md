# RB-04 api-gateway 502/504 응답 지연: 게이트웨이 타임아웃·커넥션 설정 문제

- 대상 서비스: api-gateway(인그레스 포함)
- 증상 키워드: 502 에러, 504, 응답 지연, 간헐적 오류, 게이트웨이

## 증상
- api-gateway 응답이 느리고 502 또는 504가 난다. 업스트림 파드는 모두 Ready다.
- 긴 요청(업로드, 리포트)에서만 실패가 집중된다.

## 진단 절차
1. `kubectl get endpoints <upstream-service>`로 업스트림 파드가 모두 Ready인지 확인한다. 아니면 RB-03을 본다.
2. 게이트웨이 설정에서 `proxy_read_timeout`, `proxy_connect_timeout`, keepalive 값을 확인한다.
3. 게이트웨이 접근 로그에서 실패 요청의 upstream_response_time을 본다.

## 조치 절차
1. 타임아웃 값이 실제 업스트림 응답 시간보다 짧으면 ConfigMap의 값을 늘리는 변경을 요청한다.
2. keepalive 커넥션 수 부족이면 `keepalive_requests` 증설을 요청한다.
3. 설정 반영은 게이트웨이 설정 리로드 절차로 적용하고 오류율을 확인한다.

## 주의
- 업스트림 파드를 건드리는 조치는 이 런북에 없다.
