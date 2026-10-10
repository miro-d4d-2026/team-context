# 진동 연동 모의 운전 API 추가

`POST /api/operation/linked-simulation`에 `{"enabled":true}` 또는 `{"enabled":false}`를 보냅니다. 켜기 전에 `mode=hybrid`를 선택해야 합니다. 응답은 기존 overview 전체입니다. 재시작 시 기본값은 꺼짐입니다.

활성 overview의 `linked_simulation`에는 `enabled`, `phase`, `phase_label`, `pattern`, `values`, `elapsed_s`, `history`, `message`, `assumptions`, `time_scale_note`가 있습니다. `history`는 초 단위 최근180점의 **모의** 값이며 수신 공백은 null입니다. 진동은 기존 `/api/imu/history` 실제 기록을 사용합니다.

`telemetry`의 `source=imu_driven_simulation`은 진동으로 구동하는 모의 값, `source=derived,input_source=imu_driven_simulation`은 그 값에서 계산한 전기 값입니다. 실제 센서는 계속 `live_imu`입니다. UI는 이 출처와 시연 속도를 표시해야 합니다. 연동 중 `current_run`은 수동 입력 복원용으로 보존되므로 화면의 현재값과 그래프는 `telemetry`/`linked_simulation`을 사용합니다.

이 모드의 수온·유압 상태는 모의 규칙 표시이며 이전 5분 구간 모델의 판단이 아닙니다. 진동은 기존 등록 기준과 비교합니다. 기록 저장·정상 학습은 허용하지 않습니다.

---

# v7 실시간 진동 추이 추가

기존 operation/센서 API는 그대로입니다. `GET /api/imu/history`가 아래값을 반환합니다.

```json
{"state":"fresh","source":"live_imu","stream_id":"imu-...","installation_id":"phone-fixed-...","age_s":0.4,
 "points":[{"timestamp_s":1791690000.0,"rms_g":0.055,"envelope_cv":0.015,"valid":true,"window_id":"imu-...:1","source":"live_imu"}],
 "baseline":{"ready":true,"rms_g":0.054},"limit":180}
```

최근180개의2초RMS/CV창이며 원시가속도파형이아닙니다. 새stream/부착/출처가오면이력을리셋하며, 중복창은추가하지않습니다. 무효창은null로차트를끊고, 5초이상수신중단은stale로보여줍니다. 저장한과거값이새실시간값으로바뀌지않습니다. 기준등록은기존POST `/api/imu/calibration/start` `{confirmed_normal:true}` 입니다.

---

# GenGuard v6 운전 구간 비교 API

현재 메인 화면은 아래 v6 API를 사용합니다. 문서 아래의 v2–v5 경로는 기존 센서·실험 기능을 위한 호환 API입니다. v6과 이전 API의 시계열 입력 구조는 다릅니다.

## v6 경로

- GET `/api/operation/overview`: 현재 분석과 전체34개 항목, 과거점검.
- POST `/api/operation/evaluate`: 아래 입력을 검증하고 관측구간까지만 분석.
- POST `/api/operation/save`: `{request_id:"save-unique-001",label:"정기점검",confirmed_normal:false}`. 같은 요청ID/내용은 중복저장하지 않습니다. 정상승인은 분석정상일때만 가능합니다.
- POST `/api/operation/retrain`: `{}`. 승인정상이력만새학습기준에사용합니다.

```json
{
  "mode": "scenario",
  "context": {"load_pct":62,"ambient_c":28.5,"engine_rpm":1800},
  "samples": [
    {"elapsed_s":0,"coolant_c":50,"oil_kgf_cm2":4.0,"vibration_rms_g":0.045},
    {"elapsed_s":300,"coolant_c":60,"oil_kgf_cm2":3.6,"vibration_rms_g":0.046},
    {"elapsed_s":600,"coolant_c":80,"oil_kgf_cm2":3.3,"vibration_rms_g":0.045}
  ],
  "selected_elapsed_s":600,
  "reference_id":null,
  "auxiliary":{"voltage_ll_v":208,"battery_v":13.8,"fuel_pct":70,"humidity_pct":47.7,"pf":0.8}
}
```

