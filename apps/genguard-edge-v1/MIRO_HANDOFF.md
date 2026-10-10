# 미로 전달: UNO Q 다변량 온디바이스 학습 연결

미로, 이번 묶음은 **IMU + 기존 발전기 데이터로 정상 운전 패턴을 학습하고, 다음 운전에서 비교하는 코드**야. 기존 웹의 진동 그래프와 MCU 수집기는 유지하고, UNO Q Linux에 분석 프로세스를 추가하면 돼.

**현재 확인한 것:** Mac에서 모델 학습/추론, SQLite 저장, 수집 어댑터의 모의 소켓 연결, 정상 검토/모델 전환 가드, 통합 시나리오 시험. **미확인:** UNO Q 실기기 실행, 실제 발전기 컨트롤러 연결, 실제 발전기 고장 탐지 정확도.

## 먼저 할 일 3개

1. 새 폴더에서 `python -m genguard.demo --output runs/demo-001`을 실행해 전체 흐름을 확인해줘. NumPy 설치는 README 참고.
2. UNO Q에서 기존 `127.0.0.1:9877` IMU gateway가 동작하는지 확인하고, 새 `genguard.stream`을 같은 보드에서 실행해줘.
3. 현재 발전기 실물이 없으므로 **`fusion-hybrid-demo.json`**으로 실측 IMU + 모의 컨트롤러를 연결해보자. 화면 전체에 ‘통합 시연’을 표시하고 카드마다 실제/모의를 나눠줘.

물리 보드 설치·명령은 [UNO_Q_CONNECTION.md](docs/UNO_Q_CONNECTION.md)에 있어. 보드에 NumPy 설치 후에는 학습과 분석에 인터넷이 필요하지 않아.

## 파일 역할

| 파일 | 연결할 곳 |
|---|---|
| `genguard/stream.py` | UNO Q의 기존 gateway 구독 → 2초 진동 특징 + 컨트롤러 snapshot 결합 |
| `genguard/core.py` | SQLite 기록·정상 검토·운전 후 학습·모델 버전 관리 |
| `genguard/model.py` | 부하/외기온 보정 회귀 + 센서 오차 간 관계 학습/비교 |
| `profiles/fusion-hybrid-demo.json` | 이번 행사 실측 IMU + 모의 발전기 값용 프로필 |
| `profiles/fusion-live-template.json` | 실제 컨트롤러 연결 후 장비별 복사/확정할 템플릿 |
| `scripts/controller_simulator.py` | 보드에 모의 RPM·온도·유압·전압·전류 snapshot 작성 |
| `genguard/web.py` | 보드의 분석 결과를 읽는 localhost:9878 API |
| `INTERFACE.md` | 원시→특징 frame 및 controller snapshot 계약 |
| `verification/*-final/` | 실행 결과·학습 모델·평가 수치·그래프 |

## 현재 장비로 시연

아래는 **UNO Q Linux에서** 실행하는 예야. 기존 IMU gateway는 이미 실행 중이라고 가정해. 두 터미널을 사용해.

```sh
cd /home/arduino/genguard-edge-v1
.venv/bin/python scripts/controller_simulator.py --output data/controller.json --condition normal --seed 1
```

```sh
cd /home/arduino/genguard-edge-v1
.venv/bin/python -m genguard.stream \
  --db data/hybrid.sqlite --profile profiles/fusion-hybrid-demo.json \
  --session run-001 --installation phone-fixture-01 \
  --controller-snapshot data/controller.json
```

아이폰과 IMU 부착을 유지하고 **기준 진동을 같은 조건으로** 여러 차례 수집해. 아직 모델이 없으면 `baseline_collecting`이 정상 동작이야. 정상 패턴 학습 단계에서 기준·강함·간헐을 모두 정상으로 넣으면 구분하고 싶은 변화까지 정상 범위에 들어갈 수 있어. 새로운 운전마다 세션 ID를 바꾸고, 실제 한 운전을 여러 독립 운전처럼 나누지 말아줘.

최소한 독립 수집 `run-001`~`run-004`를 마련하고 각 수집 종료 후 아래 명령의 ID를 바꿔 정상 확인을 기록해. 이건 제품의 점검 확인 절차이며 자동 정상 판정을 근거로 학습 승인하는 기능은 없어.

```sh
.venv/bin/python -m genguard --db data/hybrid.sqlite --profile profiles/fusion-hybrid-demo.json \
  review run-001 --status normal --reviewer miro --note '같은 부착과 기준 진동, 모의 정상 운전 확인'
.venv/bin/python -m genguard --db data/hybrid.sqlite --profile profiles/fusion-hybrid-demo.json \
  train --fit run-001 run-002 --calibration run-003 --validation run-004 --output data/candidate.json
.venv/bin/python -m genguard --db data/hybrid.sqlite --profile profiles/fusion-hybrid-demo.json \
  activate --model-file data/candidate.json
```

이제 `run-005`에서 모의 컨트롤러를 `--condition thermal_pressure`로 실행하면 240초부터 온도 상승/유압 하락을 주입해. 아이폰 진동이 그대로여도 다른 센서 때문에 변화가 잡히는지를 확인하는 시연이야. 실제 수집 조건에 따라 탐지 여부/시점은 달라질 수 있어. 실험 결과를 기록해줘. `--condition stale_controller`는 값이 오래된 경우를 시험해.

`train` 최소 횟수·30개 창·3회 연속 경보는 개발 가정이고 실제 군 장비 검증 기준이 아니야. 안정 운전 단계만 지원하므로 시동/예열/정지에는 unknown이 나와. 시동 후 경과 시간과 수집 시작 시간도 구분해야 해.

## 기존 웹 연결

