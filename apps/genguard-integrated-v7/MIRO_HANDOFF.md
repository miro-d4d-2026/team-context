# GenGuard v7 연결 요약

> **2026-10-11 UNO Q 이전 업데이트:** 현재 실행은 UNO Q 내부의 센서 특징 추출·모델 분석·웹 API입니다. 아래 Mac 실행 결과는 이전 단계의 기록입니다. 현재 실행법과 복원 기준은 [UNO_Q_RUNBOOK.md](deployment/UNO_Q_RUNBOOK.md)를 우선 확인하세요. 기존 정상 기준은 재학습하지 않고 옮겼습니다.


ZIP 전체를 풀어 사용합니다. 기존 v6/팀 원본은 보존했고 이번 통합본만 새 폴더로 만들었습니다.

## 진동 연동 모의 운전

IMU 연결 모드에서 **진동에 맞춰 모의 운전**을 켭니다. 기준 진동을 주면 RPM·전압·부하가 시동 순서로 상승하고 수온이 서서히 오릅니다. 정지하면 출력은0으로 내려가고 수온은 천천히 냉각합니다. 강진동은 실측 기준과 비교하며, 간헐 진동에서는 RPM·전압·부하의 불안정도 모의로 연출합니다. 이 모의 값들을 실제 센서값이나 AI 예측으로 설명하지 않습니다.

`vibration_driven_sim.py`가 시간 기반 모의 계측을 만들고, `linked_simulation_view.py`가 출처·운전 단계별 경고를 연결합니다. API는 API_CONTRACT를 참고하세요. 연동을 끄면 기존 수동 값 편집으로 돌아옵니다.

## 이전 Mac 개발 실행 방법 (현재 보드 실행은 UNO_Q_RUNBOOK 우선)

1. README대로 Python 의존성과 Node.js를 준비합니다.
2. `GenGuard_웹_시작.command`를 실행하고 http://127.0.0.1:9883/ 을 엽니다.
3. UNO Q USB를 연결하고 `adapter/USB_IMU_연결.command`를 실행합니다. ADB 및 보드의 기존 gateway 준비는 USB_CONNECTION.md에 있습니다.
4. 웹에서 **IMU 연결**을 선택합니다. 아이폰 **기준 진동**을 유지한 채 체크박스 → **정상 진동 30초 수집**으로 등록합니다.
5. 아이폰 **정지 → 강도 증가**: 약 6초간 변화가 이어지면 점검 권고가 뜹니다. **정지 → 기준 진동**으로 정상 복귀합니다.

이미 웹과 수신기가 실행 중이면 중복 실행하지 않습니다. 이 Mac에서는 실제 정상 → 강함 → 정상 복귀를 확인했습니다. 기준 RMS 약 0.055 g, 강함 약 0.156 g입니다. 부착 위치가 바뀌면 이 숫자를 정답으로 재사용하지 않고 다시 기준을 등록합니다.

## 연결되는 코드

- `adapter/usb_connect.py`: 기존 UNO Q gateway를 USB로 연결.
- `adapter/`의 수집 코드: 2초 단위 RMS와 시간적 변동성, 품질을 서버에 전달.
- `server.py` → `operation_service.py`: 값 변경, 비교 분석, 실시간 IMU 이력 API.
- `hybrid_model.py`: 지금 부착한 센서의 정상 진동 기준과 변화 비교.
- `operation_model.py`: 같은 부하·운전 시간에서 수온 상승률·유압 변화·진동을 비교.
- `vibration_classifier.py`: 저장된 아이폰 4종 패턴 재생 분석. 학습 범위 밖 패턴은 판단 보류.
- `web/operation.html/css/mjs`: 대시보드·이전 점검과 비교·점검 기록.
- `API_CONTRACT.md`: 기존 웹에 붙일 요청/응답 계약. `GET /api/imu/history`는 실측 RMS 최근 이력을 제공합니다.

새 UI에 붙일 때 `operational_status`는 전체 상태, `analysis.channels`는 변화 근거, `telemetry`의 `source_label`과 `state`는 계측 카드에 사용합니다. 새 데이터가 없으면 미수신으로 표시하며 이전 값을 실시간 정상처럼 표시하지 않습니다.

## 실측과 모의 구분

- 실제: UNO Q + IMU에서 들어오는 진동. 특징 계산·판단도 UNO Q에서 수행합니다.
- 모의: 수온·유압·RPM·전압 등 발전기 값. 직접 바꾸거나 시간별 기록을 재생합니다.
- 계산: 모의 RPM/전력 등에서 계산한 주파수·전류. 독립된 실제 센서가 아닙니다.
- 과거 실측: 아이폰 실험 기록 재생. live_imu와 구분합니다.

진동이 간헐적이라는 사실은 RPM 저하를 측정했다는 뜻이 아닙니다. 이를 구분하려면 실제 RPM을 받거나 별도의 명시된 모의 RPM 이상 시나리오를 선택합니다. 간헐 진동 라이브 시험도 완료했습니다. 9개 유효 창에서 불규칙 진동 점검 권고와 연동 모의 RPM·전압 변화를 확인했습니다. 증거는 verification/strong-intermittent-live.json입니다.

## 채택한 장점과 검증

비교 보고서는 COMPARISON.md에 있습니다. 이치 ZIP과 팀 저장소 Predictive 앱은 40개 파일이 동일했습니다. 수치 변화 표현과 패턴 판단 보류를 가져오고, 기존의 운전 구간 비교·승인 정상 기록 학습·절대 보호 참고값을 유지했습니다. 예측 MLP는 입력 주기와 필요한 계측이 달라 주 모델로 바꾸지 않았습니다.

최종 상태와 검증 자료는 CURRENT_STATUS.md, verification/summary.json, adapter/usb-live-verification.json을 참고합니다. 현재는 UNO Q 내부 배포와 정상·강함·간헐 실물 확인까지 완료했습니다. 세부 증거는 verification/unoq-live-patterns.json을 확인하세요.

모의 운전 시나리오를 적용하면 해당 항목의 그래프로 자동 이동하며, 첫 변화가 있는 10분 지점을 표시합니다. 수온·유압·진동별 그래프와 설정 창 제출 흐름을 브라우저에서 확인했고 웹 테스트 111개가 통과했습니다.