samples는 시동부터0,300,600…초의 누적 관측입니다. 시간순이 아니면422, 5분간격/초기점/필수값이 빠지면 해당 모델은비교보류입니다. 명시적null은결측, auxiliary생략값은모의초기값입니다. 실제센서값으로사용하지않습니다. 수온80°C라도5–10분+20°C가평소약+8°C보다빠르므로inspect가됩니다.

`mode:"hybrid"`는samples의vibration_rms_g를null/생략하고실제센서창을사용합니다. `mode:"replay"`는똑같이진동입력을비우고`recording_id:"random"|"training"`, `frame_index:0..47`을추가합니다. 실제진동웹주입은422, 혼합모드점검저장·재학습은거절합니다.

## 주요 반환 필드

- `schema_version: "genguard.operation-overview/1"`
- `current_run`: 설정한전체시간표. 아직관측하지않은구간이포함될수있으므로그대로현재그래프로그리지않습니다.
- `observed_run`: 선택한현재시점까지의실제분석입력. 그래프와저장은이접두기록을사용합니다.
- `operational_status`: 종합state/headline/summary/alerts/missing_active. `urgent > inspect > unavailable > normal` 순서로보호참고규칙과모델을합칩니다.
- `analysis.channels`: 항목별state,summary,method,current,interval,observed_delta,expected_delta,observed_rate,expected_rate,intervals,worst_interval. 진동rate는null이며크기비율을사용합니다.
- `analysis.model`: 알고리즘·학습기록수·출처·현재값학습제외여부.
- `analysis.reference_run`: 실제선택된과거정상기록. 원곡선은조건보정전값, 기대변화율은모델보정결과입니다.
- `telemetry`: 25계측+9수동점검. key,label,value,unit,state,source,source_label,method,note,basis. 미연결항목은null입니다.
- `vibration_replay`: 실측기록원본시각,원본구간,분류결과,평가용정답. 정답은분류입력에사용하지않습니다.
- `records`: 저장된점검. 초기8회는합성입니다.

서버인증·IMU수집·기준등록은아래기존계약을유지합니다. 브라우저동일출처호출이기본이며LAN노출시토큰이필요합니다.

---

# GenGuard hybrid API v2

모든 분석은 API 호스트에서 수행한다. `data_domain: hybrid_demo`는 IMU 실측 또는 기록 재생과 발전기 모의 입력의 혼합 시연이다. 원래 6채널 모의 모델을 실제 IMU에 직접 적용하지 않는다.

## 연결

- GET `/api/health`: 준비 상태, 모델 ID, active session.
- GET `/api/config`: fields(출처/단위/editable), default_values(모의 항목만), model_id.
- GET `/api/imu`: 최근 센서 창, 출처, age_s, 품질, 정상 기준 수집 진행.
- POST `/api/sessions` `{request_id: "unique-id"}` → `{session_id,next_seq:1,time_policy}`.
- POST `/api/sessions/{id}/samples`: 아래 웹 입력. 한 세션에 순서대로 보낸다.
- POST `/api/sessions/{id}/close` `{}`: 현재 브라우저가 시작한 시험 종료.
- POST `/api/imu/windows`: 어댑터 전용 IMU 창.
- POST `/api/imu/calibration/start` `{confirmed_normal:true}`: 현재 진동을 기준으로 선택, 이후 새로운 고유 창 15개를 수집한다.

토큰이 설정되어 있으면 API에 `Authorization: Bearer ...`가 필요하다. LAN 바인딩은 공백 없는 16자 이상 ASCII 토큰 필수. 별도 웹은 정확한 origin을 `--allow-origin`으로 허용한다. GET health와 정적 화면은 공개이며 상태 DB/모델 파일은 HTTP 다운로드 경로가 없다.

## 웹 입력

```json
{
  "seq": 1,
  "source": "hybrid_demo",
  "phase": "running",
  "values": {
    "engine_rpm": 1800, "coolant_c": 80, "oil_kgf_cm2": 3.2,
    "voltage_ll_v": 208, "frequency_hz": 60, "load_pct": 62,
    "ambient_c": 28.5, "battery_v": 13.8, "fuel_pct": 70, "pf": 0.8
  },
  "quality": {}
}
```

