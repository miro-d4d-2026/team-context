# 미로 전달: 화면 항목·기준·학습 역할

이 ZIP은 기존 코드의 업데이트 설치본이 아니라 **감시 스펙/출처/시연 입력/AI 선택 근거**입니다. 현재 웹과 `genguard-edge-v1`을 통째로 대체하지 마세요. `spec/*.json`은 신규 연구 스키마여서 기존 v1 모델 프로필 경로에 넣으면 안 됩니다.

## 먼저 연결할 파일

| 파일 | 웹에서의 용도 |
|---|---|
| `army-coverage.json` | 군 구매요구 충족 항목 체크, 계기/보호/검사 기록 페이지의 빠진 항목 확인 |
| `spec/field-catalog.json` | 카드/점검표 이름, 단위, 수집 방법, 학습/규칙/수동 구분 |
| `spec/reference-profiles.json` | 군 요구 참고와 Onan 실제 제품 참고를 분리한 기준 설명 |
| `sources-manufacturer.json` | 숫자의 문서/페이지/조건/미확정 상태를 보여주는 근거 패널 |
| `spec/demo-rules.json` | **시연용으로만** 적용할 규칙 후보. 별도 evaluator 어댑터 구현 필요 |
| `spec/demo-scenarios.json` | 기준값+변경값으로 재생할13개 시나리오. `hold_s`만큼 유지하는 흐름 설계 |
| `ai-study/AI_DECISION.md` | IF 선택과 기존 모델 유지 이유, 별도 합성 비교 결과 |

`expected_ui`는 요구하는 출력 설명이고, 모델을 실제 실행한 결과가 아닙니다. 모델 시나리오는 v1 runtime에 입력한 **실제 반환 점수/결과**로 표시해야 합니다. 시나리오 ID가 `combined`라는 이유만으로 'AI 감지'를 출력하면 안 됩니다.

## 화면을 바꿀 우선순위

1. **자산 프로필 선택:** `군10kW 구매요구 참고`와 `Cummins Onan 7.5HDKAL-1 시연` 분리. 이번 값 변경 시연은 Onan 선택. 군 요구를 Onan이 충족한다고 표시하지 않습니다.
2. **빠진 필수 항목:** 주파수, 누적 운전시간, 예열 상태, 배터리 충전계, 각 상/측정점, 기존 보호 경보와 출력 차단 상태 추가. 충전계의 실제 종류가 불명확하면 '정보 미확인'.
   - 전압·전류 각 표본에 상/선간, 계측 위치, 선택스위치 위치, 시각을 함께 저장합니다. 전역 스위치 위치만 저장하지 않습니다.
   - `reference_profile_id`가 현재 장비와 다르면 카탈로그의 Onan 범위를 카드 기본값으로 사용하지 않습니다.
   - 콘센트·운전반·스위치 종류 및 성능시험은 `army-coverage.json`에서 별도 자산/검사 기록으로 생성합니다.
3. **추가 상태:** 연료잔량/수위, 오일량·냉각수량, 부하, 외기온, 시동 이력, 정비이력. 센서가 없으면 미연결 또는 수동 점검으로 둡니다.
4. **3개 판단 표시:** `문서 기준`, `평소 패턴`, `데이터 상태`. 한 카드가 정상 범위 안이어도 패턴주의를 함께 표시할 수 있습니다.
5. **근거와 행동:** 값/단위/기준/지속시간/출처/점검후보 표시. '오일부족 확정', '베어링 고장85%', 'RUL37일' 같은 근거 없는 확정값은 출력하지 않습니다.
6. **시험·성능 기록:** THD/절연저항/상평형/소음/내구 등은 실시간 카드가 아니라 계측/검사 증빙으로 관리합니다.

## 실측·모의 표시 규칙

