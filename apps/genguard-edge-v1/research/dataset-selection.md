# 공개 엔진 데이터 선택과 적용 범위

확인일: 2026-10-10. 사용자가 발전기 모델을 먼저 정하지 않아도 공개 데이터를 검색하고 실험용 후보를 고를 수 있다. 실제 설치 장비의 정상값·경보값·통신 레지스터는 해당 장비의 매뉴얼과 운전 이력으로 별도 확인해야 한다.

## 이번에 확보한 원본

[Marine Engine Fault — Zenodo](https://zenodo.org/records/19857425), BahooToroody 외, CC BY 4.0. [연관 저자 프리프린트](https://arxiv.org/abs/2607.19444).

Matsui MU323DGSC **선박용 디젤엔진 시험대**의 실제 관측이다. 국내 비상발전기나 군 발전기 데이터로 표현하지 않는다. 원본 ZIP, 라이선스, 저자 README, 변수 사전, 파일 인덱스, 출처 JSON을 `data/public/marine-engine-fault/`에 보존했다. ZIP의 제공 MD5와 다운로드 파일이 일치한다.

- 16개 데이터 CSV, 검사 결과 114,770행.
- 정상 reference 1개와 고장 실험 15개. 13개 고장 실험에는 고장 개입 전 구간도 있고, 인젝터 실험 2개는 전체가 이상 구간이다.
- `Anomaly State`: 0은 개입 전, 1은 개입 후. 미래 고장 시점이나 잔여수명 정답이 아니다.
- 샘플 간격은 대부분 1~2초이며 일정하지 않다. 균일 1Hz로 가정하지 않는다.

## 입력 매핑

| 앱/모델 키 | 원본 관측 | 단위·의미 |
|---|---|---|
| `engine_rpm` | Engine Speed | rpm |
| `ambient_c` | Engine room Temp. | °C, 기계실 공기 온도 |
| `load_pct` | 파일별 nominal load | %, 계측 전력으로 계산한 값이 아닌 실험 조건 메타데이터 |
| `coolant_c` | Cooling Water Temp. Engine Out I | °C, 특정 출구 위치 |
| `oil_temp_c` | LO Temp. Engine In | °C, 윤활유 입구 위치 |
| `oil_pressure_signal_v` | LO Circulating Pump Press. | **V, 보정 전 압력 센서 신호** |
| `coolant_pressure_signal_v` | Fresh Cooling Water Press. | **V, 보정 전 압력 센서 신호** |
| `shaft_power_kw` | Shaft Power | kW, 기계적 축출력; 발전 전력과 다름 |

IMU, 발전 출력 전압/전류, bar 단위 윤활유 압력은 이 자료에 없다. 변환기는 해당 입력을 `null / missing`으로 표시한다. 원시 압력 전압을 bar로 변환하지 않는다. 다른 실험의 IMU를 행 번호로 붙여 동시 계측인 것처럼 만들지 않는다.

원본 전수 검사에서 `LO Temp. Engine In = 999°C`가 842행 발견됐다(정상 prefix 196, 이상 348, reference 298). 저자 문서에 이 값의 정확한 코드 의미가 설명되지 않아 결측 코드라고 확정하지 않는다. 변환기는 숫자를 보존하면서 `quality: invalid`로 표시한다. 모델은 이 행을 정상 학습에 넣지 않고 평가 시 `unknown`으로 센다. 시험 결과를 좋게 만들기 위한 삭제·보간은 하지 않는다.

## 모델 결과를 보기 전에 고정한 분할

모델 학습, 경보 임계값 보정, 정상 검증, 최종 시험 사이에 **원본 파일을 공유하지 않는다**. 같은 파일의 정상 prefix로 학습한 뒤 고장 suffix를 독립 시험이라고 발표하지 않는다.

| 역할 | 원본 파일 그룹 | 사용하는 구간 |
|---|---|---|
| fit | AF 40/60/75/85%, AC 75/85% | 정상 prefix만 |
| calibration | AC 40/60% | 정상 prefix만 |
| validation | Turbine 40/60% | 정상 prefix만 |
| test | Turbine 85%, Pump 60/85% | 정상과 이상 모두 |
| 제외 | Reference, Injector 2개 | 원본·메타는 보존 |

이 분할은 알고리즘 점수를 보기 전에 파일별 채널·운전 조건을 점검해 정했다. 서로 다른 엔진으로의 일반화 시험은 아니다. 모든 기록은 동일 엔진의 실험이다. 테스트 종류도 냉각수 펌프·터빈 관련 실험으로 한정된다.

Reference는 행별 부하율이 명시되지 않았고, Injector 파일은 여러 부하가 섞여 있다. 이번 공통 부하 프로파일에서는 제외한다. 전체 archive를 읽을 수 있는 연구 로더는 제공한다.

파일 간 정상 온도·RPM에도 차이가 있다. 학습 범위를 벗어나는 운전 조건을 `unknown`으로 판단할 수 있다. `unknown`을 정상이나 성공한 고장 탐지에 포함하지 말고 별도 비율로 보고한다. 특히 Turbine 85%의 기계실 온도는 fit 정상 범위를 벗어날 수 있다. 이 현상을 숨기려고 시험 데이터로 재학습하거나 임계값을 조절하지 않는다.

## 실제 발전기와 IMU 통합에 대한 결론

이 데이터는 **실제 엔진의 여러 센서 관계를 학습·평가하는 연구 경로**에 쓴다. 우리 IMU와 발전기 컨트롤러가 동시 측정한 학습 데이터는 아직 없다. 통합 코드의 동작은 명시적 합성 시나리오로 시험하고, 실제 통합 정확도는 발전기 설치 후 별도 운전 회차로 검증해야 한다.

다음에 받아야 할 실제 데이터는 동일 장비·동일 운전 회차의 IMU, RPM, 부하, 냉각수/윤활유 온도, 가능하면 유압·전압·전류이며, 채널별 단위·측정 시각·품질·설치 위치를 함께 기록한다. 센서 채널이 다르면 프로파일과 모델도 분리한다.

## 함께 조사했지만 이번 학습에 넣지 않은 후보

| 후보 | 판단 |
|---|---|
| [Thermo King SE2.2 + 10kW 발전기 실험 논문](https://www.nature.com/articles/s41598-026-40979-5) | 온도·3축 진동·RPM·전기량 조합이 유사하다. 원자료는 저자에게 요청하는 방식이라 이번에 확보하지 못했다. 논문의 수치만으로 학습 데이터를 만들지 않는다. |
| [3500-DEFault](https://data.mendeley.com/datasets/k22zxz29kr/1) | 열역학 시뮬레이션의 실린더 압력·비틀림 진동 특징이다. 실제 발전기 컨트롤러+IMU 동시 관측으로 쓸 수 없다. |
| [CORA 회전기계 데이터](https://doi.org/10.34810/data2500), [저자 논문](https://www.nature.com/articles/s41597-026-07224-0) | 전동기/기어/DC발전기 시험대에서 진동·전류·온도·속도를 측정한 대안. 디젤엔진은 아니다. 저장소가 봇 확인 페이지를 반환하여 원본 파일·라이선스 검증을 완료하지 못했고 이번 패키지에서 사용하지 않았다. |

## 재현

```bash
python3 research/marine_dataset.py
python3 scripts/prepare_public.py
```

첫 명령은 원본 체크섬과 파일별 행·시간·단위·라벨을 검사한다. 두 번째는 gzip JSONL replay와 별도 정답 파일을 만든다. CSV 첫 행은 이전 샘플 간격이 없으므로 생략한다. `duration_s`는 전후 관측 간격이고, 관측값이 그 구간의 평균이라는 뜻은 아니다. `elapsed_s`는 고장 구간으로 넘어가도 원래 운전 시계를 유지한다.

API: `scripts/prepare_public.py`의 `frame_iter(zip_path, filename, segment)`와 `labeled_frame_iter(zip_path, filename)`. `segment`는 `normal`, `anomaly`, `reference` 중 하나다. 정답은 분할·사후 평가에만 사용하고 모델 입력 특징으로 넣지 않는다.
