# 센서 원시 데이터와 분석 결과 계약

버전 `1.0.0`. 팀원은 아래 두 JSON 형식으로 개발한다. 필드 구조는 JSON Schema로, 값 사이의 관계는 `validate_contracts.py`로 함께 검사한다. 기존 계획의 초안 필드명보다 이 계약이 우선한다.

**원시 데이터는 센서 수집 프로그램이 만들고, 분석 결과는 분석 함수와 저장 런타임이 함께 만든다. 관제 화면은 분석 결과를 읽는다.** 원시 파형을 매번 관제 서버에 보내지는 않는다.

```text
센서 수집 → raw_window → 분석 함수 → analysis_result → 로컬 DB → 관제
XJTU 특징 파일 → RUL 추론 → analysis_result → 공개 데이터 평가 화면
```

## 바로 사용할 파일

| 파일 | 용도 |
| --- | --- |
| [raw-window.schema.json](raw-window.schema.json) | 원시 IMU 메시지의 필수 필드·타입·허용값 |
| [analysis-result.schema.json](analysis-result.schema.json) | 분석 결과 메시지의 필수 필드·타입·허용값 |
| [raw-window-small.json](examples/raw-window-small.json) | 구조를 읽기 위한 8샘플 원시 예시 |
| [raw-window-2s.json](examples/raw-window-2s.json) | 416Hz 설정, 832샘플 합성 파형 |
| [result-warning.json](examples/result-warning.json) | 위 2초 파형과 RMS 수치가 대응하는 경고 결과 |
| [result-normal.json](examples/result-normal.json) | 기준 범위 안인 화면 예시 |
| [result-watch.json](examples/result-watch.json) | 변화 관찰 화면 예시 |
| [result-calibrating.json](examples/result-calibrating.json) | 기준 수집 중, 기준값·비율이 null인 예시 |
| [result-invalid.json](examples/result-invalid.json) | 수집 품질 불량으로 판정할 수 없는 예시 |
| [result-rul-example.json](examples/result-rul-example.json) | 공개 베어링 RUL 화면용 형식 예시 |

**예시 8개는 모두 `is_example: true`인 합성 자료다.** 실측·실제 모델 출력·성능 결과가 아니다. RUL 예시의 숫자도 설명용이다. 정상·관찰·경고 파일은 각각 독립적인 UI 예시이며 파일 순서대로 진행된 회복 시나리오가 아니다. 작은 원시 예시는 통신 형식 확인용으로, 실제 분석에 필요한 구간 길이를 충족한다는 뜻이 아니다.

## 공통 규칙

- UTF-8 JSON 객체 하나를 메시지 하나로 사용한다. 파일에 연속 저장할 때는 객체 한 개를 한 줄에 담는 NDJSON을 사용한다. 예시 파일은 읽기 편하게 여러 줄로 저장했다.
- `schema_version`은 `1.0.0`으로 고정한다. 키 이름이나 단위·타입을 개인별로 바꾸지 않는다. 알려지지 않은 필드는 거부한다.
- `node_id`는 노드 영구 ID, `asset_id`는 점검 대상 ID다. 영숫자·밑줄·하이픈만 사용하고 숫자로 바꾸지 않는다. 한 실물 노드에는 이번 버전에서 IMU 1개를 연결한다.
- `boot_id`는 수집 세션 시작 시 UUID를 새로 만든다. MCU 또는 수집 앱이 재시작해 시간·순번이 초기화되면 새 세션으로 취급한다. 다른 세션의 샘플을 한 창에 섞지 않는다.
- `source_kind`는 `live_imu`, `xjtu_replay`, `scenario`로 구분한다. 원시 IMU 메시지는 `live_imu`와 `scenario`만 허용한다. XJTU의 두 축 자료는 별도 어댑터로 특징을 읽어 분석 결과 형식으로 보낸다.
- `source_ref`는 입력의 출처다. 실물은 설치·수집 세션 이름, 시나리오는 시나리오 이름, XJTU는 `XJTU-SY/Bearing1_5/record-31`처럼 쓴다.
- 실제 수집·실제 평가 결과는 `is_example: false`로 만든다. 예시 파일을 그대로 흘려보낼 때는 true를 유지한다. 이 값이 true이거나 source_kind가 scenario이면 실제 성능 평가 자료에 포함하지 않는다.
- 가속도는 `g`로 통일한다. `1 g = 9.80665 m/s²`다. ADC 정수값이나 m/s² 값을 g인 것처럼 보내지 않는다. 단위 변환은 센서 어댑터의 책임이다.
- UTC는 `2026-10-10T06:00:00.000Z` 형식이다. 시계가 맞지 않으면 `measured_at: null`을 보낸다. null을 1970년이나 현재 수신 시각으로 바꿔 채우지 않는다. 화면만 한국 시간으로 변환한다.
- 모르는 값은 `null`이다. `0`은 실제로 측정·계산한 0을 뜻한다. NaN, Infinity, 중복 JSON 키는 금지한다.
- 모든 순번과 마이크로초 값은 JavaScript가 정확히 표현할 수 있는 정수 범위 안으로 제한한다. 한 원시 창은 최대 50,000샘플이다.

