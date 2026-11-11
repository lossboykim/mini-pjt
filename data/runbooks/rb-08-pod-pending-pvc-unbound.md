# RB-08 Pod Pending: PVC 바인딩 실패

- 대상 서비스: 볼륨을 쓰는 파드(DB, 큐, 업로드 서비스 등)
- 증상 키워드: Pod Pending, 스케줄링 실패, PVC, unbound, 볼륨

## 증상
- 파드가 Pending이고 Events에 `pod has unbound immediate PersistentVolumeClaims`가 있다.
- 노드 리소스는 충분하다.

## 진단 절차
1. `kubectl get pvc`에서 해당 PVC의 STATUS가 Pending인지 확인한다.
2. `kubectl describe pvc <name>`에서 StorageClass 이름과 Events의 프로비저닝 오류를 본다.
3. `kubectl get storageclass`로 지정한 StorageClass가 실제로 존재하는지 확인한다.

## 조치 절차
1. StorageClass 이름 오타이면 올바른 이름으로 PVC 매니페스트 수정을 요청한다.
2. 프로비저너 오류이면 스토리지 담당자에게 Events 내용을 전달한다.
3. PVC가 Bound가 된 뒤 파드가 Running이 되는지 확인한다.

## 주의
- PVC를 삭제하지 않는다. 데이터가 사라질 수 있다.
