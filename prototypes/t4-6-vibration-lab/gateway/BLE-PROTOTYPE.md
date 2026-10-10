# UNO Q 무선 IMU 게이트웨이

`NU-40 + IMU → BLE → UNO Q Linux → USB ADB 전달 → 컴퓨터 웹 화면`

UNO Q의 Linux Python이 BLE를 수신하고, STM32 MCU 스케치가 내장 8×13 LED 매트릭스를 표시한다. PC의 Bluetooth는 사용하지 않는다. Python은 실제 수신한 데이터만 전달하며, 연결이 끊겨도 예시 데이터로 대체하지 않는다.

## 필요한 환경

- UNO Q Linux: Python 3.10 이상, 실행 중인 BlueZ 및 전원이 켜진 Bluetooth 어댑터, Arduino Router의 `/var/run/arduino-router.sock`.
- Python 패키지: `requirements.txt`의 Bleak 3.0.2, arduino-router-bridge 0.5.1. Linux에서 각각 dbus-fast와 msgpack이 추가로 필요하다. 가상환경에 설치한다.
- MCU 빌드: `arduino:zephyr:unoq`, 코어에 포함된 `Arduino_LED_Matrix`, `Arduino_RouterBridge` 라이브러리. 이 코드에 desktop App Lab이나 App Bricks는 필요하지 않다.
- 컴퓨터: 대상 UNO Q를 선택한 ADB와 기존 로컬 대시보드.

보드에 인터넷이나 pip가 없으면, PC에서 **Linux aarch64 및 보드의 Python 버전에 맞는 wheel**을 준비하여 USB로 복사한 뒤 가상환경에 설치한다. Mac용 wheel은 사용할 수 없다. 설치·업로드는 장치 선택을 확인한 상태에서 별도로 수행한다.

## 실행

1. `unoq_led/unoq_led.ino`를 UNO Q의 STM32 MCU에 컴파일·업로드한다. Linux를 다시 설치할 필요는 없다.
2. UNO Q의 가상환경에서 `python unoq_gateway.py`를 실행한다. `--address AA:BB:CC:DD:EE:FF`로 특정 NU-40을 선택할 수 있다. 같은 이름의 센서가 여러 개 보이면 자동 선택하지 않는다.
3. PC에서 해당 UNO Q에 `adb -s <확인된 보드 일련번호> forward tcp:9877 tcp:9877`을 설정한다.
4. 로컬 웹사이트에서 UNO Q 게이트웨이를 연결한다. TCP는 양쪽 모두 loopback으로 한정한다.

`--no-led`는 BLE 수신만 따로 점검할 때 쓰는 명시적 진단 모드다. 기본 실행은 LED RPC 응답까지 확인하여 `led_ready`로 보고한다. LED 오류가 나도 BLE 샘플 수신은 계속된다.

## LED 의미

- **빈 사각형이 천천히 깜빡임:** 샘플 대기·끊김·게이트웨이 응답 없음.
- **왼쪽 점이 깜빡임:** 최근 1.5초 이내 실제 IMU 데이터를 받음.
- **오른쪽 막대:** 최근 1초 가속도에서 축별 평균을 뺀 3축 RMS. 0.25 g에서 최대 8칸. 자세 변화도 영향을 주므로 고장 판정이나 수명 예측 표시가 아니다.
- Python이 멈춰도 MCU가 마지막 정상 표시를 유지하지 않도록 3초 watchdog을 둔다.

## BLE 프로토콜 v1

| 항목 | UUID / 형식 |
|---|---|
| 광고 이름 | `NU40-IMU` |
| 서비스 | `7e57a000-b792-4d7a-a252-87986150fe70` |
| 데이터 NOTIFY | `7e57a001-b792-4d7a-a252-87986150fe70` |
| 메타 READ + NOTIFY | `7e57a002-b792-4d7a-a252-87986150fe70` |
| 데이터 | 20바이트 little-endian `<IIhhhhhh>`: seq, t_us, ax, ay, az, gx, gy, gz |
| 메타 | 12바이트 little-endian `<BBBBHHHH>`: 버전, 상태, WHO_AM_I, I2C 주소, ODR, 가속도 범위, 자이로 범위, flags |

설정은 208 Hz, ±4 g, ±500 dps이며 변환은 가속도 `raw × 0.000122 g`, 자이로 `raw × 0.01750 dps`다. 설정이 다르면 잘못된 단위 변환을 막기 위해 수신을 거절한다. `seq`와 `t_us`는 NU-40에서 온 값을 그대로 전달한다. BLE 손실은 순번 누락으로 PC에서 계산한다.

포트 9877은 줄마다 JSON을 내보낸다. `sample`은 기존 6축 샘플, `meta`는 센서 식별·설정, `error`는 수신 오류, `gateway`는 BLE/LED 연결 상태다. 새 클라이언트에 과거 샘플을 보내지는 않는다.

## 확인 범위

`python -m unittest -v test_gateway.py`는 **합성 입력만** 사용해 프로토콜·단위·오류·RMS·신선도 처리를 검증한다. Bluetooth, USB, LED, 웹 화면의 실제 동작을 검증하는 테스트가 아니다. 실기기 결과는 상위 프로젝트 검증 기록을 따른다.

## 공식 근거

- [Arduino UNO Q 사용 설명서](https://docs.arduino.cc/tutorials/uno-q/user-manual/): Linux/MCU 구조, 내장 8×13 매트릭스, Bridge.
- [Arduino LED matrix 공식 예제](https://github.com/arduino/app-bricks-examples/tree/main/core-and-foundational/02-led-matrix/02-led-matrix-frame): `Arduino_LED_Matrix`, `matrix.draw`, RPC 구조.
- [Arduino Router Bridge Python](https://github.com/arduino/arduino-router-bridge-py): 독립 Python 패키지, Unix 소켓, `Bridge.call`.
- [Bleak Linux 문서](https://bleak.readthedocs.io/en/latest/backends/linux.html): BlueZ D-Bus 및 notification 동작.