`vibration_rms_g`, `envelope_cv`를 웹 values/quality에 넣으면 **422**다. 서버가 가장 최근 유효한 센서 창을 선택한다. 모의 품질은 `{coolant_c:{quality:"stale",age_s:6}}` 형식. 누락은 null/생략이며 정상 기본값으로 채우지 않는다. 유압은 웹의 kgf/cm²에서 내부 bar로 ×0.980665 변환한다.

## 어댑터 IMU 창

```json
{
  "schema_version": "genguard.imu.window/1",
  "source": "live_imu",
  "stream_id": "imu-unique-connection-id",
  "installation_id": "phone-mount-001",
  "window_seq": 1,
  "duration_s": 2,
  "sample_count": 416,
  "sample_rate_hz": 208,
  "configured_sample_rate_hz": 208,
  "accel_range_g": 4,
  "measured_at": "2026-10-11T01:00:00.000Z",
  "features": {"vibration_rms_g": 0.0348, "envelope_cv": 0.41},
  "quality": {"valid": true, "flags": []}
}
```

위 시각·숫자는 형식 예시이며 전송 시 현재 계측 종료 시각을 사용한다. 기록 파일 재생은 `recorded_imu`이며 이때 measured_at은 재생 시각이다. 실측으로 재라벨하지 않는다. 유효하지 않은 창은 quality.valid=false와 flags를 보내고 두 특징값에 null을 허용한다. invalid 창도 수신 상태를 즉시 무효로 바꾼다.

응답: `{id:"stream-id:1",accepted:true,duplicate:false}`. 중복 body는 duplicate:true, 동일 ID의 다른 body는409. 중복을 보내도 수신 시각을 갱신하지 않는다. 다른 stream/source/installation/range/rate는 새 정상 기준을 요구한다. 동일 stream 안의 메타 변경은409다. 5초를 넘긴 과거 계측과 2초 넘는 미래 시각은422다.

## 기준 등록

현재 창이 fresh일 때만 등록을 시작한다. 이후 약30초 동안15개의 새로운 연속 창을 받아10개로 학습하고5개로 일관성을 검증한다. 수집 중 결손·단절·무효 품질·기준 변화가 생기면 실패한다. 매우 약한 진동 또는 불안정한 학습 데이터도 거절한다.

GET imu의 `calibration.state`: untrained / collecting / ready / failed. `count`, `required`, `baseline_id`, `error`가 포함된다. 실제 고장 라벨을 학습하는 기능이 아니라 현재 부착 상태의 정상 진동 분포 학습이다. 정상 여부는 사용자가 지정한 실험 조건을 따른다.

## 분석 응답

```json
{
  "schema_version": "genguard.bridge.result/2",
  "session_id": "bridge-server-generated",
  "seq": 1,
  "elapsed_s": 32.1,
  "analyzed_at_epoch_s": 1791674400.0,
  "analysis_age_s": 0,
  "data_domain": "hybrid_demo",
  "is_demonstration": true,
  "duplicate": false,
  "status": "normal",
  "imu": {"state":"fresh","source":"live_imu","age_s":0.3,"window_id":"imu-id:17","calibration":{"state":"ready"}},
  "provenance": {"vibration_rms_g":{"source":"live_imu"},"coolant_c":{"source":"scenario"}},
  "ai": {
    "state":"within_baseline","persistent_change":false,
    "components":{"generator":{"source":"scenario"},"vibration":{"source":"live_imu"}},
    "current_comparison":{"score":null,"threshold":null,"evidence":[]},
    "checks":[],"summary":"각 기준 대비 판단 설명"
  },
  "fixed": {"metrics":{},"coverage":{},"alerts":[]},
  "alerts": [],
  "notification_events": []
}
```

위 JSON은 축약 예시다. 실제 응답에는 각 component의 state/score/threshold/change_streak/persistent_change/evidence와 품질·규칙 근거가 들어 있다. 두 모델 점수를 하나의 고장 확률로 바꾸지 않는다. component state는 within_baseline/change/unknown이며 통합 ai state는 within_baseline/change_observed/persistent_change/unknown이다.

