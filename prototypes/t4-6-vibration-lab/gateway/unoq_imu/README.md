# UNO Q Qwiic IMU + 내장 LED 매트릭스

현재 실물 경로는 **IMU → UNO Q Qwiic → MCU 계측·LED → UNO Q Linux Bridge → USB로 연결된 컴퓨터**입니다. 이 스케치에는 NU-40이나 BLE를 사용하지 않습니다.

## 연결과 센서 설정

UNO Q의 Qwiic는 **Wire1 / I2C4**, **SCL PD12 / SDA PD13**, **3.3 V**입니다. UNO 헤더의 기본 Wire와 다릅니다. [Arduino 공식 사용자 설명서](https://docs.arduino.cc/tutorials/uno-q/user-manual/)와 설치된 코어 1.0.0의 UNO Q overlay에서 확인했습니다.

센서 주소 0x6A·0x6B를 탐색하며 WHO_AM_I 0x69·0x6A만 허용합니다. 가속도 208 Hz, ±4 g와 자이로 208 Hz, ±500 degrees/s를 설정하고 레지스터를 다시 읽어 확인합니다. 가속도는 중력을 포함한 원시 좌표계 값입니다.

## 내장 화면

- 최근 실제 측정이 있으면 왼쪽 점이 깜빡입니다.
- 오른쪽 막대는 1초간 축별 평균을 뺀 진동 RMS를 0~8단계로 표시합니다. Welford 분산 누적을 사용합니다.
- 막대의 전체 높이는 RMS 0.25 g이며, 이것은 **표시 배율**입니다. 고장 임계값이나 장비 안전 기준이 아닙니다.
- 센서가 없거나 최근 측정이 끊기면 빈 사각형이 깜빡입니다.

Linux Router와 초기 Bridge 연결이 완료되면 계측과 LED 갱신은 MCU가 담당합니다. 컴퓨터의 웹 화면이 없어도 MCU는 측정을 계속합니다. `matrix.begin()`의 반환값은 소프트웨어 초기화 상태이며 LED의 실제 점등을 검증한 결과는 아닙니다.

## Linux에서 읽는 RPC

UNO Q 코어의 `Serial`은 RouterBridge Monitor로 연결됩니다. 이 경로의 기본 UART 속도로 샘플마다 긴 JSON을 보내지 않고, 다음 세 함수를 제공합니다.

| 함수 | Python 반환형 | 내용 |
| --- | --- | --- |
| `imu_meta` | `bytes`, 12바이트 | `<BBBBHHHH>`: version=1, ready=0/1, WHO_AM_I, I2C 주소, ODR=208, 가속도 범위=4, 자이로 범위=500, reserved=0 |
| `imu_batch` | `bytes`, 0~160바이트 | 최대 8개 레코드. 레코드마다 `<IIhhhhhh>` 20바이트: seq, t_us, accel X/Y/Z raw, gyro X/Y/Z raw |
| `imu_status` | `list[int]`, 8개 | ready, LED initialized, acquired count, ring overflow count, queued count, read error count, LED level, RMS micro-g |

`MsgPack::bin_t<uint8_t>`는 실제 MessagePack binary로 인코딩되므로 샘플 데이터는 UTF-8 문자열이 아닙니다. 비어 있는 배치는 `b''`입니다. 메타데이터의 ready가 0이어도 ODR과 범위 필드는 설정 상수 208/4/500을 유지합니다.

환산은 `accel_raw × 0.000122 g`, `gyro_raw × 0.01750 degrees/s`입니다. 현재 Linux 게이트웨이는 부분 배치를 약 40회/초로 요청하되 RPC에 걸린 시간을 대기 시간에서 뺍니다. 최대 8개가 꽉 찬 배치는 추가 대기 없이 이어서 가져오고, 빈 배치에는 10 ms 대기를 둡니다. 요청 빈도와 센서 취득률은 서로 다른 값입니다.

256개 레코드를 보관하며 가득 차면 가장 오래된 레코드를 버리고 overflow를 증가시킵니다. 순번은 성공적으로 읽은 샘플마다 증가합니다. Linux가 가져가기 전에 재부팅하거나 큐가 넘치면 순번이 달라지므로 기록에서 확인해야 합니다. 센서 오류가 나면 남아 있는 큐를 비우고 2초마다 재탐지합니다. 오류를 채우는 가짜 샘플은 만들지 않습니다.

`t_us`는 보드가 I2C 레지스터를 읽기 시작한 `micros()`이며 센서 내부 시각이나 UTC가 아닙니다. 약 71.6분마다 32비트 정수가 순환합니다. 센서 FIFO를 쓰지 않으므로 내부 변환 누락은 순번만으로 알 수 없습니다. 실제 시간 간격과 수집률도 봐야 합니다. 잔여수명이나 고장 판정은 포함하지 않습니다.

## 실제 빌드·업로드와 확인 결과

프로젝트 폴더에서 실행합니다.

```sh
arduino-cli compile --fqbn arduino:zephyr:unoq --output-dir gateway/unoq_imu/build gateway/unoq_imu
```

2026-10-10 실제 업로드용 스케치는 **UNO Q에 설치된 Arduino Zephyr 코어 0.51.0으로 보드에서 컴파일**했습니다. 프로그램 22,716바이트, 전역변수 11,248바이트이며, 업로드 후 실제 IMU 수신을 확인했습니다. [온보드 컴파일 로그](../../evidence/unoq-onboard-compile.log)와 [업로드 로그](../../evidence/unoq-upload.log)를 보존했습니다.

그전에 로컬 코어 1.0.0에서도 컴파일을 확인했지만, 실제 배포는 보드의 기존 코어·로더에 맞춘 0.51.0 빌드를 사용했습니다. 위 명령은 실행하는 환경의 설치된 코어를 사용하므로, 배포할 때에는 대상 보드와 버전이 맞는지 확인해야 합니다.

실측 기록은 10,608개 / 50.703497초, MCU 시각 기준 평균 209.1966 Hz였습니다. 기록 구간의 순번 누락과 잘못된 수신 행은 0개였고, CSV 다운로드와 재생 샘플 수를 확인했습니다. 사용자는 “LED와 그래프 모두 반응함”이라고 별도로 확인했습니다. 수신 결과와 사용자의 실물 관찰은 `matrix.begin()`의 소프트웨어 응답과 구분합니다. 자세한 근거와 미확인 범위는 [검증 기록](../../VERIFICATION.md)에 있습니다.
