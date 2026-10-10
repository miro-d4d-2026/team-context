# 설치와 재실행

이 폴더는 2026-10-10에 검증한 계측 시제품이다. 보드 설정을 자동 설치하거나 펌웨어를 자동 업로드하지 않는다. 아래 명령은 이 폴더에서 실행한다.

## 1. 컴퓨터의 웹 화면

Python 3.10 이상을 사용한다. 실측 당시 Mac에서는 Python 3.12, UNO Q Linux에서는 Python 3.13.5를 사용했다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

브라우저에서 http://127.0.0.1:8765/ 를 연다. 보드가 없으면 미연결 상태이며 가짜 실측값을 만들지 않는다.

보존한 실제 연결 검증 기록을 재생하려면 서버 실행 전에 아래 두 파일을 복사한다. 웹에서 해당 항목의 **재생**을 누르면 과거 실측값이라는 표시가 나타난다.

```sh
mkdir -p recordings
cp evidence/recordings/20261010T074432-2c6a1881.csv recordings/
cp evidence/recordings/20261010T074432-2c6a1881.json recordings/
```

## 2. UNO Q MCU와 IMU

- LSM6DS3 계열 IMU를 UNO Q의 3.3 V QWIIC 커넥터에 연결한다. 스케치는 `Wire1`을 사용한다.
- 업로드할 소스는 `gateway/unoq_imu/unoq_imu.ino`다.
- 보드의 설치된 코어·로더 버전을 먼저 확인하고, 그 버전에 맞는 Arduino 공식 빌드·업로드 경로를 사용한다. 현재 보드에는 `arduino:zephyr` **0.51.0**으로 보드에서 컴파일한 결과를 업로드했다.
- 당시 사용한 라이브러리는 Arduino_RouterBridge 0.2.2, Arduino_RPClite 0.2.0, MsgPack 0.4.2, DebugLog 0.8.4, ArxTypeTraits 0.3.2, ArxContainer 0.7.0이다. 설치 파일과 펌웨어 바이너리는 저장소에 포함하지 않는다.
- 다른 환경의 코어 1.0.0 빌드 성공은 기존 보드 로더와의 호환성을 보장하지 않는다. 상세 동작과 빌드 결과는 [스케치 문서](gateway/unoq_imu/README.md)를 따른다.

## 3. UNO Q Linux 수신기

Arduino Router가 `/var/run/arduino-router.sock`에서 실행되는 UNO Q를 사용한다. USB 통신에는 [Google 공식 Android Platform Tools의 ADB](https://developer.android.com/tools/releases/platform-tools)를 사용한다. `adb devices`에 표시되는 대상 보드를 확인한다.

아래 `BOARD_SERIAL`은 실제 대상 보드의 일련번호로 지정한다. 코드를 전달할 보드가 맞는지 확인한 뒤 실행한다.

```sh
export BOARD_SERIAL='대상 UNO Q 일련번호'
adb -s "$BOARD_SERIAL" shell mkdir -p /home/arduino/t4-6-vibration-lab/gateway
adb -s "$BOARD_SERIAL" push gateway/unoq_qwiic_gateway.py /home/arduino/t4-6-vibration-lab/gateway/
adb -s "$BOARD_SERIAL" push gateway/requirements.txt /home/arduino/t4-6-vibration-lab/gateway/
adb -s "$BOARD_SERIAL" shell 'python3 -m venv /home/arduino/t4-6-vibration-lab/.venv'
adb -s "$BOARD_SERIAL" shell '/home/arduino/t4-6-vibration-lab/.venv/bin/python -m pip install -r /home/arduino/t4-6-vibration-lab/gateway/requirements.txt'
```

보드에 인터넷이 없으면 보드의 Python 버전과 Linux aarch64에 맞는 wheel을 컴퓨터에서 받아 USB로 전달하고, 보드에서 `pip install --no-index --find-links <wheel 폴더> -r <requirements 경로>`로 설치한다. 실측 보드는 이 방법으로 설치했다. Mac용 바이너리 wheel은 사용할 수 없다.

실측 보드는 `ensurepip`도 없어 기본 가상환경 생성이 실패했다. 같은 오류가 나면 `python3 -m venv --without-pip /home/arduino/t4-6-vibration-lab/.venv`로 환경을 만들고, 공식 PyPI에서 받은 `pip` wheel을 함께 전달한다. 보드에서 `PYTHONPATH=<pip wheel 전체 경로> <가상환경>/bin/python -m pip install --no-index --find-links <wheel 폴더> pip -r <requirements 경로>`로 pip와 의존성을 설치할 수 있다. 이때 모든 wheel은 보드 Python 버전에 맞아야 한다.

설치 후 별도 터미널에서 수신기를 실행한다. 이 명령은 수신기가 이미 실행 중일 때 중복 실행하지 않는다.

```sh
adb -s "$BOARD_SERIAL" shell '/home/arduino/t4-6-vibration-lab/.venv/bin/python /home/arduino/t4-6-vibration-lab/gateway/unoq_qwiic_gateway.py'
```

컴퓨터의 다른 터미널에서 USB 전달을 열고 웹 서버를 실행한다.

```sh
adb -s "$BOARD_SERIAL" forward tcp:9877 tcp:9877
.venv/bin/python server.py
```

웹의 **UNO Q 연결하기**를 누른다. 센서명·LIVE·실제 수집률이 나타나면 기록을 시작할 수 있다. 수신기는 보드의 `127.0.0.1:9877`, 웹 서버는 컴퓨터의 `127.0.0.1:8765`에만 바인딩한다.

## 4. 기존 장비용 바로가기

`start.command`와 `launch.py`는 기존 검증 장비용 편의 실행기다. `launch.py`의 `DEVICE`는 검증 당시 UNO Q의 일련번호이고, ADB 경로는 `.tools/platform-tools/adb`다. 다른 보드에서는 `DEVICE`를 수정하고, 설치한 ADB 경로를 맞춘다. 앞 절의 명시적 실행 방법을 사용해도 된다.

`.venv`, `.tools`, 실행 중 생성되는 `recordings/`, 컴파일 산출물은 Git에서 제외한다. 보존한 검증 기록만 `evidence/recordings/`에 포함했다.

## 5. 장치 없이 실행하는 검사

```sh
.venv/bin/python -m unittest -v test_server.py test_gateway_transport.py
.venv/bin/python -m unittest discover -s gateway -p 'test_qwiic*.py' -v
```

현재 경로의 합성 입력 검사 23개이며, 실제 장치 검증은 [VERIFICATION.md](VERIFICATION.md)의 별도 근거를 따른다. 이전 NU-40·BLE 시제품 파일은 개발 이력으로 보존했으며 현재 직접 연결 실행에 필요하지 않다.