## 원시 데이터 필드

| 필드 | 의미와 생성 주체 |
| --- | --- |
| `message_type` | `raw_window` 고정 |
| `sensor_id` | 연결한 IMU의 식별자. 센서 교체 시 갱신 |
| `window_seq` | 수집 세션 안에서 창마다 증가하는 1부터 시작하는 순번. 재전송 때 유지 |
| `measured_at` | 창의 첫 샘플 측정 UTC 또는 null |
| `monotonic_start_us` | 첫 샘플의 수집 세션 기준 단조 증가 시간. 마이크로초. UTC와 무관 |
| `axis_order` | `["x", "y", "z"]` 고정. 센서 자체 좌표축이며 지구 좌표가 아님 |
| `gravity_removed` | false 고정. 어댑터에서 중력·평균을 제거하지 않고 변환된 가속도를 전달 |
| `full_scale_g` | 실제 설정한 ±범위: 2, 4, 8, 16 중 하나 |
| `configured_sample_rate_hz` | 센서에 설정한 출력률 |
| `sample_rate_hz` | 전달받은 샘플 시각으로 계산한 유효 평균 수집률 |
| `sample_count` | 실제 전달한 samples 행 수. 2 이상 |
| `quality.dropped_samples` | 창 안의 유실 수. 정확히 모르면 null. 기대 주기만으로 임의의 0을 넣지 않음 |
| `quality.clipped_samples` | 하나 이상의 축이 센서 범위에 걸린 행 수. 축 개수 합산이 아님. 모르면 null |
| `quality.timestamps` | 실제 장치·수집 시계에서 얻은 시각은 measured, 설정 주기로 복원한 시각은 reconstructed |
| `samples` | 각 행은 `[offset_us, ax_g, ay_g, az_g]`. 첫 샘플 기준 상대 시간과 3축 값 |

`samples[0][0]`은 0이며 이후 상대 시간은 엄격히 증가한다. 예를 들어 `[2404, 0.0568, 0.0329, 1.0187]`은 첫 샘플에서 2,404마이크로초 뒤의 세 축 가속도다. 수치는 설명용이다.

`sample_rate_hz = (sample_count − 1) × 1,000,000 / (마지막 offset_us − 첫 offset_us)`로 계산한다. 검증기는 0.1% 오차까지 허용한다. 이 평균 수집률만으로 균일 샘플링이나 FFT의 적합성이 보장되지는 않는다. 분석기는 누락과 시간 간격의 변동을 따로 확인한다.

MCU가 매 샘플을 JSON 문자열로 만들 필요는 없다. MCU는 버퍼로 전달하고 Linux 수집 어댑터가 이 계약의 창을 구성해도 된다. Bridge로 보내는 작은 묶음과 분석용 창은 달라도 되며, FIFO 넘침·샘플 순서·시각을 보존해야 한다. 센서 응답이 없으면 0으로 채운 가짜 창 대신 분석 결과의 판정 불가 상태를 만든다.

## 분석 결과 필드

| 필드 | 의미와 생성 주체 |
| --- | --- |
| `message_type` | `analysis_result` 고정 |
| `record_seq` | 노드 전체에서 재시작 후에도 증가하는 영구 순번. 저장 런타임이 DB 트랜잭션에서 배정 |
| `node_id`, `asset_id`, `boot_id`, 출처·시각 | 실물 분석에서는 입력 창에서 그대로 복사 |
| `input_window` | 입력의 window_seq, sample_count, sample_rate_hz. 센서 무응답이면 null |
| `features.axes` | 실물·시나리오는 xyz, XJTU는 xy |
| `features.rms_g` | 축별 평균 제거 후 축별 분산의 평균에 제곱근을 취한 값 |
| `features.rms_ratio` | 현재 rms_g / baseline.rms_g. 기준이 없거나 계산 불가하면 null |
| `features.kurtosis` | 축별 Pearson 첨도의 평균. Gaussian 기준 3이며 excess 첨도 아님. 필요한 축의 분산이 0이면 null |
| `features.dominant_frequency_hz` | 평균 제거·Hann 창 적용 후 축별 전력 합이 최대인 비DC FFT 빈의 주파수. 유효 대역·시간 품질 확인 전에는 null |
| `baseline.id`, `baseline.rms_g` | 등록한 기준 버전과 RMS. 둘 다 값이 있거나 둘 다 null |
| `health_state`, `reason_codes` | 상태 enum과 아래 표의 이유 코드. 화면은 코드를 한글로 바꿈 |
| `quality_state`, `quality_flags` | 입력 품질 판정. ok면 flags는 빈 배열, invalid면 이유가 하나 이상 |
| `model_version` | 실제 적용한 판정 규칙 또는 모델·전처리 버전. 예: rms-rule-v1 |
| `warning_event_id` | 확정 경고 에피소드의 UUID. 같은 경고가 이어지면 유지. warning 외에는 null |
| `rul` | 실물에서는 null. 공개 베어링 평가에서만 값·분 단위·대상·모델·평가 ID를 함께 전달 |
| `additional_measurements` | 표면 온도·전류·오일 레벨의 예약 필드. 이번 버전에서는 모두 null만 허용 |

