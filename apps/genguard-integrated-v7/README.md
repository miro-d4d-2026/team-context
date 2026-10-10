# GenGuard · UNO Q 운전 기록과 검토 기반 ML

실측 IMU 진동과 발전기 모의 계측을 구분해 보여주고, 운전별 기록에 사람이 남긴 정상·비정상 판정으로 모델을 학습하는 해커톤 프로토타입입니다.

**운전 기록 → 상태 검토 → AI 학습 → 성능 확인 → 새 모델 적용 → 다음 운전 보고서**

## 현재 앱

- 운전 현황: 현재 상태·계측·그래프·점검 권고를 한 화면에 표시.
- 기록 · 정상 검토: 정상/비정상/판단 보류/측정 오류와 근거 저장.
- 학습 센터: 같은 조건의 기록을 자동 분리해 실제 ML 학습. 학습·보정·평가 역할을 사람이 지정하지 않음.
- 모델 관리: 새 후보의 실측/합성 출처, 사용 기록 수, 평가 결과와 기존 모델 비교. 적용은 별도 버튼으로 승인하며 다음 유효 실측 창부터 사용.
- 분석 보고서: 당시 모델·판단·사람 검토·점검 조치를 운전 회차별로 누적.
- 센서 · 입력 연결: 기존 UNO Q IMU 수신과 정상 기준 등록 경로 유지.

[최신 자동 학습 사용 안내](AUTO_LEARNING_GUIDE.md) · [현재 검증 상태](CURRENT_STATUS.md) · [UNO Q 자동 시작](deployment/AUTO_START.md)

## 실행

Python 3.10 이상, NumPy, Node.js 18 이상이 필요합니다. Node는 기존 규칙 판정기에 사용합니다. 학습·추론에는 외부 AI API나 GPU가 필요하지 않습니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python server.py --host 127.0.0.1 --port 9883 --state-dir .local-state
```

http://127.0.0.1:9883/ 에 접속합니다. 이미 실행 중인 서버의 포트를 중복 사용하지 마세요. Linux/UNO Q에서도 같은 Python 서버를 실행할 수 있습니다.

저장소에는 개인 센서 기준과 실제 운영 DB를 넣지 않습니다. `model_bundle/baseline.sqlite`는 앱 시작에 필요한 **합성 학습 기준 8회·3,600창**의 고정 seed로 포함합니다. 실측 센서 기록 DB가 아닙니다.

## 실제 센서 연결

기존 팀 UNO Q gateway가 JSONL을 제공해야 합니다. gateway·MCU 설치는 이 앱을 복사하는 것만으로 수행되지 않습니다. 같은 보드에서 예를 들어:

```sh
python3 -m adapter.imu_to_api --installation my-fixed-mount --api-url http://127.0.0.1:9883 --gateway-port 9877
```

자기 부착 조건의 정상 기준을 등록해야 합니다. 다른 사람의 아이폰 부착 기준을 자신의 발전기 기준으로 사용하지 않습니다. 기존 보드 복원 절차는 [UNO Q 실행 안내](deployment/UNO_Q_RUNBOOK.md)를 참고하세요. `deployment/start-on-unoq.sh`는 해당 안내의 **기존 보드 배치와 외부 runtime/config/gateway가 준비된 환경**을 전제로 합니다. 새 clone의 기본 실행 명령은 위 Python 명령입니다.

## 학습 동작과 범위

- 정상만 충분하면 조건부 회귀·잔차 기반 정상 패턴을 학습합니다.
- 정상·비정상이 충분하면 NumPy 기반 이차 로지스틱 분류기도 학습합니다.
- 동일 운전의 구간을 서로 다른 독립 평가 회차로 취급하지 않습니다. 평가 회차는 재학습에서도 학습에 섞지 않습니다.
- 사람의 검토가 바뀌면 관련 후보의 적용 가능 여부도 재확인합니다.
- 학습 중 새 운전이 시작되면 운전 감시를 우선합니다.
- 새 학습 모델은 자동 적용되지 않습니다. 기존 실시간 진동 기준과 과거 기록은 유지합니다.

현재 실제 입력은 IMU 진동입니다. 수온·유압·RPM·전압은 모의 값이며, 진동에 연동한 시동·정지/가열·냉각은 시뮬레이션입니다. 서로 다른 출처를 같은 실측 학습 데이터라고 표시하지 않습니다. 실제 발전기 고장 정확도, 고장 부위 확정, RUL 및 설치 현장 운용 적합성은 검증하지 않았습니다.

## 검사

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s . -p 'test_*.py' -q
node --test web/test_*.mjs
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python deployment/verify_auto_learning.py --output /tmp/genguard-auto-proof.json
```

자동 학습 종단 검사는 격리된 합성 기록을 사용합니다. 정상-only 초기 학습, 새 기록 추가 재학습, 평가 회차 보존, 명시적 모델 적용, 다음 운전 판단, 재시작 뒤 보고서 보존을 검사합니다. 실제 발전기 성능 평가가 아닙니다.

## 파일과 출처

- `review_learning/`: 검토 라벨·자동 분할·ML 학습·모델 관리.
- `learning_workspace.py`, `server.py`: 기존 운영 서비스와 검토 학습 API 연결.
- `web/`: 다섯 메뉴의 웹 화면.
- `adapter/`: 기존 gateway의 실측 특징 수신.
- `model_bundle/`, `*_artifacts/`: 고정 모델·합성 참고 자료·과거 진동 특징 재생.
- [데이터 항목과 판단 방식](FIELDS_AND_METHODS.md), [API 계약](API_CONTRACT.md), [기존 진동 실험 범위](VIBRATION_MODEL.md).
- [이전 README 보존본](docs/PRE_AUTO_LEARNING_README.md): 과거 버전 설명이며 최신 사용 순서는 본문과 자동 학습 안내를 따릅니다.

모델·규칙 원본 출처와 해시는 `model_bundle/source-manifest.json`, 실험 특징 출처는 `replay_artifacts/frozen-original.json`에 보존합니다. 팀 제공 코드와 사용자 제공 화면 참고본을 통합한 작업이며 새 오픈소스 라이선스를 임의로 부여하지 않습니다. 새로운 분류기를 CLIKA로 실행했다고 주장하지 않습니다.

## PC를 옮기거나 USB를 다시 연결할 때

보드는 전원 부팅 때 자동 실행되지만 PC의 USB 화면 연결은 다시 열어야 합니다. [팀원 접속 안내](deployment/pc-access/README.md)를 참고하세요. Mac 접속 도구는 보드 연결·부팅·서버 준비를 기다리고 접속을 복구합니다. 모델과 DB를 새 PC로 복사하지 않습니다. GitHub에는 접속 스크립트만 포함하며 Google Platform Tools 바이너리는 로컬 공유 ZIP에 별도 포함했습니다.
