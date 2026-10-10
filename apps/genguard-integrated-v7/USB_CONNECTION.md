# 실제 UNO Q → GenGuard USB 연결

> **2026-10-11 UNO Q 이전 업데이트:** 현재 실행은 UNO Q 내부의 센서 특징 추출·모델 분석·웹 API입니다. 아래 Mac 실행 결과는 이전 단계의 기록입니다. 현재 실행법과 복원 기준은 [UNO_Q_RUNBOOK.md](deployment/UNO_Q_RUNBOOK.md)를 우선 확인하세요. 기존 정상 기준은 재학습하지 않고 옮겼습니다.


2026-10-11 현재 연결된 보드에서 **실제 IMU 샘플 수신과 사이트 API 전송을 확인했다.**

```text
아이폰의 물리적 진동
  → 아이폰에 고정한 LSM6DS3TR-C IMU
  → UNO Q MCU의 기존 센서 펌웨어
  → UNO Q Linux의 기존 gateway (127.0.0.1:9877)
  → USB ADB 터널 (Mac 127.0.0.1:19877)
  → 2초 RMS·간헐성 특징 계산
  → GenGuard /api/imu/windows
  → 실제 센서 그래프·현재 정상 기준과 비교
```

**아이폰 앱이 웹사이트에 값을 보내는 방식은 아니다.** 아이폰은 진동을 만들고, 부착한 IMU가 진동을 측정한다. USB 케이블을 연결한 현재 구성에서는 센서 취득은 UNO Q, 특징 계산과 모델 분석은 Mac에서 실행된다. 이를 UNO Q 온디바이스 분석 완료라고 발표하지 않는다.

## 이 Mac에서 바로 다시 연결

1. UNO Q를 USB로 연결하고 Qwiic IMU 케이블을 유지한다.
2. GenGuard 서버를 `http://127.0.0.1:9883`에서 실행한다.
3. 패키지 루트에서 아래 명령을 실행한다.

```sh
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
python3 -m adapter.usb_connect \
  --installation phone-fixed-20261011 \
  --api-url http://127.0.0.1:9883
```

이 Mac에서는 `adapter/USB_IMU_연결.command`를 더블클릭해도 된다. 연결기는 이미 설치된 팀 gateway가 꺼져 있으면 실행하고, USB 터널을 만든 뒤 실측 창을 API로 전달한다. **MCU 펌웨어를 업로드하거나 기존 프로젝트·모델을 덮어쓰지 않는다.** 다른 프로세스의 포워딩을 덮어쓰거나 수집기를 종료하지 않는다.

`sensor_connected` → `connected` → 약 2초마다 `accepted`가 보이면 전송 중이다. `quality.valid`가 `true`인지도 확인한다. 연결을 끊거나 API가 멈추면 전송기는 종료한다. 오래된 데이터는 새 실측으로 재전송하지 않으며 사이트에는 수신 중단이 표시된다.

다시 부착하거나 방향·받침을 바꾸면 `--installation` 이름을 새로 지정하고 정상 기준을 다시 수집한다. 자동으로 이상 상태를 정상에 편입하지 않는다.

## 사이트에서 시연

1. 화면의 **실제 IMU** 모드를 선택한다. 수온·유압 등 웹 입력은 **모의** 표시를 유지한다.
2. 아이폰·IMU·받침을 고정하고 아이폰 앱에서 기준 진동을 반복한다.
3. 사용자가 정상으로 삼을 진동임을 확인한 뒤 **정상 기준 수집**을 누른다.
4. 30초·15개의 유효한 2초 창을 수집한다. 기준이 너무 약하거나 일관되지 않으면 등록되지 않는다.
5. 등록 후 기준 진동을 유지했을 때와 **강도 증가 / 간헐 진동**을 선택했을 때를 비교한다.

화면은 RMS의 크기 변화와 간헐성을 비교한다. 같은 샘플을 여러 번 화면에서 읽는 것으로 지속 횟수가 늘지 않으며, 서로 다른 이상 창 3개가 이어져야 진동 점검 권고가 뜬다. 이는 현재 부착 상태의 변화 감지이며 실제 발전기 고장 부위의 확정 진단은 아니다.

## 다른 Mac에서 필요한 것

- Python 3.10 이상 및 이 패키지의 `requirements.txt` 환경
- Google의 공식 [SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) 안에 있는 `adb`
- UNO Q에 팀의 기존 `/home/arduino/t4-6-vibration-lab/gateway/unoq_qwiic_gateway.py`와 `.venv/bin/python`

ADB를 PATH에 두거나 아래처럼 위치를 지정한다. 이 도구는 자동으로 프로그램을 내려받거나 관리자 권한을 요구하지 않는다.

```sh
export GENGUARD_ADB='/설치한/platform-tools/adb'
python3 -m adapter.usb_connect --installation phone-mount-002
```

여러 ADB 기기가 연결됐다면 `adb devices`에 나온 UNO Q ID를 `--serial`로 지정한다. `--check-only`는 실제 IMU 수신까지만 검사하고 API 전송·기준 학습은 하지 않는다. `--local-port`는 Mac의 USB 터널 포트이며, 이미 다른 기기가 점유하면 덮어쓰지 않고 실패한다.

현재 Mac의 ADB는 임시 폴더 `adb`에만 설치했다. 임시 파일 정리 후에는 `GENGUARD_ADB` 또는 PATH를 설정한다.

