# UNO Q Qwiic IMU 게이트웨이

현재 구성은 **IMU를 UNO Q의 Qwiic 커넥터에 직접 연결**하는 방식이다.

`IMU → UNO Q STM32 MCU (Wire1) → Arduino Router → UNO Q Linux → USB ADB → 컴퓨터 웹 화면`

MCU가 208 Hz로 IMU를 읽고 내장 LED 매트릭스를 직접 갱신한다. Linux의 `unoq_qwiic_gateway.py`는 원본 샘플을 가져와 기존 웹 화면에 전달한다. NU-40과 Bluetooth는 이 구성에서 사용하지 않는다.

## 필요한 환경

- MCU: [`unoq_imu/unoq_imu.ino`](unoq_imu/unoq_imu.ino)의 직접 연결 스케치. 코어 `arduino:zephyr:unoq`, `Arduino_RouterBridge`, `Arduino_LED_Matrix` 사용. 실제 업로드용 빌드는 보드에 설치된 코어 0.51.0으로 수행했다.
- Linux: Python 3.10 이상, 실행 중인 Arduino Router와 `/var/run/arduino-router.sock`.
- Python: `requirements.txt`의 arduino-router-bridge 0.5.1, msgpack 1.2.3. 가상환경에 설치한다.
- PC: 해당 UNO Q를 선택한 ADB와 기존 로컬 대시보드.

보드에 인터넷이나 pip가 없으면 Linux aarch64 및 보드의 Python 버전에 맞는 wheel을 PC에서 준비해 USB로 복사한다. Mac용 바이너리는 사용할 수 없다.

## 실행

1. 보드의 설치된 코어 및 loader와 호환되는 환경에서 직접 연결 MCU 스케치를 컴파일·업로드한다. 새 코어로 만든 ELF를 이전 loader에 바로 넣지 않는다.
2. UNO Q의 가상환경에서 `python unoq_qwiic_gateway.py`를 실행한다.
3. PC에서 `adb -s <확인된 보드 일련번호> forward tcp:9877 tcp:9877`을 설정한다.
4. 로컬 웹사이트에서 UNO Q 게이트웨이를 연결한다.

9877은 UNO Q와 PC 양쪽에서 loopback으로만 사용한다. 실제 수신 샘플만 전달하며, 오류가 발생하면 예시 데이터로 대체하지 않는다. 새 웹 연결에 이미 전달한 과거 샘플을 재전송하지 않는다.

## 전송 방식

UNO Q의 `Serial`은 일반적인 MCU USB 직렬과 다르다. 최신 코어에서는 RouterBridge의 `Monitor`로 연결되며, Monitor는 Linux Router의 TCP 포트로 전달된다. 내부 MCU↔Linux UART의 기본 속도는 115200 baud다. 208 Hz JSON을 이 UART에 바로 쓰면 대역폭을 초과할 수 있으므로 이 구현은 **이진 샘플 묶음을 RPC로 가져온 다음 Linux에서 JSON으로 변환**한다.

| MCU RPC | 반환값 |
|---|---|
| `imu_meta` | 12바이트 little-endian `<BBBBHHHH>`: 버전, 준비 상태, WHO_AM_I, 주소, ODR, 가속도 범위, 자이로 범위, flags |
| `imu_batch` | 최대 8개 × 20바이트 `<IIhhhhhh>`: seq, t_us, 가속도 XYZ 원시값, 자이로 XYZ 원시값. 데이터 없음은 빈 bytes |
| `imu_status` | uint32 배열 8개: 센서 준비, LED 초기화, 취득 개수, 링버퍼 초과 개수, 큐 깊이, 읽기 오류 개수, LED 높이, RMS micro-g |

스케치의 `MsgPack::bin_t<uint8_t>`는 Python `bytes`로, `MsgPack::arr_t<uint32_t>`는 Python `list`로 전달된다. 센서 설정은 208 Hz, ±4 g, ±500 dps다. 단위 변환은 가속도 `raw × 0.000122 g`, 자이로 `raw × 0.01750 dps`이며 설정이 다르면 수신을 거절한다.

게이트웨이는 부분 묶음을 약 40회/초로 요청하고, 8개가 꽉 찬 묶음은 바로 이어서 요청한다. 빈 묶음에는 10 ms 대기를 둔다. MCU의 샘플 시각과 순번을 유지하며, 링버퍼 초과 개수는 별도로 보고한다. 256개 MCU 버퍼가 꽉 차면 이전 샘플이 버려질 수 있다.

## 상태와 검증 범위

JSONL의 `sample`, `meta`, `error`는 기존 웹 API와 같고, `gateway`에는 `transport: "QWIIC"`, Router 연결, 센서 준비, MCU 카운터가 포함된다. `led_ready`는 **MCU가 보고한 matrix.begin() 성공 여부**다. LED가 실제로 보이는지 확인했다는 뜻은 아니다.

2026-10-10 직접 Qwiic 경로에서 실제 샘플 10,608개를 50.703497초 동안 기록했다. MCU 시각 기준 평균 취득률은 209.1966 Hz, 순번 누락과 잘못된 수신 행은 각각 0개였다. CSV 다운로드는 저장 파일과 바이트 단위로 같았고, 10,608개 샘플을 재생했다. 사용자는 별도로 “LED와 그래프 모두 반응함”이라고 확인했다.

`python -m unittest -v test_qwiic_gateway.py`의 합성 테스트 9개도 통과했다. 실측 수치·사용자 관찰·자동 테스트의 근거와 한계는 상위 [검증 기록](../VERIFICATION.md)에 구분해 두었다. 프린터 운전 실험과 RUL 예측은 아직 수행하지 않았다.

## 이전 BLE 시제품

`unoq_gateway.py`, `unoq_led/`, `test_gateway.py`, `requirements-ble-prototype.txt`와 `BLE-PROTOTYPE.md`는 이전 무선 시제품 자료다. 현재 직접 연결 실행에는 사용하지 않는다.

## 공식 근거

- [Arduino UNO Q 사용 설명서](https://docs.arduino.cc/tutorials/uno-q/user-manual/): Serial/Monitor, Router 및 예약된 UART.
- [Arduino Core Zephyr](https://github.com/arduino/ArduinoCore-zephyr): UNO Q의 Serial 라우팅과 Wire 구성.
- [Arduino Router](https://github.com/arduino/arduino-router/blob/main/main.go): 기본 115200 baud, Monitor TCP 7500.
- [Arduino Router Bridge Python](https://github.com/arduino/arduino-router-bridge-py): 독립 Python 패키지, Unix 소켓, `Bridge.call`.
