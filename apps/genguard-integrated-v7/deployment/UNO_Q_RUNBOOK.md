# UNO Q 내부 분석 실행 · 2026-10-11

## 현재 구조

아이폰 진동 → IMU → UNO Q MCU → 기존 gateway(9877) → **UNO Q의 Python 특징 추출 → UNO Q의 모델·규칙 분석 → UNO Q의 웹 API** → USB 터널 → Mac 브라우저.

Mac은 브라우저와 USB 통신만 담당합니다. 외부 AI API를 호출하지 않습니다. 진동은 실측이고 수온·유압·RPM·전압은 시연용 모의입니다. 제품 UI 상단의 `UNO Q 내부 분석`은 서버 OS/CPU/보드 식별값을 확인해 표시합니다.

### 화면 모드와 현재 실행 범위

- `모의 운전`: 준비한 발전기 수치를 변경하는 시연입니다.
- `실측 운전`: 현재 IMU에서 측정한 진동을 사용합니다. 아이폰은 발전기를 대신하는 진동원이며 수온·유압·RPM·전압은 모의입니다.
- 기존 `실측 기록` 버튼은 현재 측정이 아니라 과거 아이폰 실험 재생이었습니다. 상단에서 빼고 `분석 보고서 → 과거 진동 재생`으로 옮겼습니다. 원본은 보존합니다.

웹 파일 제공·API·진동 특징 추출·모델 학습·분석·SQLite 저장은 UNO Q에서 실행합니다. Mac의 `127.0.0.1:9883`은 ADB가 보드의 같은 포트로 연결하는 주소이며 Mac의 Python 서버가 아닙니다. 브라우저는 받은 결과의 화면 표시·그래프·입력 UI를 처리합니다.

기록을 시작하지 않은 상태의 진동 감시는 기존 부착 기준을 사용합니다. 새로 검토·학습한 두 모델은 `센서 · 입력 연결 → 운전 기록 시작` 후 같은 장비·부착·운전 조건에서 사용합니다. 학습된 조건은 기록 양식에 자동 제안됩니다. 재학습이나 기존 기록의 복사는 필요하지 않습니다. 브라우저를 닫아도 이미 시작한 기록과 보드 분석은 계속됩니다.

2026-10-11 재확인: 웹 서버·IMU 수신기가 보드 내부에서 실행 중이고, 활성 모델 두 개 및 기록 8개가 보드 DB에 보존됨을 확인했습니다. 지연으로 종료된 수신기는 일관된 DB 백업 후 GenGuard 서버와 함께 다시 실행했습니다. 기존 부착 기준은 계수 변경·재학습 없이 복원했고 새 센서 창의 보드 분석을 확인했습니다. 부팅 자동 시작과 별도 전원으로 USB를 분리한 시험은 아직 수행하지 않았습니다.

## 설치 위치와 실행

