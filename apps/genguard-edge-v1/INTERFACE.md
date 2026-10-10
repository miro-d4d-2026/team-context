# 구현 계약 (v1)

이 패키지는 기존 contracts/v1을 수정하지 않는 별도 다변량 분석 경로다.

## 입력 창

```json
{
  "schema_version": "genguard.edge.window/1",
  "asset_id": "GEN-DEMO-001",
  "session_id": "run-001",
  "installation_id": "fixture-v1",
  "seq": 1,
  "elapsed_s": 2.0,
  "duration_s": 2.0,
  "phase": "running",
  "measured_at": null,
  "readings": {
    "load_pct": {"value": 50.0, "unit": "%", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "ambient_c": {"value": 25.0, "unit": "degC", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "engine_rpm": {"value": 1800.0, "unit": "rpm", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "coolant_c": {"value": 80.0, "unit": "degC", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "oil_bar": {"value": 3.0, "unit": "bar", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "voltage_v": {"value": 240.0, "unit": "V", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "current_a": {"value": 15.0, "unit": "A", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "vibration_rms_g": {"value": 0.03, "unit": "g", "source": "scenario", "age_s": 0.0, "quality": "ok"},
    "envelope_cv": {"value": 0.2, "unit": "1", "source": "scenario", "age_s": 0.0, "quality": "ok"}
  }
}
```

`elapsed_s`는 해당 운전 세션 시작 후 창 종료 시각. `duration_s`는 분석 창 길이. 같은 세션 내 seq와 elapsed_s는 증가해야 한다. 결측은 value:null/quality:missing; 0으로 보충하지 않는다. phase: starting/warming_up/running/stopped. 측정 UTC를 모르면 null. source: live_imu/live_controller/recorded_imu/public_replay/scenario. quality: ok/missing/invalid. 단위·품질·시각·설치 ID가 입력 계약이다.

`vibration_rms_g = sqrt(var(ax)+var(ay)+var(az))`, 각 창에서 축 평균 제거, 모집단 분산. 기존 contracts/v1 rms_g와 sqrt(3) 차이가 있으므로 그대로 재사용하지 않는다. envelope_cv: 창 시작 기준 0.1초 bin, bin마다 축 평균 제거 RMS, bin당 5샘플 이상; bin RMS의 모집단 표준편차/평균(분모 floor 1e-9).

## Python 공개 API (core.py)

```python
from genguard.core import EdgeRuntime
runtime = EdgeRuntime(db_path, profile_path)
runtime.start_session(session_id, installation_id)
result = runtime.ingest(frame)  # 검증, 현재 고정 모델로 분석, SQLite에 원자 저장
runtime.close_session(session_id)
runtime.review_session(session_id, "normal", "reviewer-name", "점검 근거")
runtime.close()
```

입력 품질 불량은 unknown으로 저장한다. 형식·asset/session ID·시간 역행은 ValueError로 거부한다. 같은 seq/동일내용 재전송은 저장된 결과 반환, 다른내용은 충돌 오류. 현재 세션의 모델 버전은 시작 시 고정한다. 학습은 별도 CLI 프로세스로 수행한다.

## 보드 수집 연결

기존 gateway `127.0.0.1:9877`을 읽는 새 어댑터 `genguard/stream.py`가 2초 창을 만든다. generator 컨트롤러 값은 보드 로컬의 최신 JSON snapshot으로 전달한다. snapshot 형식:

```json
{"monotonic_s":12345.0,"phase":"running","readings":{"load_pct":{"value":50,"unit":"%","source":"live_controller","quality":"ok"}}}
```

`monotonic_s`는 UNO Q의 time.monotonic() 시계로 snapshot 작성 시점. 각 reading의 선택적 age_s는 그 시점에서 센서값 나이이며 어댑터가 현재까지의 경과를 더한다. 이를 통신 도착 시각만으로 실제 계측 동기화가 보장된 것으로 표현하지 않는다. snapshot은 임시파일+os.replace로 저장. 컨트롤러 실제 레지스터/CAN ID는 제공되지 않았으므로 하드웨어 어댑터는 미구현이다.
