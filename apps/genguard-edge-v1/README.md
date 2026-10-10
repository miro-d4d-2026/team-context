# GenGuard Edge v1 — UNO Q 다변량 운전 학습 패키지

**발전기 컨트롤러 값과 IMU 특징을 함께 학습하고, 운전 후 새 모델을 만들어 다음 운전에서 비교하는 실행 가능한 Python 프로토타입입니다.** 학습·추론·기록은 모두 UNO Q Linux에서 실행하도록 구성했습니다. 이 전달물의 실행 검증은 Mac에서 했으며 실제 UNO Q 설치·속도·실제 발전기 고장 진단은 검증하지 않았습니다.

미로는 먼저 [MIRO_HANDOFF.md](MIRO_HANDOFF.md)를 읽고, 물리 연결은 [docs/UNO_Q_CONNECTION.md](docs/UNO_Q_CONNECTION.md)를 따르면 됩니다.

## 무엇을 만들었나

| 기능 | 구현 상태 |
|---|---|
| 컨트롤러 값 + IMU 특징을 하나의 모델에 입력 | 구현, 모의 통합 시험 완료 |
| 부하·외기온에 따른 정상 기대값 학습 | Ridge 회귀 모델 |
| 센서 간 관계 변화 분석 | 잔차 공분산을 학습한 Mahalanobis 점수 |
| 운전 중 빠른 비교·근거·점검 제안 | 구현; 원인 확정/확률 점수가 아님 |
| 운전 후 학습 → 별도 정상 운전 검증 → 다음 운전 적용 | 구현 |
| 초기 정상 기준 보존 + 최신 모델 병렬 비교 | 구현; 변화가 정상으로 덮이는 것 방지 |
| 네트워크 없이 SQLite 저장·기록 ID로 재조회 | 구현; 원격 자동 업로드/서버 ACK 동기화는 별도 |
| 기존 MCU/gateway의 데이터 읽기 | 어댑터 구현·CPU 모의 소켓 시험; 보드 인수 필요 |
| Modbus/CAN 실제 레지스터 접근 | 미구현, 장비별 어댑터 필요 |
| 실제 발전기 결함 종류/RUL | 미검증, 출력 null |

## 가장 빠른 재현