전체 표시에는 **result.status**를 사용한다. 유효한 긴급 규칙 > 점검 경고 > 일부 판단 불가 > 비교 기준 내 순서다. 비운전은inactive. 진동 미학습/미수신을 정상으로 표시하지 않는다. 진동 지속 판단은 새로운 sensor window ID가 있을 때만 증가하며, 발전기 패턴은 서버의 서로 다른2초 비교 구간에서 확인한다. 둘 다 연속3창 조건이다.

`ai.current_comparison.evidence` 각 항목에 source가 있으며, 실측 진동 기대값은 현재 등록 기준, 모의 발전기 기대값은 기존 모델에서 나온다. 전류·출력은 모의 입력의 계산값이다. 실제 발전기와의 동기화·진동/수온 사이 상관관계 학습·잔여수명·제어 명령은 없다.

재시도는 원래 seq/body로 한다. 성공한 요청은 센서 창까지 원래 분석 결과를 돌려주고 새로운 알림을 내지 않는다. **duplicate의 analysis_age_s는 원래 분석 이후 실제 지난 시간**이다. 웹은 이를 응답 나이에 더하고 5초 넘은 결과를 현재 정상으로 표시하지 않는다. result.imu.age_s 역시 원래 분석 당시의 센서 나이이므로 analysis_age_s를 더해 표시한다. 같은 요청 재전송이 새로운 센서 분석은 아니다.

알림은 `notification_events`의 ID로 중복 제거한다. 이미 본 알림은 이력으로 남기고 현재 상태와 구분한다. 서버 재시작 후에는 새 시험·센서 수신·정상 기준 등록이 필요하다.


## v3 추세 실험 확장

기존 result/2 계약을 유지하고 `trend`와 `trend_notification_events`를 추가합니다. 후자는 실험용 이벤트이며 기존 상태·경보를 대체하지 않습니다. 중복 요청에서는 새 이벤트를 다시 보내지 않습니다.

`GET /api/trend/catalog`는 모의 시나리오 목록과 정책을 반환합니다. `POST /api/trend/experiment`는 `{"scenario_id":"coolant_drift"}`처럼 등록된 ID만 받습니다. 정상 세션이나 실측 IMU 상태를 변경하지 않습니다. 인증·출처 제한은 기존 API와 동일합니다.

실험 응답 `rows`는 모의 시간 기준으로 순차 분석한 `{t_s,values,trend,current_anomaly,fixed_warning}` 배열입니다. 브라우저가 미래 관측값을 미리 그리지 않도록 현재 재생 행까지 사용합니다. `comparison`에는 최초 경고 시각과 기존 탐지 대비 추가 시간을 제공하며 음수는 추세가 늦었다는 뜻입니다. `evaluation`은 학습/보정과 분리한 합성 운전 평가입니다.

`trend`의 source는 scenario입니다. `channels`의 method가 linear이면 단순 직선 추세를 사용한 것입니다. Ridge 계수는 학습됐지만 현재 선택되지 않았습니다. 학습 완료와 채택은 구분합니다.

## v4 점검 비교 대시보드

루트 `/`는 새 점검 비교 화면입니다. 기존 센서 설정/추세 실험은 `/lab`에 보존했습니다. 기존 API 계약은 유지합니다.

### GET /api/inspections/overview

`equipment`, `records`(과거 기록), `current`, `comparison`, `operational_status`, `fixed`, `imu`, `imu_analysis`, `mode`, `defaults`를 반환합니다. 전체 배너는 `operational_status`를 사용해 보호 참고값과 독립 실측 센서 권고를 누락하지 않습니다. `comparison`만 보고 전체가 정상이라고 표시하면 안 됩니다.

### POST /api/inspections/compare

```json
{"mode":"scenario","reference_id":null,"values":{"coolant_c":87.2,"oil_kgf_cm2":3.19,"vibration_rms_g":0.046,"load_pct":62,"ambient_c":28.5,"engine_rpm":1800,"elapsed_s":600}}
```

