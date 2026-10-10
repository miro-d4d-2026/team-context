# GenGuard 작업본 인수인계 · 2026-10-10

현재 실행본은 `apps/genguard/`의 **2.1.0-ml**이다. v1 → v2 → v2.1 작업을 마지막 앱에 통합했고, 초기 라벨링 도구와 참고값 패키지는 별도 폴더로 보존했다.

## 먼저 파일 반영 상태 확인

`delivery/genguard-publication.json`의 `full_payload_uploaded`가 true이고 `apps/genguard/app.py`가 있을 때만 전체 업로드가 완료된 것이다. 초기 PR에는 인수인계 문서·작은 분류/추론 모듈만 먼저 게시될 수 있다. 인수인계 문서가 있다는 이유로 전체 소스·실물 검증 완료라고 보고하지 않는다.

## 작업 위치

| 위치 | 내용 |
|---|---|
| `apps/genguard/` | GenGuard 웹, 기준 비교, 저장·복구, 학습·추론, 배포·검증 실행기 |
| `prototypes/vibration-pattern-demo/` | 이전 라벨링·학습·실시간 패턴 실험 앱 |
| `references/vibration-patterns/` | 기존 고정 모델·대시보드 참고값·반복/랜덤 평가 근거 |
| `contracts/v1/` | 우지의 원본 메시지 계약·스키마·검증기·예시·테스트 |
| `docs/genguard/` | 요구 검토·모델 실패 결과·검증 요약·이번 재검사 로그 |

이 PR은 기존 `main`과 하드웨어 PR #1을 덮어쓰거나 자동 병합하지 않는다. 현재 프로토타입은 원래의 펌웨어/계측기 또는 UNO Q의 QWIIC 게이트웨이를 사용한다. 게이트웨이 소스는 PR #1에서 별도로 확인한다.

## 실행

```sh
cd apps/genguard
# Mac/PC에서 로컬 웹 실행. 추가 패키지 없는 기존 기능
bash Start.command
# 같은 PC에서 학습도 시험하려면 Python 3.11+와 학습 의존성 필요
bash Start-ML.command
# Mac에서 연결된 UNO Q에 새 디렉터리로 배포·검증
bash Deploy-and-Verify.command
```

보드 셸에서는 `README-ML.md`의 `setup-ml.sh` 및 `verify_on_device.py` 절차를 사용한다. 파일 게시만으로 보드에 설치되지 않는다. UNO Q는 사용자 확인상 4GB RAM/32GB eMMC다. Gemma/LLM을 쓰지 않고 작은 학습 모델을 사용한다.

## 검증과 미검증을 구분

- 기존 4종 모델: 아이폰-IMU 자극 종류 분류. 별도 랜덤 녹화 48창에서 RMS+CV 48/48. 독립 실험 48회 또는 발전기 고장 정확도 100%가 아니다.
- 새 Isolation Forest 후보: 기준 연속 3/12 이탈, 강한 연속 0/12 이탈로 기대한 변화 검출에 실패. 자동 실물 적용 금지. 이 실패를 숨기지 않는다.
- v2.1의 기존 호스트 기록 107테스트 통과를 보존했다. 이번 업로드 준비 중에도 Linux x86_64 / Python 3.13.5에서 107테스트를 재실행해 통과했다. UI 검사를 이번에 다시 수행한 것은 아니다.
- 실제 UNO Q 배포, 보드 시간·메모리, 장비 전원 차단, 실제 발전기 고장·RUL·Modbus는 이번 업로드 작업에서 검증하지 않았다.
- `data/*raw.csv.gz`와 반복/랜덤 표시 자료는 실측 녹화 재생이다. 모의 장비 데이터는 모의로 표시한다.
- 계약 v1 RMS와 모델의 3축 합성 RMS를 혼용하지 않는다. `maintenance_record`/`pattern_result`를 계약 v1 `analysis_result`와 자동 동일시하지 않는다.

## 담당 경계

미로는 센서·펌웨어·보드 배치와 통합, 이치는 분석·모델 평가, 우지는 계약·영구 기록·통신·관제를 맡는다. 여기 게시한 통합 앱은 이 대화에서 만든 작업본이며 다른 팀원 로컬 구현의 존재나 완료 상태를 대신하지 않는다. 다른 담당 소스를 삭제하거나 재작성하기 전에 실제 브랜치·PR을 확인한다.