Python 3.10 이상, NumPy만 필요합니다. Linux/macOS에서 실행합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m genguard.demo --output runs/demo-001
.venv/bin/python -m unittest discover -s tests -v
```

출력 폴더는 매번 새 이름을 사용합니다. 이전 결과를 덮어쓰지 않습니다. 이 명령은 합성 정상 운전 8회로 두 모델을 학습하고, 별도 5개 시나리오를 평가합니다. 결과 `report.json`, `fusion-model.json`, `traces.csv`, 예시 분석 JSON, SQLite가 생성됩니다. 실행 환경과 메모리·시간이 보고서에 기록됩니다. **Mac 결과를 UNO Q 속도로 발표하지 않습니다.**

선택적으로 실제 공개 엔진 데이터 평가:

```sh
.venv/bin/python scripts/evaluate_public.py --output runs/public-001
```

원본 ZIP을 포함한 전체 전달 묶음에서 실행하거나 [자료 선정 문서](research/dataset-selection.md)의 원본을 `data/public/marine-engine-fault/`에 둡니다. 표준 학습/추론에 Pandas, scikit-learn, PyTorch, 인터넷 API, 유료 서비스는 필요 없습니다. 그래프를 다시 그리는 보조 스크립트만 Matplotlib를 사용합니다.

## 세 가지 데이터 모드

| 프로필 | 입력·학습 의미 | 화면 표시 |
|---|---|---|
| `fusion-demo.json` | 모든 센서값이 합성 | 전체 모의 시연 |
| `fusion-hybrid-demo.json` | 실제 IMU + 모의 컨트롤러 | 통합 시연 · IMU 실측 / 컨트롤러 모의 |
| `fusion-live-template.json` | 실제 IMU + 실제 컨트롤러 | 실측 · 검증된 모델 없으면 기준 수집 중 |
| `marine-engine-public.json` | 실제 공개 선박 엔진 기록 재생, IMU 없음 | 공개 데이터 재생 · 전용 모델 |

프로필/장비/출처가 바뀌면 **별도 DB**를 사용합니다. 실측 프로필에 모의 값이 들어오면 unknown으로 처리합니다. 혼합 시연은 별도 `hybrid_demo` 프로필에서만 허용하며, IMU 항목은 live_imu, 컨트롤러 항목은 scenario로 강제합니다. 모의 모델을 live DB에 활성화할 수 없습니다.

## 학습과 판단

1. 입력: 부하·외기온 + RPM·냉각수 온도·오일 압력·출력 전압·전류·진동 RMS·진동 시간 변동(CV).
2. 학습: 정상으로 확인한 과거 운전의 `부하·외기온 → 각 센서 기대값`을 학습합니다.
3. 관계 학습: 기대값 대비 오차들의 분산·상관관계를 함께 학습합니다.
4. 비교: 새 운전에서 예상값과 실제값의 차이를 다변량 점수로 계산합니다.
5. 점수 기준: **다른 정상 운전**으로 경보 기준을 보정합니다. 또 다른 정상 운전에서 오경보/조건 범위를 검사합니다.
6. 적용: 검사를 통과한 후보를 다음 운전 시작 전에 명시적으로 활성화합니다. 기존 모델은 학습 중에도 보존합니다.

이는 데이터로 회귀 계수와 공분산을 학습하는 머신러닝입니다. 처음 제안한 Isolation Forest 대신, 작은 JSON 모델·NumPy 단일 의존성·센서별 예상값 설명을 위해 이 구조를 선택했습니다. 설명의 채널별 편차는 원인의 인과적 기여도나 고장 확률이 아닙니다.

최소 학습 조건은 **학습 2회 + 보정 1회 + 검증 1회**의 분리된 정상 운전이며, 각 회 최소 30개 유효 창입니다. 이 숫자는 소프트웨어 가드의 개발 가정입니다. 4회면 현장 성능이 충분하다는 의미가 아닙니다. 한 운전을 네 파일로 나누어 채우지 않습니다. 안정 운전 조건의 다양성과 독립 반복 검증이 필요합니다.

기본 프로필은 **running 안정 운전만** 분석합니다. 시동·예열·정지는 unknown입니다. 수집기의 elapsed_s는 엔진 시동 후 시간이 아닐 수 있어 기본 모델 입력에서 제외했습니다. 시동/예열의 온도 상승 속도·안정화 시간 분석은 실제 엔진 경과 시간과 각 단계 정상 자료를 확보하여 별도 프로필로 검증할 확장 항목입니다.

## 실제 운전 생명주기 예시

데이터는 먼저 [INTERFACE.md](INTERFACE.md)의 frame으로 수집합니다. 이미 저장된 JSONL 하나를 재생할 수도 있습니다.

```sh
.venv/bin/python -m genguard --db data/asset.sqlite --profile profiles/fusion-live-template.json import-run --input data/run-001.jsonl
.venv/bin/python -m genguard --db data/asset.sqlite --profile profiles/fusion-live-template.json review run-001 --status normal --reviewer miro --note '동일 설치·유사 부하에서 정상 점검 확인'
```

run-002, run-003, run-004도 각각 수집·점검 후 다음을 실행합니다.

```sh
.venv/bin/python -m genguard --db data/asset.sqlite --profile profiles/fusion-live-template.json train \
  --fit run-001 run-002 --calibration run-003 --validation run-004 --output data/candidate.json
