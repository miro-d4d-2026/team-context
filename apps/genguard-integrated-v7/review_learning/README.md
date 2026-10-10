# 검토 학습 연결 계층

이 패키지는 사용자가 제공한 `GenGuard_Label_Learning_v2`의 `service.py`, `feedback.py`, `label_model.py` 흐름을 기존 GenGuard 앱에 맞춰 통합한 코드다. 원본 참조 폴더는 수정하지 않았다.

## 기존 데이터와 모델의 경계

- 기존 `bridge.sqlite`, 고정 `gg-*` 발전기 모델, 현재 IMU `imu-*` 정상 기준, 센서 수집기, ClikaRT는 변경하지 않는다.
- 검토 기록은 별도 `review-learning/genguard.sqlite`에 저장한다.
- 정상 후보는 기존 `model_bundle/runtime/genguard/model.py`의 `fit_phase`와 `score_phase`를 직접 호출한다. JSON 바깥 형식은 별도 `genguard-reviewed-core-baseline/1`이며 기존 모델 JSON으로 오인해 로드할 수 없다.
- 사람이 표시한 정상·비정상 분류기는 별도 `genguard-reviewed-quadratic-logistic/1`이다. 분류 점수는 고장 확률이 아니다. 정상 분류가 기존 경보·정상 기준 이탈을 해제하지 않는다.
- 진동 전용 입력은 실제 기존 특징인 RMS와 envelope CV 두 개다. Peak나 RPM을 진동에서 만들어내지 않는다.
- 진동+제어반 프로필은 동일 출처의 숫자값만 사용한다. 실측 진동과 모의 수온을 동일한 실측 모델에 혼합 학습하지 않는다.

## 기록 및 검토

수신기는 원래 API를 계속 사용한다. 명시적으로 운전 기록 수집을 시작한 동안만 accepted feature packet을 검토 기록에 복사한다. IMU `window_seq`, configured sample rate, installation, stream을 확인하고 실제 수신 속도의 작은 편차 때문에 설정 변경으로 오인하지 않는다. 스트림/설치/설정 변경은 기록을 종료한다.

전체 기록 또는 시간 구간에 정상·비정상·판단 보류·측정 오류 라벨을 붙인다. 창의 실제 `[elapsed-duration, elapsed]`가 지정 범위에 완전히 포함되어야 한다. 구간 밖은 정상으로 추측하지 않는다. 사람 라벨, 원래 모델 판정, 변경 이력은 별도 보존한다.

기존 반복/무작위 아이폰 데이터는 각각 한 녹화로 가져온다. 합쳐 96개의 독립 시험인 것처럼 취급하지 않는다. 기존 자극 버튼 이름은 원본에 보존하지만 사람의 검토 결과로 자동 승인하지 않는다. 측정 설정이 확인되지 않은 옛 기록은 현재 208 Hz / ±4g 실측 설치와 별도 기준이다.

## 학습과 활성화

- 정상: 같은 출처/장비/설치/프로필/조건에서 정상 승인한 독립 학습 2회, 보정 1회, 검증 1회. 기록당 유효 정상 창 30개 이상, 유효율 95% 이상.
- 이진 분류: 각 클래스에 학습 2회, 보정 1회, 검증 1회. 각 사용 기록·클래스의 유효 창 30개 이상. 클래스별/기록별 가중치를 동일하게 적용한다.
- 전처리·계수는 fit, 분류 경계는 calibration에서 결정한다. validation은 최종 통과 여부에만 쓴다. 검증 시 운전 조건 밖 입력은 추론 때와 동일하게 판단 보류 처리하고 coverage에 반영한다.
- 후보는 background thread에서 계산한다. 운전 시작은 학습 취소를 요청하고 즉시 진행한다. 취소/검토 변경 후보는 적용하지 않는다.
- 활성화는 별도 사용자 동작이며 운전 중에는 금지한다. 새 운전 시작 때 모델 ID를 고정한다.
- 학습에 사용한 라벨·역할·검토의 fingerprint가 바뀌면 관련 후보/운영 모델이 비교에서 제외된다. 원래 기록의 판정은 재작성하지 않는다.

## 모의 시연

모의 기록은 기존 저장소 `genguard.demo.frames` 숫자 생성기를 사용한다. 전압 중심은 현재 선택한 `model_bundle/rules/profile.json`의 208 V 선간 기준으로 명시적으로 이동한다. 각 기록은 독립 난수로 300개의 2초 창을 생성한다. 기존 실제 진동 값에는 이 생성기를 사용하지 않는다.

4개 정상 합성 기록 → 직접 검토/2·1·1 역할 → 정상 학습 → 활성화. 이어 4개 비정상 합성 기록을 직접 검토하고 각각 2·1·1 역할을 지정하면 별도 이진 분류 후보를 학습할 수 있다. 실제 발전기 성능 검증이 아니라 소프트웨어 경로의 기능 확인이다.

## 원본과 API

`learning_workspace.py`가 `/ui-api/`에 연결된다. `state`, `session_detail`, `raw_export`, `dispatch`, `ingest_imu`, `summary`, `close`가 통합 표면이다. 수신 원본 JSONL은 **기존 수신기가 분석한 특징 패킷**이며 원시 가속도라고 부르지 않는다. 기존 CSV와 가져온 원본 JSON은 수정하지 않는다. 모델은 JSON 숫자로 저장하며 pickle을 로드하지 않는다.

테스트: `python3 -m unittest -v test_learning_workspace test_learning_acceptance_audit test_learning_api`
