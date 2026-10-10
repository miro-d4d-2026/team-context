# UNO Q 연결 — 미로 전달용

이 문서는 **코드 연결 방법**이다. CPU 테스트는 실제 UNO Q 설치, 메모리, 처리 시간 검증을 대신하지 않는다. 발전기 컨트롤러의 실제 Modbus 레지스터·CAN ID는 아직 정해지지 않았으므로 하드웨어 연동 완료로 표시하지 않는다.

## 1. 기존 수집기에 연결

2026-10-10 확인한 `team-context` PR 1 head는 `27f16199954c5c40eceae25cc7665eed4539f098`다.

```text
IMU → UNO Q MCU(Wire1, 208 Hz, ±4 g)
    → Router RPC → UNO Q Linux의 unoq_qwiic_gateway.py
    → 보드 127.0.0.1:9877 (JSONL, 복수 클라이언트 지원)
        ├→ genguard.stream → 보드에서 분석·SQLite 저장
        └→ 기존 ADB forward → Mac server.py → 기존 웹
```

**기존 MCU와 gateway를 유지한다.** 새 어댑터는 보드의 TCP 스트림을 구독하므로 Router를 중복 점유하지 않는다. Mac `server.py`에만 모델을 넣으면 온디바이스가 아니다. 학습·추론 모두 UNO Q Linux에서 실행한다.

기존 수신기는 `/home/arduino/t4-6-vibration-lab/gateway/unoq_qwiic_gateway.py`다. 이미 실행 중이면 중복 실행하지 않는다. MCU·수신기 설치는 원 저장소 `SETUP.md`를 따른다. 웹에서 `LIVE`와 실측 수집률을 확인한 후 어댑터를 시작한다.

## 2. 패키지 전달·설치

Mac에서 대상 보드를 확인하고 새 폴더로 전달한다. 아래 일련번호는 실제 보드로 바꾼다. 이 문서 작성 중에는 보드에 접속하지 않았다.

```sh
adb devices
export BOARD_SERIAL='adb devices에서 확인한 대상 일련번호'
adb -s "$BOARD_SERIAL" shell 'python3 --version'
adb -s "$BOARD_SERIAL" shell 'uname -m'
adb -s "$BOARD_SERIAL" push genguard-edge-v1 /home/arduino/
adb -s "$BOARD_SERIAL" shell
```

이후 명령은 **UNO Q shell 안에서** 실행한다.

