# 기존 홈페이지에 연결하기

이 폴더의 `client.mjs`를 기존 웹 프로젝트에 넣으세요. `index.html`은 연결 확인용 독립 화면입니다. 서버 실행·IMU 수신기 설정은 패키지 최상위 README를 따릅니다.

## 입력 구분

| 항목 | 입력 위치 | 화면 표시 |
|---|---|---|
| 진동 RMS · 간헐성 CV | UNO Q가 수신한 IMU 창 | 수정 불가. `IMU 실측` 또는 `IMU 기록 재생` |
| RPM · 수온 · 유압 · 전압 · 주파수 · 부하 · 주변 온도 · 배터리 · 연료 · 역률 | 웹 입력 | `모의` |

`/api/config`에서 `editable: true`이면서 `source: "scenario"`인 항목만 입력칸으로 만드세요. 진동을 기본값이나 웹 입력으로 채우면 안 됩니다. 서버가 IMU 특징값을 결합합니다.

```js
import {GenGuardClient} from './client.mjs';

const client = new GenGuardClient({
  baseUrl: 'http://UNO_Q_IP:9879',
  token: '서버에 설정한 접속 토큰', // 브라우저 메모리에서만 사용
});
const config = await client.getConfig();
await client.startSession();

// 웹이 보내는 것은 발전기 모의 값뿐입니다.
const generatorValues = {...config.default_values, coolant_c: 89, oil_kgf_cm2: 2.6};
const result = await client.sendSample(generatorValues);

renderStatus(result.status, result.ai.summary);
renderImu(result.imu); // source, features, calibration 상태를 함께 표시
renderEvidence(result.ai.current_comparison.evidence); // 항목별 source 표시
renderComponent('발전기', result.ai.components.generator);
renderComponent('진동', result.ai.components.vibration);
renderAlerts(result.fixed.alerts, result.notification_events);
```

`render*`는 기존 웹의 화면 갱신 함수입니다. 점수 하나로 합치지 말고 발전기·진동의 점수와 근거를 각각 표시하세요. 두 분석을 합동 판단에 쓰며, 이 버전에서 두 데이터 간 상관관계를 하나의 모델로 학습하지 않습니다.

## 처음 시연할 때

1. UNO Q의 IMU 수신기를 실행하고 웹에서 `연결 · 시험 시작`을 누릅니다.
2. 센서 카드의 출처와 실제 측정값을 확인합니다. 기록 파일을 쓰는 경우 `IMU 기록 재생` 배지가 보여야 합니다.
3. 센서를 고정한 상태로 정상 진동을 유지합니다. 확인란을 체크하고 `정상 기준 수집 시작`을 누릅니다.
4. 15개 고유 창 수집이 끝나 `기준 등록됨`이 표시되면 `2초마다 자동 분석`을 누릅니다.
5. 진동은 실제 진동 발생 장치에서 바꿉니다. 수온·유압 등은 웹 입력 또는 프리셋으로 바꿉니다.
6. 각각의 변화 근거와 출처, 지속 변화 확인, 고정 규칙 경고를 확인합니다.

정상 기준은 다음 호출로 등록을 시작합니다. 정상 여부는 작업자가 확인해야 합니다.

```js
await client.startCalibration({confirmedNormal: true});
// 수집 중에는 2초마다 순서대로 호출. client.busy면 건너뜁니다.
const imu = await client.getImu();
console.log(imu.calibration.state, imu.calibration.count, imu.calibration.required);
```

자동 분석 응답에 IMU 상태도 포함됩니다. 분석 중에 별도의 IMU 폴링 요청을 겹치지 마세요. 포함된 데모는 자동 분석이 멈췄을 때만 IMU를 폴링합니다.

## 통신 실패 처리

- `client.busy`일 때 새 요청을 겹치지 않습니다.
- 타임아웃 후 `client.pendingSample`이 있으면 동일한 값과 순번으로 재전송합니다. 센서 값은 재전송 시에도 웹이 지정하지 않습니다.
- `notification_events`는 `id`로 중복 표시를 막습니다.
- 마지막 분석이 5초보다 오래됐으면 현재 상태를 `판단 불가`로 표시하고 이전 결과는 이력으로 남깁니다. 재전송 결과의 `analysis_age_s`를 수신 시각에서 빼서 분석 시각을 복원해야 합니다. IMU의 `age_s`에도 같은 분석 나이를 더합니다. 새 IMU 창을 폴링해도 옛 분석이 새 결과가 되지는 않습니다. IMU가 끊겼거나 정상 기준이 없을 때도 정상으로 표시하지 않습니다.
- 종료는 `await client.closeSession()`으로 이 브라우저의 시험만 닫습니다.