`reference_id:null`은 조건에 맞는 최신 승인 정상 점검을 선택합니다. 지정된 기록이 미승인·조건 불일치라면 대체 기록으로 몰래 바꾸지 않고 비교 불가 처리합니다. `comparison.channels`에는 현재값·이전값·보정 전 차이·보정 후 차이·학습 기대값·출처·판단이 들어갑니다. 누락은null/비교 불가이며 정상0으로 채우지 않습니다.

`mode:"hybrid"`에서는 `vibration_rms_g` 키를 보내지 않습니다. 서버가 IMU를 읽으며 웹의 진동 덮어쓰기는422입니다. 현재 모의 운전 조건에서 실측 진동의 과거 발전기 비교는 보류하고 `imu_analysis`의 기존 설치 기준 결과를 별도로 표시합니다. 출처와 설치가 같은 기록끼리만 비교합니다.

### POST /api/inspections/save

위 본문에 `request_id`(UUID), `label`(선택), `confirmed_normal`(기본false)을 추가합니다. 먼저 과거 기준으로 판단한 후 기록을 저장합니다. 응답의 `saved_record`와`records`로 목록을 갱신합니다. 같은request_id·본문은 같은결과, 본문변경은409입니다. 이상/비교불가/고정경계이탈은 정상학습용 승인을 거절하지만 관찰기록 저장은 허용합니다. 결과미확인 네트워크실패는 같은request_id로 다시요청합니다. 명확한422거절 후 수정저장은 새request_id를 사용합니다.

### POST /api/inspections/retrain

본문 `{}`. 정상 승인된 과거 기록으로 후보 모델을 만들고 계수를 검증·저장한 다음 활성화합니다. 학습 JSON은 서버상태폴더의 `inspection-model.json`입니다. 현재 화면에 표시 중인 저장기록 자체는 해당 기록의 재평가 때 학습에서 제외하며, 그다음 새 측정부터 과거 정상 이력으로 사용됩니다.

학습/추론은 API 서버에서 이루어집니다. 초기 이력과 발전기 입력은 모의입니다. 실제 장비제어·고장원인 확정·잔여수명은 제공하지 않습니다. 실측 장비 조건과 승인 이력이 없는 물리IMU의 조건 보정 학습은 활성화하지 않습니다.


## v5 실측 진동 재생

`GET /api/vibration/catalog`: 기록 목록, 학습 범위, 랜덤 기록의 별도 평가 결과를 반환합니다.

`POST /api/vibration/replay`:

```json
{"recording_id":"random","frame_index":0,"reference_id":null,"values":{"coolant_c":80.2,"oil_kgf_cm2":3.19,"load_pct":62,"ambient_c":28.5,"engine_rpm":1800,"elapsed_s":600}}
```

`recording_id`는 `training` 또는 `random`, `frame_index`는 0~47 정수입니다. 진동 특징·정답·예측 라벨을 클라이언트에서 보내지 않습니다. `vibration_rms_g`를 values에 넣으면422입니다. 서버가 등록 기록의 RMS·세기변동 CV를 읽고 학습 모델로 추론합니다. 정답은 추론 후 별도 평가값에만 사용됩니다.

응답은 점검 overview 구조에 `mode:replay`, `vibration_replay`를 추가합니다. `vibration_replay.prediction`은 label/title/state/summary/checks/model_id, `evaluation`은 expected_label/correct입니다. 평가 정답은 접힌 검증 화면에서만 사용합니다. `recorded_at`은 원본 실험 시작 시각, `start_s/end_s`는 실험 시작 이후 구간, `analyzed_at`은 이번 추론 시각입니다.

메인 배너는 `operational_status`를 사용합니다. 보호 참고 경계 긴급 > 수온/유압 변화 및 진동 점검 > 무진동·비교 불가 > 기준 유사의 순서입니다. 진동 카드는 `vibration_replay.prediction`을 표시하고 발전기 과거 진동과 가짜 차이를 만들지 않습니다. `comparison`의 진동 채널은 출처가 맞지 않아 계속 비교 불가입니다.

재생 중 `/api/inspections/save`와 `/retrain`은409입니다. 모의 운전 또는 실제 IMU 모드로 바꾸면 기존 기능을 그대로 사용합니다. 재생→IMU 전환 시 실측 입력이 없다면 결측이며 재생값을 재활용하지 않습니다.
