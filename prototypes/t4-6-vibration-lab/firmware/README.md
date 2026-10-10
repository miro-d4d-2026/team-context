# NU-40 + IMU USB 계측 예제

`nu40_imu/nu40_imu.ino`는 NU-40에 연결한 LSM6DS3 계열 센서의 3축 가속도와 각속도를 읽습니다. 프린터·모터·햅틱을 제어하지 않습니다. Arduino UNO Q는 이번 예제의 경로에 들어가지 않습니다.

## 연결

NUCODE Arduino 보드 정의의 기본 `Wire` 핀은 **SDA = P0.26, SCL = P0.27**입니다. 센서와 GND를 공통으로 연결하고, 센서 모듈에 맞는 전원을 공급합니다. 점퍼선으로 연결했다면 SDA/SCL이 이 핀으로 연결됐는지 확인하세요. 다른 I²C 커넥터의 핀을 자동으로 바꾸지는 않습니다.

## 빌드

공식 NU40 보드 패키지 `nucode:nrf52@1.0.2`와 그 안의 Wire/TinyUSB를 사용합니다. 센서 라이브러리는 추가로 설치하지 않습니다.

```sh
arduino-cli core update-index --additional-urls 'https://raw.githubusercontent.com/Nucode01/Adafruit_nRF52_Arduino/refs/heads/master/package_nuduino_index.json'
arduino-cli core install nucode:nrf52@1.0.2 --additional-urls 'https://raw.githubusercontent.com/Nucode01/Adafruit_nRF52_Arduino/refs/heads/master/package_nuduino_index.json'
arduino-cli compile --fqbn nucode:nrf52:nu40dk --output-dir firmware/nu40_imu/build firmware/nu40_imu
```

마지막 명령은 프로젝트 `prototypes/t4-6-vibration-lab`에서 실행합니다. 빌드는 보드에 업로드하지 않습니다. Arduino IDE에서는 `NUBoards nRF52 → NU40DK nRF52840`과 실제 NU-40 포트를 선택하세요. 업로드는 기존 보드 스케치를 대체합니다.

2026-10-10 로컬 빌드 결과: 스케치 58,816 바이트, 전역변수 8,972 바이트. 보드 패키지의 `USE_TINYUSB` 중복 정의 경고가 있지만 빌드는 통과했습니다. macOS 패키지 내 `tools/adafruit-nrfutil/macos/adafruit-nrfutil`에 실행 권한이 빠져 있어 해당 실행 파일에만 사용자 실행 권한을 추가했습니다. 소스와 보드 패키지 버전을 바꾸면 다시 빌드하세요.

`build/nu40_imu.ino.uf2`는 동일한 빌드의 `.hex`를 보드 패키지의 `tools/uf2conv/uf2conv.py`로 변환한 파일입니다. 시작 주소 `0x26000`, family `0xADA52840`, S140 6.1.1용입니다. 이 부트로더/SoftDevice 조합이 확인된 보드에서만 사용하세요. 빌드 성공은 센서 실측 성공을 뜻하지 않습니다.

## 출력과 진단

USB CDC 직렬 포트를 115200으로 열면 한 줄에 JSON 하나가 나옵니다. USB CDC는 물리 UART가 아니므로 115200이 USB 전송량 제한을 의미하지 않습니다.

- `meta`: 2초마다 보드·센서 ID·설정을 전달합니다.
- `sample`: `seq`, `t_us`, `ax_g/ay_g/az_g`, `gx_dps/gy_dps/gz_dps`를 전달합니다.
- `error`: 읽기에 실패하면 샘플을 만들지 않고 오류를 전달합니다. 센서 탐지가 실패하면 응답한 I²C 주소 목록을 포함하고 2초마다 다시 탐지합니다.

I²C 주소 `0x6A`, `0x6B`에서 WHO_AM_I가 `0x69` 또는 `0x6A`인 장치만 설정합니다. `0x69`는 LSM6DS3 계열, `0x6A`는 LSM6DS3TR-C의 ID입니다. JSON의 센서명은 이 ID에 따른 분류로, 패키지 인쇄를 독립적으로 확인한 결과는 아닙니다.

설정값은 가속도 **208 Hz, ±4 g**, 자이로 **208 Hz, ±500 dps**입니다. 가속도 환산은 `0.000122 g/LSB`, 각속도는 `0.01750 dps/LSB`를 사용합니다. 가속도는 중력을 포함한 센서 좌표계 값이며, 자동 보정·중력 제거·고장 판정·잔여수명 예측을 하지 않습니다.

`t_us`는 새 데이터 플래그를 확인한 뒤 레지스터 읽기를 시작할 때의 보드 `micros()` 값입니다. 센서 내부의 측정 시각이나 UTC 시각이 아닙니다. 약 71.6분마다 32비트 정수가 순환하므로 시간 차는 `(현재 - 이전) mod 2^32`로 계산해야 합니다. `seq`는 성공적으로 읽은 샘플마다 증가하며 재부팅 시 0으로 시작합니다. USB 소비자가 없어도 센서 읽기는 계속되어 연결 후 오래된 샘플을 새 측정처럼 전달하지 않습니다.

FIFO를 사용하지 않는 초기 계측 예제입니다. `208 Hz`는 센서 설정값이며 실효 수집률 보장이 아닙니다. 출력 지연 동안 발생한 센서 내부 샘플 유실은 `seq`만으로 세지 못하므로, 실제 타임스탬프 간격과 수집률도 확인해야 합니다. 208 Hz로는 모든 프린터 진동 주파수를 관찰할 수 없습니다.

보드 패키지의 Wire 구현에는 버스 대기 시간제한이 없습니다. 배선 문제로 I²C가 멈추면 오류 JSON도 멈출 수 있으므로, 수신 측에서 마지막 샘플 시각을 확인해야 합니다. 배선을 수정할 때는 전원을 분리하세요.

## 근거

- [NUCODE NU-40 Arduino 설치 안내](https://nucode.gitbook.io/nuseries/dks/nu-40-dk)
- [NUCODE NU40 variant 핀 정의](https://github.com/Nucode01/Adafruit_nRF52_Arduino/blob/master/variants/nu40dk_nrf52840/variant.h)
- [ST LSM6DS3 공식 드라이버](https://github.com/STMicroelectronics/lsm6ds3-pid)
- [ST LSM6DS3TR-C 데이터시트](https://www.st.com/resource/en/datasheet/lsm6ds3tr-c.pdf)