## 실제 확인 범위

| 확인 항목 | 관측 결과 |
| --- | --- |
| 보드 | UNO Q Linux, USB ADB 쉘 접근 성공 |
| 센서 | LSM6DS3TR-C, Qwiic I²C |
| 설정 | 208 Hz, 가속도 ±4 g, 자이로 ±500 dps |
| 원시 수신 | 약 3초에 626개 실제 샘플 |
| 상태 | `sensor_ready=true`, `router_connected=true`, 센서 읽기 오류 0 |
| API 연결 | 새 GenGuard 서버에 실제 2초 창 연속 수락, 초기 창 품질 통과 |
| 정상 기준 학습 | 사용자가 기준 진동을 확인한 뒤 **15/15개 창으로 등록 성공**, 기준 RMS 약 0.05497 g |
| 학습 직후 판단 | 실측 RMS 약 0.05562 g, CV 약 0.01415에서 **정상 · 등록한 부착 기준과 비슷함** 반환 |
| 강도 증가 실측 | 사용자가 강도 증가를 누른 뒤 RMS 0.1560–0.15624 g, 기준 대비 약 **2.84배** |
| 변화 판단 | 고유 창 55·56·57 모두 품질 통과, 진동과 종합 상태에 **점검** 반환 |
| 정상 복귀 | 사용자가 정지 후 기준 진동으로 복귀. **재학습 없이 동일 기준**으로 창 140·141·142에서 RMS 0.05429–0.05432 g, CV 0.01346–0.01449, 진동·종합 상태 모두 **정상** 반환 |
| 펌웨어 | 기존 펌웨어 그대로 사용 |
| 분석 위치 | 현재 Mac API 호스트 |

기동 전 MCU 링버퍼 초과 누적값이 있었다. 이미 누적된 횟수 자체로 현재 창을 실패 처리하지 않고 **수신 중 증가·샘플 누락·포화·불규칙 시각**을 검사한다. 실제 연결을 위해 품질 기준을 완화하지 않았다.

첫 기준 수집에서는 중간에 진동이 정지해 마지막 검증 창이 달라졌고 등록을 거부했다. 사용자가 **기준 진동**을 다시 실행한 두 번째 수집에서는 등록과 정상 판단이 성공했다. 정상 결과의 요약은 `adapter/usb-live-verification.json`에 남겼다. 이는 이 부착 상태의 실제 센서 학습·분석 증거이며 발전기 고장 분류 정확도 평가가 아니다.

## UNO Q에서 분석까지 실행하려면

동일 패키지의 API와 `adapter.imu_to_api`를 보드 Linux에서 실행하고, 기본 gateway 포트 9877을 직접 구독하면 된다. 보드에서 읽기 전용으로 확인한 실행 조건은 다음과 같다.

| 조건 | 현재 관측 |
| --- | --- |
| CPU·OS | aarch64, UNO Q Debian Linux |
| Python | 3.13.5 |
| NumPy | `PYTHONPATH=/home/arduino/clikart/site`에서 **2.5.3 import 성공** |
| 메모리 | 총 3,669 MiB, 확인 시 available 3,185 MiB |
| 저장 공간 | `/home/arduino`에 약 15,998 MiB 여유 |
| Node.js | PATH와 `/home/arduino`, `/opt`, `/usr/local`의 제한 탐색에서 실행 파일 미발견 |

미로가 설치한 NumPy를 재사용할 수 있다. 추가로 **공식 Node.js Linux ARM64 실행 환경**을 새 별도 폴더에 준비하고 PATH를 지정하면, 이 패키지의 고정 규칙 처리기를 실행할 조건을 갖출 수 있다. 기존 ClikaRT 폴더나 모델을 교체할 필요는 없다. Python+NumPy 분석과 Node 규칙 처리기를 모두 보드에서 기동한 뒤 실제 수신·기준 학습·연속 분석의 지연과 메모리를 검증해야 한다. 현재 작업에서는 추가 Node 설치나 보드 내 v7 실행은 하지 않았다.

준비 후 사용할 환경의 예시는 다음과 같다. `NODE_ARM64_BIN`은 실제로 검증한 Linux ARM64 Node 실행 파일이 들어 있는 **디렉터리**로 바꾼다.

```sh
export PYTHONPATH=/home/arduino/clikart/site
export PATH="$NODE_ARM64_BIN:$PATH"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
python3 server.py --host 127.0.0.1 --port 9883
# 별도 보드 터미널
python3 -m adapter.imu_to_api --installation phone-fixed-board-001 \
  --api-url http://127.0.0.1:9883
```

그때 Mac은 `adb forward tcp:9883 tcp:9883`으로 보드 웹 화면에 접근할 수 있다. 동일한 Mac 포트의 기존 서버가 있다면 다른 로컬 포트를 선택한다. 보드 실행 환경 준비와 실제 통합 검증이 끝나기 전까지는 온디바이스 분석 완료라고 표시하지 않는다.

공식 연결 근거: [Arduino UNO Q Remote Access](https://docs.arduino.cc/tutorials/uno-q/remote-access/), [Arduino UNO Q User Manual](https://docs.arduino.cc/tutorials/uno-q/user-manual/). 팀 gateway는 현재 보드에 설치된 파일을 직접 읽어 형식을 확인했다.
