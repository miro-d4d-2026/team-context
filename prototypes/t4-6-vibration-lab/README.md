# T4-6 첫 계측: UNO Q + QWIIC IMU

현재 구성은 **LSM6DS3 계열 IMU → UNO Q QWIIC → USB → 컴퓨터 웹 화면**이다. UNO Q의 STM32가 센서와 내장 LED 매트릭스를 담당하고, Linux가 측정값을 USB로 전달한다.

2026-10-10 실제 센서 취득·LED·웹 표시·CSV 저장·재생까지 확인했다. [확인 결과와 한계](VERIFICATION.md)를 먼저 읽고, 새 컴퓨터에서는 [설치와 재실행 안내](SETUP.md)를 따른다. 저장소에는 실행 환경과 컴파일된 펌웨어를 포함하지 않는다. 프린터 왕복 동작의 성공 및 고장·잔여수명 추정은 아직 확인하지 않았다.

## 첫 마일스톤

1. 센서 식별과 6축 실제 데이터 수신을 확인한다.
2. 정지 상태 30초, 손으로 천천히 기울이기 10초를 각각 기록한다.
3. CSV를 내려받고 같은 기록을 웹에서 재생한다.
4. 센서가 멈췄을 때 웹이 실측 중으로 표시하지 않는지 확인한다.

이 단계는 측정 경로 검증이다. 프린터의 운전 조건에 따른 진동 변화가 고장·열화·잔여수명 정답은 아니다.

## 실행

기존 실험 컴퓨터에서 `start.command`를 열면 설치된 UNO Q 수신기와 로컬 서버를 시작한다. 새 clone에는 `.venv`와 `.tools`가 없으므로 먼저 [SETUP.md](SETUP.md)를 따른다. 웹 주소는 **http://127.0.0.1:8765**이다. 화면에서 **UNO Q 연결하기**를 누른다. 프로그램은 외부로 공개되지 않는다.

처음 보드를 준비할 때에는 아래 MCU 스케치 업로드와 Linux 수신기 설치가 선행되어야 한다. `start.command`는 펌웨어를 자동으로 바꾸지 않는다. 현재 연결 대상은 USB 일련번호 `233936836`인 UNO Q이며, 다른 보드로 바꾸면 `launch.py`의 대상도 변경한다.

| 파일 | 역할 |
|---|---|
| `gateway/unoq_imu/unoq_imu.ino` | QWIIC 센서 읽기·LED 매트릭스·측정 버퍼 |
| `gateway/unoq_qwiic_gateway.py` | UNO Q Linux에서 측정 배치를 받아 USB 전달 |
| `gateway/requirements.txt` | UNO Q 수신기의 Python 의존성 |
| `server.py` | 컴퓨터의 수신·기록·CSV·재생 서버 |
| `dist/` | 로컬 웹 화면 |
| `recordings/` | 실제 측정 CSV와 조건·설정 메타데이터 |
| `VERIFICATION.md` | 실제 확인 결과와 미확인 항목 |

## 연결과 화면 사용

- IMU를 UNO Q의 **QWIIC 커넥터**에 연결한다. 스케치는 **Wire1**을 사용한다.
- UNO Q를 데이터 통신이 가능한 USB 케이블로 컴퓨터에 연결하고 Linux가 부팅될 때까지 기다린다.
- `start.command`를 실행한 뒤 웹의 연결 버튼을 누른다. 센서명·수집 속도·실측 표시가 나타나는지 확인한다.
- 기록 이름과 조건을 입력하고 **기록 시작 → 기록 종료**를 누른다. 저장된 항목에서 **재생** 또는 **CSV**를 선택한다.
- Arduino 직렬 모니터로 이 측정 스트림을 직접 읽는 구조가 아니다. UNO Q MCU↔Linux의 RouterBridge와 USB 전달을 사용한다.

## LED 표시

- 빈 사각형: 센서 대기 또는 읽기 중단.
- 왼쪽 깜빡이는 점: 최근 실제 IMU 샘플을 읽고 있음.
- 오른쪽 막대: 약 1초 단위로 계산한 축별 평균 제외 3축 RMS. **0.25 g에서 8칸**에 도달하는 표시용 눈금이다.

LED는 MCU가 직접 갱신하므로 컴퓨터 웹이 닫혀도 센서가 정상이라면 계속 표시된다. 웹의 `LED 표시 준비`는 펌웨어의 초기화 응답이며, 물리적인 점등을 카메라로 검증한 결과가 아니다.

## 측정값을 읽는 법

센서 설정은 가속도 **208 Hz / ±4 g**, 각속도 **208 Hz / ±500°/s**이다. 설정 속도와 실제 수신 속도는 별도로 표시한다. 정지 상태의 가속도 벡터에는 약 1 g의 중력이 포함된다. RMS는 축별 평균을 뺀 값이며, 자세 변화까지 보정한 중력 제거값은 아니다.

`seq`는 MCU에서 성공적으로 읽은 샘플 순번, `t_us`는 레지스터 읽기 시작 시각이다. `elapsed_s`는 32비트 시각 순환을 처리한 경과 시간이다. 순번 누락은 버퍼·전달 경로에서 빠진 샘플을 보여주지만, 센서 내부에서 읽지 못한 변환 횟수를 모두 세지는 못한다. 208 Hz로 모든 프린터 진동을 관찰할 수 있는 것은 아니다.

프린터 실험은 센서 고정 방법과 위치를 유지하고, 정지·동일 기준 동작·정상 설정 범위 내 조건 변경을 각각 반복한다. 기록 이름에 회차를 넣고 조건 메모에 고정 위치와 운전 설정을 남긴다.

## 개발 검증

```sh
.venv/bin/python -m unittest -v test_server.py test_gateway_transport.py
.venv/bin/python -m unittest discover -s gateway -p 'test_qwiic*.py' -v
arduino-cli compile --fqbn arduino:zephyr:unoq gateway/unoq_imu
```

단위 테스트는 명시적인 합성 입력을 사용한다. 이 테스트 결과를 실기기·프린터 검증으로 간주하지 않는다. 보드의 코어·로더 버전과 맞는 공식 업로드 경로를 사용해야 하며, 새 코어에서 빌드한 ELF를 구형 로더에 임의로 복사하지 않는다.

초기 NU-40 USB/BLE 검토용 소스는 `firmware/nu40_imu`, `gateway/unoq_gateway.py`, `gateway/unoq_led`에 남아 있다. 현재 실행 경로는 위의 UNO Q QWIIC 직결 예제다.

근거: [Arduino UNO Q 공식 사용 설명서](https://docs.arduino.cc/tutorials/uno-q/user-manual/), [공식 Router Bridge Python](https://github.com/arduino/arduino-router-bridge-py).
