# GenGuard 관제 화면

UNO Q에서 직접 띄우는 시연용 관제 웹이다. 관제 PC나 담당자 휴대폰이 접속한다.

| 영역 | 무엇이 계산하나 |
|---|---|
| 계측 카드, 규칙 경보, 점검 안내 | 우지의 `static/vendor/engine.mjs` (브라우저, 원본 그대로) |
| AI 패턴 판정 | 우지의 208V 학습 모델(`models/gen10k-208v`, `context-ridge-residual-covariance/1`)을 보드의 ClikaRT로 실행 (`server.py` → `/api/score`). 3개 창 연속 초과 시 지속 경고 |
| 참고 한계 도달시간 | 우지의 `static/vendor/trend.mjs` (모의 유압 기록, 5분 이상 필요. "시연 가속 ×10" 사용) |
| 정비 우선순위 | 발전기 3대(1호기 조작, 2호기 복합 변화, 3호기 정상)를 긴급 규칙 → AI 지속 경고 → 점검 규칙 → AI 변화 관찰 → 판정 불가 → 정상 순으로 정렬 |
| 실측 IMU | PR #1 게이트웨이(127.0.0.1:9877)에서 1초 진동 RMS. **판정 모델에는 넣지 않는다** (모델은 합성 데이터로만 학습) |

발전기 값은 전부 모의 입력이다. 실제 발전기 정확도·RUL이 아니다.

## 실행 (UNO Q)

전제: [ClikaRT 설치·라이선스](../../runtime/hardware/clikart/README.md), `~/genguard-edge-v1`, IMU 게이트웨이 실행 중.

```sh
cd ~/genguard-console
PYTHONPATH=$HOME/clikart/site:$HOME/genguard-edge-v1 GENGUARD_ENGINE=clikart \
  python3 server.py --edge-dir $HOME/genguard-edge-v1          # 127.0.0.1:8790
```

Mac에서 `adb forward tcp:8790 tcp:8790` 후 `http://localhost:8790`. 다른 기기에서 접속하려면 `--host 0.0.0.0`(인증 없음, 시연망에서만).

## 확인한 것 (2026-10-11 01:41 KST, 보드 Wi-Fi 끔)

`/api/status`: `engine=clikart-0.6.4`, 라이선스 파일, 네트워크 미도달. "개별 범위 안 · 복합 변화"(수온 89℃, 유압 2.6 kgf/cm²)에서 규칙 경보 없음, AI 점수 26.2/임계 2.0, 3개 창 뒤 지속 경고, 추론 약 6ms, 우선순위 1위. 정상 운전에서 AI 점수 0.05로 정상 패턴.