세 축 RMS는 `sqrt((rms_x² + rms_y² + rms_z²) / 3)`이다. 벡터 크기의 RMS나 축별 RMS의 단순 평균과 다르다. XJTU 두 축은 분모가 2다. 표본 분산의 N−1 보정은 하지 않는다. **저장할 기준 RMS도 같은 정의와 축을 사용해야 한다.**

FFT 값은 샘플링 속도의 절반 미만이어야 하며 실제 센서·필터의 유효 대역 안에서 계산한다. 정의상 지배 주파수 하나만으로 고장 원인을 확정하지 않는다. 이 요약 필드들은 연결용이며 모델 내부의 추가 특징 벡터 전체를 메시지에 넣을 필요는 없다.

| health_state | 화면 문구 | 이유 코드 |
| --- | --- | --- |
| `calibrating` | 기준 수집 중 | baseline_collecting |
| `normal` | 기준 범위 내 | within_baseline |
| `watch` | 진동 변화 관찰 | rms_elevated |
| `warning` | 반복 변화로 점검 권고 | persistent_change, 필요하면 rms_elevated |
| `unknown` | 판정 불가 | poor_signal, baseline_unstable, model_not_ready, insufficient_history 중 해당 이유 |

품질 flag는 `sample_loss`, `clipping`, `timing_unverified`, `sensor_missing`, `quality_unknown`, `insufficient_samples`다. 첫 버전은 유실·포화가 있거나 그 수를 모르거나 시각을 설정 주기로 복원했다면 품질을 invalid로 보내고 건강 상태는 unknown으로 둔다. 데이터 품질에 자신이 없는데 정상으로 표시하는 것을 막는 보수적인 초기 정책이다. 검증 후 다른 정책이 필요하면 계약·설정을 함께 갱신한다.

센서 무응답의 경우 input_window는 null, quality_state는 invalid, quality_flags에 sensor_missing, reason_codes에 poor_signal, health_state는 unknown이다. 기준 학습 중에는 baseline의 두 값과 rms_ratio가 null이다. RMS가 0에 가까워 기준 비율을 안정적으로 계산할 수 없으면 기준을 등록하지 않는다.

**통신 두절은 health_state에 넣지 않는다.** 관제 서버가 마지막 수신 시각과 별도 heartbeat로 판단한다. 노드는 통신이 없어도 계속 분석할 수 있다. sensor_missing은 노드가 살아 있지만 센서 입력을 못 얻는 상황이다. unknown이나 통신 두절을 기존 경고의 해결로 처리하지 않는다.

## 영구 기록과 재전송 규칙

원시 창의 식별키는 `(node_id, boot_id, window_seq)`, 분석 결과의 식별키는 `(node_id, record_seq)`다. 분석 함수는 특징·판정·기준 정보를 만들고, 저장 런타임이 식별 정보와 함께 최종 객체를 구성한다. record_seq는 측정 완료 순서대로 하나의 작성 경로에서 배정한다. 이전 창을 나중에 재분석하려면 별도 replay node_id를 사용해 운영 상태를 덮어쓰지 않는다.

노드 DB에서 record_seq 증가와 결과·미전송 큐 저장을 하나의 트랜잭션으로 처리한다. 재부팅 시 record_seq를 1로 돌리지 않는다. DB를 완전히 초기화했다면 node_id를 새로 발급해 이전 기록과 충돌하지 않게 한다.