.venv/bin/python -m genguard --db data/asset.sqlite --profile profiles/fusion-live-template.json activate --model-file data/candidate.json
```

검증 실패 후보는 activate가 거부됩니다. 정상 점검 상태를 나중에 abnormal/exclude로 바꾸면 해당 기록에 의존하는 후보 활성화를 거부하고, 기존 모델의 실시간 판단도 unknown으로 바뀝니다. 정상이라는 AI 판단만으로 자동 review/재학습하지 않습니다.

추가 운전에서 정상 점검한 이력을 포함해 다시 train하면 새 후보가 됩니다. `max_sessions_per_training=30`, `max_rows_per_session=2000`으로 학습 행을 제한합니다. 전체 원시 진동을 RAM에 올리지 않습니다. 첫 모델은 초기 기준으로 남겨 최신 모델과 함께 비교합니다. 센서 이동/교체는 installation_id를 바꾸고 새 DB에서 기준을 만듭니다. 원래 이력은 보존합니다.

## 평가 결과와 한계

- `verification/demo-final/report.json`: 합성 데이터의 통합 기능 시험. 실제 장비 정확도 아님.
- `verification/public-final/report.json`: 실제 공개 엔진의 고정된 원본 파일 분할 평가. 현재 모델은 보류한 고장 시험 3개에서 지속 경보를 내지 못했습니다. **정상 검증 통과와 고장 탐지 성공은 다릅니다. 이 공개 데이터 후보는 운영 활성화하지 않았습니다.**
- `verification/review-findings.md`: 데이터 경계·동시 실행·정상 검토 철회·스트림 품질 등 코드 검증.

공개 자료에 없는 IMU·발전 출력값을 만들어 붙여 실제 다변량 실측으로 취급하지 않았습니다. 999°C 같은 비정상 측정값은 원값을 보존하고 unknown 처리합니다. 공개 선박 엔진에서 학습한 계수를 다른 발전기의 정상 기준으로 복사하지 않습니다.

현재 단계의 의미는 **온보드 학습/비교 구조와 연결 코드가 준비되었다**는 것입니다. 실제 발전기 적용에는 동시 계측, 해당 장비 정상 운전 이력, 센서별 정상 범위·제조사 보호 기준 확인, 다음 독립 운전 평가가 필요합니다.

## 기록·통신·운영 경계

- SQLite에 창과 결과를 하나의 트랜잭션으로 저장합니다. 동일 seq/내용은 중복 반환, 내용 충돌·시간 역행은 거부합니다.
- `record_id`로 `/results`를 재조회할 수 있으므로 재연결 후 읽지 못한 결과를 가져올 수 있습니다. 원격 서버 영구 저장 ACK·삭제·재전송 큐는 구현하지 않았습니다.
- 공개 모델 및 정상 검증 통과는 제조사 보호 경보·자동 정지 기능을 대체하지 않습니다. 기존 기준표 규칙 평가기를 별도로 유지합니다.
- 모델 점수는 확률이 아닙니다. RUL/고장 부위는 null이고 점검 항목은 검토 후보입니다.
- 한 세션 안의 미해결 경고는 unknown/정상 복귀만으로 삭제하지 않습니다. 세션 종료 후에도 기록에 남습니다. 전사 정비 티켓·확인/해제 워크플로는 기존 웹에서 연결합니다.
- 실행 중 새 운전을 시작하는 것과 학습·활성화는 OS 잠금으로 분리합니다. 학습 프로세스가 죽으면 잠금은 자동 해제됩니다.
- 비가동 학습에는 UNO Q 전원이 필요합니다. 센서 재시작/통신 단절은 세션을 닫고 새 session_id로 시작합니다. 전원 차단 실물 복구 시험은 미실시입니다.

## 출처

- 실제 엔진 자료·저자·CC BY4.0·체크섬: [PROVENANCE.json](data/public/marine-engine-fault/PROVENANCE.json), [Zenodo 원문](https://zenodo.org/records/19857425).
- UNO Q 구조: https://docs.arduino.cc/tutorials/uno-q/user-manual/
- 기존 수집 코드 연결점: [UNO_Q_CONNECTION.md](docs/UNO_Q_CONNECTION.md)의 확인한 GitHub commit 링크.
- 조건부 회귀와 잔차 공분산 구현은 본 패키지 코드입니다. 점수 분포·기준값·세션 요구 수·지속 횟수는 개발 가정으로 검증이 필요합니다.