UNO Q에서 분석 결과 API 실행:

```sh
.venv/bin/python -m genguard.web --db data/hybrid.sqlite --profile profiles/fusion-hybrid-demo.json \
  --port 9878 --allow-origin http://127.0.0.1:8765
```

Mac에서 보드를 확인한 뒤 포트 전달:

```sh
adb devices
export BOARD_SERIAL='확인한 UNO Q 일련번호'
adb -s "$BOARD_SERIAL" forward tcp:9878 tcp:9878
```

현재 웹 `http://127.0.0.1:8765`에서 `http://127.0.0.1:9878/results?after=0&limit=100`을 읽어. localhost로 여는 경우 origin도 정확히 맞춰줘. MCU/IMU 포트9877과 새 결과 API9878은 달라. 실제 서버 공개/배포용 인증 API가 아니며 loopback에만 바인딩해.

```js
let after = 0;
async function readEdgeResults() {
  const response = await fetch(`http://127.0.0.1:9878/results?after=${after}&limit=100`);
  if (!response.ok) throw new Error('엣지 결과 수신 실패');
  const { records } = await response.json();
  for (const result of records) {
    // 기존 상태 저장소에 asset_id + record_id로 중복 없이 저장.
    // result.readings는 카드, current_comparison.evidence는 근거 패널.
    // 실제 UI 상태 갱신 함수는 미로 웹 구조에 맞춰 여기서 호출.
    after = Math.max(after, result.record_id);
  }
  return records;
}
```

이 예시는 연결 지점이며 웹 코드를 자동 수정한 것은 아니야. `after`를 DB별로 보관하면 재연결 후 남은 기록을 읽을 수 있어. 전체 복구와 최신값 표시는 구분해줘. `/status`의 세션 open/closed, 새로운 record_id 도착 여부를 함께 확인하고 오래된 마지막 값을 초록 ‘실시간’으로 유지하지 말아줘. 응답의 served_at은 HTTP 응답 시각이고 센서 계측 시각이 아니야. measured_at이 null이면 ‘계측 시각 미확인’을 유지해.

| 결과 필드 | 웹 표시 |
|---|---|
| `data_domain` | live / scenario / hybrid_demo / public_replay에 맞는 상단 모드 |
| `readings.*.source` | 카드별 실측 IMU / 실측 컨트롤러 / 모의 / 공개 재생 |
| `readings.*.quality`, `age_s` | 미연결·불량·오래된 값 구분; null→0 금지 |
| `state=baseline_collecting` | 기준 수집 중 |
| `state=within_baseline` | 학습 기준 내 — 장비 전체 정상 확정 아님 |
| `state=change_observed` | 변화 관찰 · 지속 여부 확인 중 |
| `state=inspection_suggested` | 점검 권고 |
| `state=unknown` | 판정 불가 + quality_flags / reason |
| `current_comparison.evidence` | 현재값·조건별 예상값·편차 근거 |
| `initial_comparison` | 초기 정상 기준과 비교한 결과 |
| `checks` | 확인할 점검 항목, 확정 고장명으로 바꾸지 않기 |
| `open_alert_in_session` | 같은 세션의 미해결 경고 이력. unknown/정상 복귀 시 숨기지 않기 |
| `model_id` / `reference_model_id` | 현재/초기 모델 버전 |
| `rul` / `fault_diagnosis` | 현재 null. 가상 숫자를 실측 추정치로 채우지 않기 |

기존 Onan 모의 패키지의 제조사 기준/규칙 표시는 유지해도 되지만, **새 ML 점수와는 별개**야. 제조사 규칙 경보를 모델이 정상이라는 이유로 지우지 말아줘. 모델 score를 % 고장 확률로 바꾸면 안 돼.

## 실제 발전기 연결 때 필요한 것

장비 모델/제어기 종류, 읽을 수 있는 항목, 단위·측정 위치, Modbus 레지스터 또는 CAN 신호 정의가 필요해. 그것은 일반 공개 엔진 데이터로 대신 결정할 수 없어. 컨트롤러 어댑터가 UNO Q에 snapshot을 쓰도록 연결하고 `source=live_controller`로 보낸 뒤 live 프로필·새 DB로 정상 기준을 다시 학습해.

유압 센서의 원시 V를 bar로 표시하거나, 선간전압을 상전압으로 바꾸거나, 엔진 축출력을 발전 전력으로 쓰면 안 돼. 센서가 없는 장비에서는 해당 항목을 제외한 별도 프로필을 만들고 평가 범위가 줄었다는 표시를 해줘. 모든 장비에 공통 UI를 쓸 수 있어도 기준·지원 항목은 장비별이야.

## 평가를 발표할 때

- 합성 시험: 진동이 그대로인 온도·유압 변화에서 통합 모델이 22초 후 지속 경보, 진동 특징만 본 비교 모델은 지속 경보 없음. **모의 입력의 결과**야.
- 실제 공개 엔진 시험: 이번 고정된 모델은 보류한 고장 조건 3개를 모두 놓쳤어. 공개 데이터가 있다는 것과 현장 고장을 잘 찾는 것은 달라. 이 모델은 운영 활성화하지 않았고 실패 결과도 포함했어.
- UNO Q에서 실행했다고 말하려면 보드 프로세스·새 센서 입력·모델 출력·인터넷 단절 중 기록을 실제로 확인해야 해. 현재 전달물은 Mac 검증까지야.

우선 성공 기준은 **보드에서 실제 IMU + 모의 컨트롤러를 모드 표시와 함께 받고, 네 번의 정상 기록으로 학습한 뒤, 다음 운전에서 모델 버전과 변화 근거를 보여주는 것**이야.