```sh
cd /home/arduino/genguard-edge-v1
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

기존 실측 환경은 Python 3.13.5 / Linux aarch64였으며 기본 venv에 ensurepip가 없었던 기록이 있다. 같은 오류라면 기존 `SETUP.md`의 `--without-pip`와 wheel 전달 절차를 따른다. 오프라인 설치에는 보드의 Python/아키텍처용 wheel이 필요하다. Mac용 NumPy 바이너리 wheel을 사용하지 않고, 시스템 Python 패키지를 덮어쓰지 않는다.

4GB 메모리와 센서 수신의 여유를 위해 NumPy 수치연산 스레드를 1개로 제한하고 시작하는 것을 권장한다. 이 설정이 실제 보드 성능을 보증하지는 않는다.

```sh
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
```

## 3. 발전기 컨트롤러 snapshot

컨트롤러 어댑터가 다음 파일을 **UNO Q 안에** 갱신한다. 프로파일의 필요한 항목을 전달하고, 없는 항목을 0으로 채우지 않는다.

```json
{
  "monotonic_s": 12345.0,
  "phase": "running",
  "readings": {
    "load_pct": {"value": 50.0, "unit": "%", "source": "live_controller", "age_s": 0.0, "quality": "ok"},
    "ambient_c": {"value": 25.0, "unit": "degC", "source": "live_controller", "age_s": 0.0, "quality": "ok"},
    "engine_rpm": {"value": 1800.0, "unit": "rpm", "source": "live_controller", "age_s": 0.0, "quality": "ok"},
    "coolant_c": {"value": 80.0, "unit": "degC", "source": "live_controller", "age_s": 0.0, "quality": "ok"},
    "oil_bar": {"value": 3.0, "unit": "bar", "source": "live_controller", "age_s": 0.0, "quality": "ok"},
    "voltage_v": {"value": 240.0, "unit": "V", "source": "live_controller", "age_s": 0.0, "quality": "ok"},
    "current_a": {"value": 15.0, "unit": "A", "source": "live_controller", "age_s": 0.0, "quality": "ok"}
  }
}
```

**위 숫자는 형식 설명용 가상값이다. 실제 측정값이나 정상 기준으로 사용하지 않는다.** 모의 생산자는 `source: scenario`, 공개 데이터 재생은 `public_replay`로 표시한다. 이 원천 정보는 어댑터가 변경하지 않는다. `fusion-live-template.json`은 `live_imu / live_controller`만 허용하므로 실측 IMU와 모의 컨트롤러를 섞으면 `source_mismatch`로 unknown을 반환한다. 모의를 실측으로 위장해 학습시키지 않는다. 전체 모의 학습은 별도 시나리오 프로필·DB를 사용한다.

- `monotonic_s`는 같은 UNO Q의 `time.monotonic()` 값이다. Mac monotonic 값·Unix epoch를 넣지 않는다.
- reading의 `age_s`는 snapshot 작성 때 이미 경과한 센서값 나이다. 어댑터가 파일을 읽을 때 추가 경과 시간을 더한다.
- 나이 한계는 프로필의 항목별 `max_age_s`로 판정한다. 필요하면 `--max-controller-age-s`로 더 엄격한 전체 제한을 줄 수 있다. 미래 시각이나 잘못된 age는 invalid로 전달한다.
- 단위와 출처를 보존한다. `vibration_rms_g`, `envelope_cv`는 실제 IMU로 계산하므로 snapshot의 같은 이름 항목은 무시한다.
- phase는 `starting / warming_up / running / stopped`다. 첫 snapshot이나 유효한 phase가 없으면 종료하며, phase를 임의로 정상 운전으로 가정하지 않는다.
- 임시파일에 완성한 뒤 `os.replace()`로 원자 교체한다. 쓰는 중인 JSON을 직접 읽게 만들지 않는다.

컨트롤러 읽기 뒤 저장 부분을 연결하는 예:

```python
import json, os, time
from pathlib import Path