- 새 전용 폴더: `/home/arduino/genguard-v8-20261011`
- 앱: `app/`, 이력 DB: `state/bridge.sqlite`, 이전 정상 기준: `config/imu-baseline-transfer.json`
- 기존 `/home/arduino/t4-6-vibration-lab`과 Clika 모델을 변경하지 않았습니다.
- Python3.13.5와 기존 `/home/arduino/clikart/site`의 NumPy2.5.3 사용.
- Node22.23.3 ARM64를 전용 runtime 폴더에 설치. [공식 다운로드](https://nodejs.org/dist/v22.23.3/), 공식 SHA-256 `a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f` 대조 완료.

현재 프로세스는 실행 중입니다. 아래 시작 명령은 **꺼졌을 때만** 실행합니다. 부팅 자동 시작 서비스는 이번에 등록하지 않았습니다.

```sh
# UNO Q 셸에서, 기존 센서 gateway9877이 실행 중인 상태
sh /home/arduino/genguard-v8-20261011/app/deployment/start-on-unoq.sh
```

Mac에 ADB가 PATH에 없다면 이번에 사용한 경로는 `/private/tmp/genguard-adb-20261011/platform-tools/adb`입니다. 같은 Mac 포트를 다른 서버가 사용하면 먼저 대상 프로세스를 확인합니다.

```sh
adb -s 233936836 forward --no-rebind tcp:9883 tcp:9883
# 브라우저: http://127.0.0.1:9883/
```

이미 같은 장치·포트로 연결되어 있으면 현재 연결을 유지합니다. USB를 다시 연결하면 터널을 다시 만들 수 있습니다. 보드 전원을 별도로 유지하면 USB 화면 연결과 보드 분석은 별개입니다. 전원 독립/재부팅 자동 복구 실물 시험은 아직 하지 않았습니다.

## 기존 정상 기준 보존

`imu-a9bad4ae32431366e759`의 평균·공분산·정밀도·임계값을 변경하지 않았습니다. 15개 정상 창(학습10·확인5)의 학습 근거와 기존 ID를 함께 옮겼습니다.

`--imu-baseline-file`을 명시한 경우에만 재사용합니다. 첫 최신 유효 창의 출처·부착ID·설정샘플링208Hz·범위±4g가 일치해야 활성화됩니다. 다른 장비에 임의로 적용하지 않습니다. 같은 설치ID가 실제 부착이 같다는 것을 물리적으로 증명하지는 않으므로 센서를 다시 붙이면 새 기준을 등록합니다.

활성화 이후 스트림이 다시 바뀌면 기준은 무효화됩니다. `GET /api/imu`의 `baseline_transfer`에서 `coefficients_unchanged:true`, `retrained:false`를 확인할 수 있습니다.

```sh
# 기존 DB를 읽기 전용으로 내보내는 예시
python baseline_transfer.py export --db /기존/bridge.sqlite \
  --baseline-id imu-a9bad4ae32431366e759 --output /새경로/imu-baseline-transfer.json
```

## 브라우저 없이도 분석

`OperationService.receive_imu()`가 각 새 창을 바로 분석하고 `operation_metadata.latest_edge_analysis`에 최신 결과를 저장합니다. 웹 폴링이 있어야만 판단을 진행하는 구조를 해소했습니다. `/api/imu.last_analysis`는 이미 계산된 창 ID·결과·실행 위치를 반환합니다. 최신값이 끊기면 웹은 수신 중단으로 표시하며 과거 결과를 새 측정으로 쓰지 않습니다.

## 파일과 검증

- `baseline_transfer.py`: 엄격한 기존 기준 내보내기/검증
- `hybrid_service.py`, `server.py`: 명시적 복원과 센서 조건 확인
- `operation_service.py`: 센서 도착 시 분석·최신 결과 저장
- `execution_info.py`: 실제 추론 장치 확인
- `web/operation.html`, `web/operation.mjs`: 분석 위치 표시
- `verification/unoq-first-live.json`: 보드 실행·기준 복원·최초 실측 결과
- `verification/unoq-migration-python.log`: Mac 전체234개 통과
- `verification/unoq-migration-javascript.log`: 웹111개 통과

시연 종료 때 전체 보드를 종료하거나 기존 gateway를 죽일 필요는 없습니다. 이 앱의 PID는 전용 `logs/server.pid`, `logs/sensor.pid`에 저장됩니다. 중단이 필요하면 해당 PID의 실행 경로를 먼저 확인하고 이 앱 프로세스만 종료하세요.

추가 검증: UNO Q에서 모델·기준 이전·시뮬레이터 79개 통과(`verification/unoq-board-tests.log`). 과거 특징31개는 Mac/UNO Q 판단이 모두 같고 점수차는 최대8.88e-16입니다(`verification/unoq-model-parity.json`). 이는 과거 특징 재평가이며 새 실시간 실험과 구분합니다.

## 이전 후 새 실물 확인

| 실물 입력 | 실측 RMS | UNO Q 판단 | 확인 |
|---|---:|---|---|
| 기준 진동 | 0.0568–0.0576 g | 정상 | 7/7창 |
| 강한 진동 | 0.1531–0.1545 g | 점검 권고 | 7/7창 |
| 간헐 진동 | 0.0243–0.0261 g | 불규칙 진동 · 구동 상태 확인 | 7/7창 |

기존 정상 기준을 유지한 연속 7창씩의 확인입니다. 실제 발전기 고장 정확도나 새 설치 일반화 검증은 아닙니다. 원본 관측은 `verification/unoq-live-patterns.json`, 화면은 `verification/unoq-intermittent-live.png`입니다.

## 배포 ZIP 구성

ZIP의 `GenGuard-UNOQ-v8` 안에는 최신 `app/`, 이전 정상 기준 `config/`, 이전 당시 일관된 상태 DB 사본 `state/`, Node ARM64 실행 파일과 라이선스 `runtime/`가 포함됩니다. state는 Mac에서 이전한 시점의 기록이며 새 라이브 테스트는 app/verification에 별도로 보존했습니다. 실행 중인 보드 state 위에 덮어쓰지 마세요.

현재 보드의 기존 gateway9877, Python3, `/home/arduino/clikart/site` NumPy를 사용합니다. 따라서 새 보드에 ZIP 하나만으로 자동 설치되는 패키지는 아닙니다. 다른 빈 경로에 풀어 복원할 경우 Node 실행 권한을 확인하고, 기존 서버 포트와 충돌하지 않는지 확인한 뒤 시작합니다. 현재 보드에는 이미 적용되어 있어 다시 설치할 필요가 없습니다.
