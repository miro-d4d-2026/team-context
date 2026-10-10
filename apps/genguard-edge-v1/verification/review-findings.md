# 독립 구현 검증 메모

이 검증은 인공 센서값을 사용하는 소프트웨어 계약·모델 생명주기 시험이다. 실제 발전기에서의 고장 탐지 정확도, UNO Q 실행 속도·메모리, 실제 컨트롤러 연결을 입증하지 않는다.

## 실행

패키지 루트에서 `python3 -m unittest discover -s tests -v`.

검증 환경: macOS, 번들 Python 3.12 및 NumPy. 최종 **53개 테스트 통과**(core 32개 + stream 21개), 0.972초. 재현 버그 수정 후 회귀 검사했고, 학습 중 다른 연결의 운전 시작 차단과 학습 실패 후 잠금 해제, 초기 기준의 정상 확인 철회 시 실행 중 unknown 전환, hybrid 채널별 출처, 시동/예열 단계의 판단 보류와 잘못된 프로필 거부까지 추가 확인했다.

## 검증한 경계

- 정상 확인된 서로 다른 운전 2개 학습 / 1개 경보 기준 조정 / 1개 보류 검증. 같은 운전 ID의 중복·분할 간 공유 거부.
- 정상 검토가 없거나 abnormal/exclude인 기록, 짧거나 결측이 많은 이력의 학습 거부.
- 보류한 정상 운전에서 검증 실패한 모델의 활성화 거부.
- 운전 중 새 학습 시작·모델 활성화 거부. 다음 운전에서 모델 적용.
- 초기 기준 모델 유지 및 새 모델과 초기 모델의 동시 비교.
- 센서 설치 변경 시 이전 모델 사용 방지, 서로 다른 설치 이력의 학습 혼합 거부.
- 모의/실측 출처 혼합, 단위 오류, 누락, 오래된 값은 정상 판정을 하지 않음.
- 학습하지 않은 부하 범위는 unknown. 점검 권고·고장 확정으로 바꾸지 않음.
- 동일 seq의 동일 내용 재전송은 중복 저장하지 않음. 내용 충돌·시간 역행은 거부.
- SQLite 재접속 후 모델·기록 유지. 3개 연속 변화에만 점검 권고, 수집 창 누락은 지속 횟수 초기화.
- 각각은 학습 주변 범위에 남아 있어도, 두 센서의 상관관계만 달라지면 결합 점수가 증가하는 인공 예제.

## 발견 후 수정·재검증한 항목

1. **정상 확인 철회 후 후보 활성화**: 학습에 사용한 세션을 후보 생성 후 abnormal로 바꾸어도 과거 validation만 확인하던 문제. 현재 `activate()`가 학습·보정·검증 세션의 정상 검토 상태를 다시 확인한다. 실행 중에도 활성/초기 모델 이력의 정상 확인이 철회되면 unknown으로 전환한다. 회귀 테스트 통과: `test_review_retraction_invalidates_candidate_activation`, `test_active_run_stops_using_model_when_initial_review_is_retracted`.
2. **객체가 아닌 reading의 처리**: `readings.oil_bar=3`이 `input_sources` 구성 중 AttributeError를 일으키던 문제. 현재 unknown으로 원자 저장하며 정상 판정하지 않는다. 회귀 테스트 통과: `test_malformed_reading_has_controlled_failure_without_partial_write`.
3. **학습 중 새 운전 시작 경쟁 조건**: lifecycle activity 파일의 비차단 OS 잠금으로 train/start/activate를 직렬화한다. 독립 SQLite 연결에서 학습 중 start를 요청하면 busy로 거부하고, 학습 완료·실패 후에는 새 운전을 시작할 수 있다. 회귀 테스트 통과: `test_second_connection_cannot_start_run_during_training`, `test_lifecycle_lock_is_released_after_failed_training`.

## 추가 운영 검토

- 잠금 검증은 동일 프로세스 내 독립 연결을 이용한 경합 시험이다. UNO Q 프로세스 강제 종료·전원 차단 복구까지 시험한 것은 아니다.
- 인공 데이터 테스트는 배관·베어링 등 고장 원인 추정이나 RUL의 근거가 아니다. 모델 점수는 고장 확률이 아니다.
- stream의 `elapsed_s`는 MCU 첫 수신 샘플부터의 수집 경과 시간이다. 엔진 시동 기준 시간과 자동으로 같아지는 것이 아니므로 기본 fusion 프로필에서 학습 조건 입력으로 사용하지 않도록 수정했다. 시동/예열 분석을 확장하려면 실제 엔진 경과 시간을 별도로 연동해야 한다. 동일 센서값·부하·외기온에 수집 시각만 달라져도 모델 비교 결과가 유지되는 회귀 테스트를 통과했다.
- 웹 API의 `served_at`은 응답 생성 시각이며 계측 시각이 아니다. 웹에서 새 `record_id` 수신 여부와 세션 상태를 확인해야 하며 마지막 정상 기록을 실시간 정상 표시로 계속 재사용해서는 안 된다.

## 추가 최종 감사에서 수정·검증한 항목

4. **실패한 파일 가져오기가 기존 운전을 닫음**: CLI가 세션 시작에 실패했는데도 finally에서 해당 ID를 닫던 문제. 현재 자신이 시작에 성공한 세션만 닫는다. 회귀 테스트 통과: `test_failed_cli_import_cannot_close_an_existing_live_run`.
5. **프로필 오류의 지연 실패**: 0인 점수 임계값·행수, 비정상 지속 횟수·결측 허용률·음수 조건 허용폭 등을 프로필 로드 시 거부한다. hybrid_demo는 채널별 `allowed_sources` 누락/빈 배열을 허용하지 않는다. 회귀 테스트 통과: `ProfileValidationTests` 2개 및 `test_hybrid_demo_preserves_real_imu_and_scenario_source_boundaries`.