# 실제 센서 읽기 코드가 아니라, 어댑터에서 받은 결과를 저장하는 함수다.
def publish_snapshot(path, phase, readings):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    packet = {'monotonic_s': time.monotonic(), 'phase': phase, 'readings': readings}
    temporary.write_text(json.dumps(packet, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)
```

파일 도착 시각만으로 실제 계측 동기화가 보장되는 것은 아니다. 서로 다른 버스의 수집 지연·계측 주기를 기록하고, 부하 전환처럼 변화가 빠른 구간의 비교 가능 여부를 따로 검증한다.

## 4. 비교 시작·종료

프로필 템플릿의 asset ID, 필요한 항목·단위, 설치 ID를 장비에 맞게 복사·확정한 후 새 DB를 사용한다. 값의 단위만 맞춘다고 발전기와 장비별 기준이 같아지는 것은 아니다. 아래는 해당 템플릿으로 실행하는 명령이다.

```sh
cd /home/arduino/genguard-edge-v1
mkdir -p state
.venv/bin/python -m genguard.stream \
  --db state/genguard.sqlite3 \
  --profile profiles/fusion-live-template.json \
  --session run-001 \
  --installation mount-001 \
  --controller-snapshot state/controller-latest.json \
  > state/run-001-results.ndjson 2> state/run-001-stream.log
```

- 운전마다 새 `--session`을 사용한다. 오류·MCU 재부팅·재연결 뒤에도 새 ID가 필요하다.
- `--installation`은 장비와 부착 위치·방향·센서 설정을 식별한다. 재부착·방향 변경 후에는 새 설치 ID와 기준을 사용한다.
- host/port 기본값은 보드 `127.0.0.1:9877`이며 임의로 외부에 공개하지 않는다.
- stdout: runtime.ingest 결과 NDJSON. stderr: 품질·수신 상태 로그.
- Ctrl+C/SIGTERM: 세션 종료, 미완성 창 폐기. 통신 단절·3초 무수신·MCU 리셋·중복 순번: unknown 로그와 종료 코드 2. 자동 재접속·정상 누적 없음.
- 운전 후 검토·학습·승격은 루트 README의 별도 `review / train / activate` CLI를 따른다. 수신 callback 안에서 학습하지 않는다. 현재 세션 모델은 중간에 바꾸지 않는다.

모델이 아직 없으면 정상 판정 대신 미학습 상태가 나오는 것이 맞다. 모델을 만들려면 여러 번의 정상 확인 운전과 독립 검증 기록이 필요하다. 기록 하나의 창을 무작위로 나눠 독립 검증으로 취급하지 않는다.

SQLite와 NDJSON은 보드 로컬 기록이다. 원격 웹은 연결 단절 후 마지막 수치가 멈춰 있어도 정상으로 보이지 않도록 마지막 수신 시각과 `미수신 / 상태 알 수 없음`을 별도 표시한다.

## 5. 진동 품질 처리

2초 창의 RMS는 `sqrt(var(ax)+var(ay)+var(az))`다. 축 평균 제거, 모집단 분산을 사용한다. CV는 100ms bin마다 축 평균을 다시 제거한 RMS들의 모집단 표준편차/평균이다. 기존 별도 contracts/v1 RMS와 정의가 다를 수 있으므로 그대로 혼용하지 않는다.

다음은 **시제품 입력 품질 정책**이며 산업용 고장 진단 기준이 아니다.

- MCU `t_us`로 창을 구성. 네트워크 도착 간격으로 샘플 간격을 가정하지 않음.
- uint32 시각·순번의 정상 rollover 처리. 리셋·장시간 점프는 종료.
- seq 누락, 기대 간격의 0.5–1.5배 밖, MCU 오류 카운터 증가: 해당 창 invalid.
- ODR·가속도 full scale 미확인: invalid.
- full scale의 98% 이상, 비유한 값, 기대 샘플 수의 80–120% 밖, 시간 범위 1.8초 미만, 100ms bin당 5샘플 미만: invalid.
- invalid 특징은 0이 아닌 `null / quality: invalid`. 이유는 stream 로그에 기록.

## 6. 보드 인수 확인

1. gateway와 새 AI 프로세스가 실제로 보드에서 실행 중인지 확인한다.
2. 새로운 입력에 따라 결과 시각·seq가 증가하는지 확인한다.
3. 전원은 유지하고 인터넷과 PC 분석 프로그램을 중단해도 보드 기록이 계속 증가하는지 확인한다.
4. 운전 종료 후 정상 검토 → 후보 학습 → 검증 → 승격을 보드에서 실행하고 시간·최대 메모리를 측정한다.
5. 다음 운전이 선택한 모델 버전으로 시작하는지 확인한다. 이상·미검토 세션이 자동 정상 학습되지 않는지 확인한다.
6. IMU 분리·snapshot 갱신 중단·gateway 종료 시 정상 대신 invalid/unknown/명시적 종료가 나타나는지 확인한다.

아직 이 실물 인수 확인은 수행하지 않았다. 4GB의 실제 메모리와 지연은 보드에서 측정한 결과로 보고한다.

## 7. App Lab으로 옮길 때

공식 구조는 `app.yaml`, `python/main.py`, 선택적인 `python/requirements.txt`, 선택적인 `sketch/`다. 영속 모델·기록은 루트 `data/`에 둔다. 기존 host Linux gateway와 App Lab 컨테이너의 네트워크·데이터 경로가 같다고 가정하지 말고 따로 확인한다. 현재 패키지는 위의 직접 Python 실행을 우선한다.

## 출처

- [팀 수신기](https://github.com/miro-d4d-2026/team-context/blob/27f16199954c5c40eceae25cc7665eed4539f098/prototypes/t4-6-vibration-lab/gateway/unoq_qwiic_gateway.py)
- [기존 설치 안내](https://github.com/miro-d4d-2026/team-context/blob/27f16199954c5c40eceae25cc7665eed4539f098/prototypes/t4-6-vibration-lab/SETUP.md)
- [UNO Q 공식 매뉴얼](https://docs.arduino.cc/tutorials/uno-q/user-manual/)
- [UNO Q Debian 안내](https://docs.arduino.cc/tutorials/uno-q/debian-guide/)
- [Arduino 공식 App 파일 명세](https://github.com/arduino/arduino-app-cli/blob/main/docs/app-specification.md)