재전송할 때는 **기록 ID, 측정 시각, 내용 모두 그대로** 보낸다. 서버는 동일 키·동일 내용이면 중복으로 확인 응답하고, 동일 키에 다른 내용이 오면 충돌로 거부한다. 네트워크 장애 상태를 기록에 덧붙여 원본 내용을 변경하지 않는다. 수신 시각·재시도 횟수·전송 상태는 별도 저장한다.

서버가 영구 저장한 뒤에만 성공 응답한다. 최신 장비 상태는 해당 node_id의 가장 큰 record_seq로 결정한다. 부팅 UUID의 문자열 순서, 부팅별 window_seq, 수신 시각으로 최신 여부를 결정하지 않는다. 한 장비에 노드를 교체한 경우 현재 담당 node_id를 장비 설정에서 명시적으로 바꾼다.

전송 API의 첫 제안은 `POST /api/v1/records`에 분석 결과 하나를 보내는 방식이다. 저장 확인 응답 예시는 아래와 같다. 이 응답은 두 핵심 메시지 스키마의 대상이 아니라 저장 담당자가 구현할 API 동작 예시다.

```json
{
  "node_id": "example-node-01",
  "record_seq": 1042,
  "status": "stored",
  "received_at": "2026-10-10T06:02:10.000Z"
}
```

같은 내용의 재전송에는 status를 duplicate로 보내도 된다. 정상 저장과 동일 중복은 HTTP 200, 같은 ID의 다른 내용은 409로 처리하는 안이다. 노드는 stored 또는 duplicate 확인을 받은 기록만 전송 완료로 표시한다. 이 문서로 수신 서버나 동기화 기능 자체가 구현된 것은 아니다.

## RUL 필드와 공개 데이터 시간

RUL 객체는 source_kind가 xjtu_replay일 때만 허용한다. target은 bearing_test_endpoint, unit은 min, dataset은 XJTU-SY로 고정한다. bearing_id, as_of_minute, model_version, evaluation_id, validation_scope를 함께 보낸다. 아직 평가된 예측값이 없으면 rul은 null이다.

XJTU 기록 번호는 1부터, 경과 분은 0부터 시작한다. record-31은 as_of_minute 30이다. window_seq에는 원래 CSV 기록 번호를 넣고 source_ref에도 같은 번호를 쓴다. 순번을 하나 건너뛰어도 과거 기록을 삭제·재번호 매기지 않는다. 배속 재생의 몇 초와 원래 실험의 몇 분을 섞지 않는다. 원 실험의 UTC 시각을 모르면 measured_at은 null이다.

RUL 정답이나 마지막 기록 번호는 이 추론 메시지에 넣지 않는다. 평가기가 별도 파일에서 보관한다. evaluation_id는 그 평가 파일과 연결하는 ID다. 이전에 살펴본 개체를 사용하는 이번 평가는 validation_scope exploratory로 표시한다. 생성된 예시의 20.5분은 실제 예측 결과가 아니며 평가표에 사용하지 않는다.

## 검사 실행

Python 3.10 이상에서 프로젝트 루트를 기준으로 실행한다. 설치는 작업용 가상환경 안에서 한다.

```sh
python3 -m venv .venv-contracts
.venv-contracts/bin/python -m pip install -r contracts/v1/requirements.txt
.venv-contracts/bin/python contracts/v1/validate_contracts.py
.venv-contracts/bin/python contracts/v1/validate_contracts.py --pair contracts/v1/examples/raw-window-2s.json contracts/v1/examples/result-warning.json
.venv-contracts/bin/python -m unittest discover -s contracts/v1 -p 'test_*.py' -v
```

첫 명령은 예시 전체를, 두 번째 검사는 연결된 원시 창과 분석 결과의 출처·순번·품질 전달·RMS 일치를 확인한다. 직접 만든 파일은 validate_contracts.py 뒤에 경로를 넣는다. 생성된 JSON 파일 하나를 받는 함수는 아래처럼 사용할 수 있다.

```python
from contracts.v1.validate_contracts import load_json, validate_record

record = load_json("my-result.json")
errors = validate_record(record)
if errors:
    raise ValueError("\n".join(errors))
```

검증 성공은 형식과 일부 수치 관계가 맞다는 뜻이다. 센서가 실제로 측정했다는 것, 정상 기준이나 경고 임계값이 적절하다는 것, RUL 정확도·전송 무결성이 실험으로 입증됐다는 뜻은 아니다. CLI는 JSON 객체 파일을 검사하며 NDJSON 파일은 줄마다 파싱해 validate_record에 전달한다.

스키마는 [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-core)를 사용한다. Python 검증기는 [jsonschema의 Draft202012Validator와 FormatChecker](https://python-jsonschema.readthedocs.io/en/stable/validate/)에 교차 필드 검사를 추가한다.
