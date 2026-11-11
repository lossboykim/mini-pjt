# RB-12 TLS 인증서 만료 임박·만료

- 대상 서비스: api-gateway(인그레스)
- 증상 키워드: 인증서 만료, TLS, certificate expired, HTTPS 오류

## 증상
- 클라이언트에서 `certificate has expired` 오류가 나거나 만료 7일 전 경고가 온다.

## 진단 절차
1. `kubectl get certificate -A`로 READY 상태와 만료 시각을 확인한다.
2. `kubectl describe certificate <name>`에서 갱신 실패 이벤트를 확인한다.

## 조치 절차
1. 자동 갱신이 실패했다면 cert-manager 로그의 오류를 보안 담당자에게 전달한다.
2. 갱신 후 `kubectl get secret <tls-secret>`의 갱신 시각을 확인한다.
3. 브라우저 또는 `openssl s_client`로 새 인증서가 적용됐는지 확인한다.