```js
const pending = client.pendingSample;
if (pending) {
  const result = await client.sendSample(pending.values, {
    phase: pending.phase,
    quality: pending.quality,
  });
}
```

실제 진동이 입력돼도 발전기 수온·유압 값은 여전히 모의 값입니다. 화면 전체를 `실측`으로 표시하지 마세요. HTTPS 홈페이지에서 HTTP 기기 API를 직접 부르면 브라우저가 차단하므로, 시연은 UNO Q가 제공하는 같은 주소의 페이지를 사용하거나 HTTPS 프록시를 준비합니다.

## v3: 악화 추세를 보조 정보로 검증하기

현재 이상 탐지 화면은 그대로 유지합니다. 두 가지 추세 경로는 서로 시간을 섞지 않습니다.

- **입력 이력의 악화 추세 카드:** `/samples`의 `result.trend`를 표시합니다. 웹에서 실제 경과 시간에 따라 입력한 모의 수온·유압 이력입니다. 최소 120초의 유효 이력이 필요하며 값을 빠르게 여러 번 눌러도 미래 운전 시간이 생기지 않습니다. IMU 진동의 미래·수명 예측은 포함하지 않습니다.
- **별도 악화 추세 실험:** 서버가 생성한 모의 시계열을 재생합니다. IMU 미연결 상태에서도 API 연결 후 실행할 수 있으며 위의 실제 센서값이나 입력란을 바꾸지 않습니다. 1·10·30배 재생은 화면의 모의 시계에만 적용됩니다.

```js
const catalog = await client.getTrendCatalog();
const experiment = await client.runTrendExperiment(catalog.scenarios[0].id);
// source === 'scenario', time_mode === 'simulated' 확인 후 별도 화면에 표시
```

실험 선택 → `실험 불러오기` → `재생`을 누릅니다. 슬라이더로 모의 운전 시점을 이동할 수 있습니다. 실선은 현재 재생 시점까지의 관측값, 점선은 그 시점까지의 데이터로 서버가 계산한 조건부 미래 추정, 음영은 제공된 경우 검증 잔차의 경험적 오차 범위입니다. 미래의 관측 곡선을 미리 표시하지 않습니다.

현재 모델의 지속 이상, 추가 추세 권고, 고정 경고를 각각 표시합니다. 전체 시나리오 끝에서 최초 발생 시점을 비교하며, **현재 이상 탐지가 더 빠르면 그 결과를 그대로 표시**합니다. 추세 권고가 더 빠르다는 고정 문구를 사용하지 않습니다. 전체 비교는 미래 관측 결과이므로 재생 종료 전에는 공개하지 않습니다.

기존 웹에 붙일 때 `trend.mjs`의 `projectChart`, `visibleMilestones`, `comparisonText`를 재사용할 수 있습니다. `createTrendUI`는 이 데모의 HTML 요소 ID에 맞춘 화면 제어기이므로 기존 웹에서는 표시 컴포넌트에 맞게 연결하세요. `client.mjs` API 호출은 기존과 동일한 토큰을 사용하며 세션 순번을 소비하지 않습니다.

```sh
node --test web/test_client.mjs web/test_demo.mjs web/test_trend.mjs
```

### 최종 평가를 반영한 표시 정책

악화 추세는 **실험용 보조 기능**입니다. `result.trend.advisory`나 `trend_notification_events`를 기존 `result.status`, 고정 경고 또는 이번 시험 알림 이력에 합치지 않습니다. 이 화면은 기존 `notification_events`만 기존 이력에 표시하고, 추세 권고는 별도 카드에만 표시합니다.

예상값을 만든 방법은 `trend.channels[항목].method`로 표시합니다. 현재 선택된 방법이 `linear`라면 `단순 선형 추세`라고 표시하며 학습형 모델이라고 바꿔 부르지 않습니다. 전체 시나리오 종료 화면에는 서버의 `evaluation.selected_method`, `evaluation.mae`, `evaluation.advisory_summary.transient`, `evaluation.current_anomaly_comparison` 수치로 선택 방법, 오차, 일시적 변화 오경보, 기존 감지 대비 결과를 표시합니다. 숫자를 웹에 고정하지 않습니다.