- 각 채널에 `source_kind`를 붙입니다: `live_imu`, `live_controller`, `scenario`, `manual`, `derived`.
- 화면 전체가 live라고 해서 온도/압력도 실측인 것처럼 보이면 안 됩니다. 현재는 진동만 `live_imu`, 온도·압력·전기값은 `scenario`입니다.
- 혼합 화면 상단: **실측 진동 + 모의 발전기 데이터**. 센서카드에는 각각 `실측`/`모의`/`수동`/`미연결` 배지.
- `derived`는 어떤 원천의 계산값인지 부모 채널과 계산식을 보존합니다. 주파수로 계산한RPM은 독립 측정 두 개가 아닙니다.
- 기준에도 `manufacturer_range` / `rated_value` / `procurement_requirement` / `learned_baseline` / `demo_assumption` 배지를 별도로 둡니다. 데이터가 모의인데 기준은 제조사 문서일 수 있습니다.
- 미연결은 `value:null`, `quality:missing`; 오래된 값은 마지막 시각과 함께 `stale`. 0으로 채우거나 초록 정상 처리하지 않습니다.
- 통합 판정 예: `점검 필요1 · 데이터 미수신2 · 관측 정상6`. 미관측까지 포함해 발전기 전체 정상이라고 하지 않습니다.

## 제안 결과 UI 예시 (계산된 모델 결과 아님)

```json
{
  "example_only": true,
  "data_domain": "hybrid_demo",
  "summary": "회전수 기준 이탈 없음. 냉각수·유압의 평소 패턴은 판정 대기.",
  "metrics": {
    "engine_rpm": {"value": 1800, "unit": "rpm", "source_kind": "scenario"},
    "coolant_c": {"value": 89, "unit": "degC", "source_kind": "scenario"},
    "oil_bar": {"value": 2.1, "unit": "bar", "source_kind": "scenario"},
    "vibration_rms_g": {"value": null, "unit": "g", "source_kind": "live_imu", "quality": "awaiting_sample"}
  },
  "assessment": {
    "reference_range": "within",
    "pattern_status": "awaiting_actual_model_result",
    "fault_component": null,
    "rul_hours": null
  }
}
```

위 예시는 실측값을 아직 받지 않은 상태입니다. 모델이 반환한 결과가 대기 중이면 **패턴 판정 대기**를 유지합니다. 실제 모델 결과를 이용한 구현은 기존 `INTERFACE.md`의 결과 형식을 따릅니다.

## 기존 edge v1과의 연결 범위

- 기존 CLI/SQLite/stream/API 연결은 이전 `genguard-edge-v1/MIRO_HANDOFF.md` 및 `docs/UNO_Q_CONNECTION.md`를 그대로 따릅니다.
- v1 기본 학습채널은 `engine_rpm`, `coolant_c`, `oil_bar`, `voltage_v`, `current_a`, `vibration_rms_g`, `envelope_cv`; 조건은 `load_pct`, `ambient_c`입니다.
- 이 패키지 추가 항목은 UI/점검/규칙 레이어부터 연결합니다. 주파수·배터리 등을 모델에 추가하려면 **새 profile/version/수집자료/모델 검증**이 필요합니다.
- `phase=steady`는 이 연구 스펙 표현입니다. v1은 `phase=running`으로 정상운전 분석하므로 어댑터에서 안정화가 확인된 구간만 매핑합니다. 엔진이 회전하기만 한다고 running을 넘기지 않습니다.
- v1의 `elapsed_s`는 수집 경과시간이며 실제 시동 경과시간으로 사용하지 않습니다.
- 최초 정상 기록 고정, 검토된 최근 정상만 후보 학습, 운전 후 학습/다음 운전 활성화 원칙을 유지합니다.
- 실측발전기 프로필에 모의 발전기 값이나 다른 엔진 공개 데이터를 정상으로 학습시키지 않습니다. 시연용 hybrid 프로필로만 결합합니다.
- 현재 v1은 IF가 아니라 조건부 회귀+잔차공분산입니다. 둘 다 머신러닝입니다. IF를 추가하는 작업은 아직 하지 않았습니다.

## 가장 먼저 할 인수 확인

1. 모의 기준에서 정상 표시, 1950rpm/65Hz 변경 후 규칙 편차가 표시되는지.
2. RPM1800 유지, 냉각수96℃ 변경 후 해당 카드와 종합 상태만 주의가 되는지.
3. 온도89℃/유압2.1bar에서 문서범위는 within을 유지하고, 학습 모델이 실제 반환한 변화 근거가 별도로 표시되는지.
4. 실제 IMU를 붙여 패턴 변경 후 실측 채널만 값이 갱신되는지. 모델 부착 기준이 동일한지.
5. 데이터 중단시 정상값이 고정되지 않고 판정불가가 되는지.
6. OEM 경보는 AI 정상점수보다 우선하며, 기계 제어는 하지 않는지.

이 검수는 기능 연결 확인입니다. 실제 발전기 고장 정확도/잔여수명 검증과 구분합니다.
