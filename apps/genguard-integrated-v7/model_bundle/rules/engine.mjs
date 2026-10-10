// Self-contained browser/Node module. Profile mirrors profile.json; tests verify equality.
export const PROFILE = {
  "schema_version": "genguard.alarm-demo/1",
  "profile_id": "army-10kw-208v-demo",
  "header": {
    "name": "GEN-10K-001 · 군 구매요구 참고 모의 발전기",
    "rated_kw": 10,
    "rated_rpm": 1800,
    "rated_hz": 60,
    "voltage_ll_v": 208,
    "phases": 3,
    "wires": 4,
    "power_factor": 0.8,
    "battery_nominal_v": 12,
    "rating_source": "군 구매요구의 10 kW 이상·1800 rpm·60 Hz·120/208 V 중 10 kW·208 V 선간을 시연 선택",
    "actual_generator_connected": false,
    "control_action": "none"
  },
  "fields": [
    {
      "key": "engine_rpm",
      "label": "엔진 회전수",
      "unit": "rpm",
      "default": 1800,
      "min": 0,
      "max": 4000,
      "step": 10,
      "normal_display": "1710–1890 rpm (시연)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "coolant_c",
      "label": "냉각수 온도",
      "unit": "degC",
      "default": 80,
      "min": -30,
      "max": 150,
      "step": 0.1,
      "normal_display": "92°C 미만 (시연 경고 기준; 하한 정상 판정 아님)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님",
      "graph_reference_range": [
        70,
        90
      ],
      "graph_reference_provenance": "demo_assumption",
      "graph_reference_note": "모의 그래프의 참고밴드; 경보 기준 및 학습한 예상 범위와 별개"
    },
    {
      "key": "oil_kgf_cm2",
      "label": "윤활유 압력",
      "unit": "kgf/cm²",
      "default": 3.2,
      "min": 0,
      "max": 10,
      "step": 0.01,
      "normal_display": "2.0–4.5 kgf/cm² (시연)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님",
      "graph_reference_range": [
        2.5,
        4.0
      ],
      "graph_reference_provenance": "demo_assumption",
      "graph_reference_note": "모의 그래프의 참고밴드; 경보 기준 및 학습한 예상 범위와 별개"
    },
    {
      "key": "voltage_ll_v",
      "label": "출력 전압 · 선간",
      "unit": "V",
      "default": 208,
      "min": 0,
      "max": 500,
      "step": 0.1,
      "normal_display": "197.6–218.4 V (정격 ±5% 시연)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "frequency_hz",
      "label": "출력 주파수",
      "unit": "Hz",
      "default": 60,
      "min": 0,
      "max": 100,
      "step": 0.01,
      "normal_display": "57–63 Hz (정격 ±5% 시연)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "load_pct",
      "label": "출력 / 부하율",
      "unit": "%",
      "default": 62,
      "min": 0,
      "max": 150,
      "step": 1,
      "normal_display": "100% 이하 (시연)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "ambient_c",
      "label": "주변 온도",
      "unit": "degC",
      "default": 28.5,
      "min": -30,
      "max": 80,
      "step": 0.1,
      "normal_display": "운전 조건 비교용; 단독 고장 판정 없음",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "vibration_rms_g",
      "label": "진동 RMS · 중력 제거",
      "unit": "g",
      "default": 0.045,
      "min": 0,
      "max": 2,
      "step": 0.001,
      "normal_display": "모의 정상 기준 0.045 g 대비; 절대 안전 한계 없음",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "envelope_cv",
      "label": "진동 간헐성 · 포락선 CV",
      "unit": "ratio",
      "default": 0.12,
      "min": 0,
      "max": 5,
      "step": 0.01,
      "normal_display": "모의 정상 이력과 비교; 절대 안전 한계 없음",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "battery_v",
      "label": "배터리 전압",
      "unit": "V_DC",
      "default": 13.8,
      "min": 0,
      "max": 30,
      "step": 0.1,
      "normal_display": "12.5–15 V (12 V 계통 시연 가정)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "fuel_pct",
      "label": "연료 잔량",
      "unit": "%",
      "default": 70,
      "min": 0,
      "max": 100,
      "step": 1,
      "normal_display": "20% 이상 (시연)",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    },
    {
      "key": "pf",
      "label": "역률 · 모의 계산 조건",
      "unit": "ratio",
      "default": 0.8,
      "min": 0,
      "max": 1,
      "step": 0.01,
      "normal_display": "유효전력·전류 환산용; 단독 고장 판정 없음",
      "source": "scenario",
      "provenance": "시연 설계값; 실제 장비의 정상 한계가 아님"
    }
  ],
  "rules": [
    {
      "id": "rpm_deviation",
      "field": "engine_rpm",
      "operator": "outside",
      "threshold": [
        1710,
        1890
      ],
      "hold_s": 10,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "회전수 범위 이탈",
      "checks": [
        "부하 변화와 회전수 센서값을 먼저 확인",
        "연료 공급 및 조속기 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "oil_deviation",
      "field": "oil_kgf_cm2",
      "operator": "outside",
      "threshold": [
        2,
        4.5
      ],
      "hold_s": 5,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "윤활유 압력 범위 이탈",
      "checks": [
        "유압 센서 및 배선값 확인",
        "윤활유량·누유·필터·펌프 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "coolant_high",
      "field": "coolant_c",
      "operator": "gte",
      "threshold": 92,
      "hold_s": 5,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "냉각수 온도 상승",
      "checks": [
        "동일 부하·외기온 조건인지 확인",
        "냉각수량·누수·라디에이터·냉각팬 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "voltage_deviation",
      "field": "voltage_ll_v",
      "operator": "outside",
      "threshold": [
        197.6,
        218.4
      ],
      "hold_s": 5,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "출력 전압 범위 이탈",
      "checks": [
        "선간 측정값과 결선 조건 확인",
        "부하 변화·배선·전압 조정기 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "frequency_deviation",
      "field": "frequency_hz",
      "operator": "outside",
      "threshold": [
        57,
        63
      ],
      "hold_s": 10,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "출력 주파수 범위 이탈",
      "checks": [
        "RPM·주파수 데이터 동시 확인",
        "부하 및 조속기 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "fuel_low",
      "field": "fuel_pct",
      "operator": "lt",
      "threshold": 20,
      "hold_s": 5,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "연료 부족 점검",
      "checks": [
        "연료량 표시와 잔량 센서 확인",
        "보급 및 대체 전원 준비 검토"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "battery_deviation",
      "field": "battery_v",
      "operator": "outside",
      "threshold": [
        12.5,
        15
      ],
      "hold_s": 5,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "배터리 전압 점검",
      "checks": [
        "12 V 계통과 운전 상태 확인",
        "충전 계통·단자·배터리 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "load_high",
      "field": "load_pct",
      "operator": "gt",
      "threshold": 100,
      "hold_s": 5,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "정격 부하 초과",
      "checks": [
        "명판 정격과 부하율 계산 확인",
        "부하 분산 및 대체 전원 필요 여부 점검"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "vibration_relative",
      "field": "vibration_rms_g",
      "operator": "ratio_gt",
      "threshold": 1.5,
      "hold_s": 6,
      "clear_hold_s": 5,
      "severity": "watch",
      "title": "진동 증가 감지",
      "checks": [
        "센서 부착 위치·고정 상태가 같은지 확인",
        "장비 체결·회전체 상태 추가 점검 요청"
      ],
      "basis": {
        "kind": "demo_assumption",
        "label": "시연 가정",
        "ref": null,
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "overspeed_reference",
      "field": "engine_rpm",
      "operator": "gte",
      "threshold": 1980,
      "hold_s": 0,
      "clear_hold_s": 5,
      "severity": "urgent",
      "title": "과속 보호 참고값 도달",
      "checks": [
        "부하 변화와 회전수 센서값을 먼저 확인",
        "연료 공급 및 조속기 점검 요청"
      ],
      "basis": {
        "kind": "procurement_reference",
        "label": "군 구매요구 보호 참고 · 앱 알림",
        "ref": "A3.4.1.2: 정격 110%, 보호기 10초 이내 응답 요구",
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "oil_low_reference",
      "field": "oil_kgf_cm2",
      "operator": "lte",
      "threshold": 0.6,
      "hold_s": 0,
      "clear_hold_s": 5,
      "severity": "urgent",
      "title": "저유압 보호 참고값 도달",
      "checks": [
        "유압 센서 및 배선값 확인",
        "윤활유량·누유·필터·펌프 점검 요청"
      ],
      "basis": {
        "kind": "procurement_reference",
        "label": "군 구매요구 보호 참고 · 앱 알림",
        "ref": "A3.4.1.3: 0.6 kg/cm² 이하, 보호기 1분 이내 응답 요구; 단위를 kgf/cm²로 해석",
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "coolant_protection_reference",
      "field": "coolant_c",
      "operator": "gte",
      "threshold": 100,
      "hold_s": 0,
      "clear_hold_s": 5,
      "severity": "urgent",
      "title": "과열 보호 참고값 도달",
      "checks": [
        "동일 부하·외기온 조건인지 확인",
        "냉각수량·누수·라디에이터·냉각팬 점검 요청"
      ],
      "basis": {
        "kind": "procurement_reference_with_demo_selection",
        "label": "군 구매요구 보호 참고 · 앱 알림",
        "ref": "A3.4.1.4: 95–100°C 중 100°C를 시연 선택. 정확한 장비 설정 아님; 보호기 1분 이내 응답 요구",
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    },
    {
      "id": "overload_reference",
      "field": "load_pct",
      "operator": "gte",
      "threshold": 110,
      "hold_s": 0,
      "clear_hold_s": 5,
      "severity": "urgent",
      "title": "과부하 보호 참고값 도달",
      "checks": [
        "명판 정격과 부하율 계산 확인",
        "부하 분산 및 대체 전원 필요 여부 점검"
      ],
      "basis": {
        "kind": "procurement_reference",
        "label": "군 구매요구 보호 참고 · 앱 알림",
        "ref": "A3.4.2.2: 정격 110% 이상, 보호기 1분 이내 차단 요구",
        "note": "알림만 생성하며 실제 보호기·자동정지를 대체하지 않음"
      }
    }
  ],
  "timer_policy": {
    "freshness_max_age_s": 5,
    "max_contiguous_gap_s": 2,
    "clear_hold_s": 5
  },
  "vibration_baseline": {
    "rms_g": 0.045,
    "source": "scenario",
    "metric_definition": "vector_ac_rms_g",
    "note": "모의 기준. 실측 IMU에는 동일 부착·샘플링·운전 조건의 별도 정상 기준 필요"
  },
  "pattern_policy": {
    "kind": "statistical_baseline_demo",
    "min_samples": 30,
    "min_context_samples": 15,
    "load_tolerance_pct": 5,
    "ambient_tolerance_c": 3,
    "hold_s": 5,
    "floors": {
      "coolant_c": 2,
      "oil_kgf_cm2": 0.3,
      "vibration_rms_g": 0.01,
      "engine_rpm": 30,
      "voltage_ll_v": 5,
      "envelope_cv": 0.08
    },
    "sources_allowed": [
      "scenario"
    ],
    "automatic_retraining": false
  },
  "source_notes": [
    "군 구매요구 원문과 기존 source-index는 ../genguard-monitoring-spec-v2/ 참조",
    "정상 평균 실측치가 없는 가상 10 kW 발전기. 경고 한계·정상 학습 로그·지연은 시연 설계값",
    "제조사 7.5HDKAL의 정상 범위를 이 모의 발전기에 제조사 승인값으로 적용하지 않음",
    "문서상 보호기 응답시간은 앱 대기시간이 아님. 본 엔진은 참고값 도달 즉시 긴급 알림만 생성"
  ]
};
export const SCENARIOS = {
  "schema_version": "genguard.alarm-demo-scenarios/1",
  "note": "프리셋은 입력값만 바꾼다. 알람 상태·문구는 engine.evaluate가 계산한다.",
  "scenarios": [
    {
      "id": "normal",
      "label": "정상 운전",
      "values": {},
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "vibration",
      "label": "진동 강도 증가",
      "values": {
        "vibration_rms_g": 0.09
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "intermittent",
      "label": "간헐 진동 변화",
      "values": {
        "envelope_cv": 0.8
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "rpm_deviation",
      "label": "RPM 범위 이탈",
      "values": {
        "engine_rpm": 1950,
        "frequency_hz": 65
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "overspeed",
      "label": "과속 보호 참고 도달",
      "values": {
        "engine_rpm": 2000,
        "frequency_hz": 66.6667
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "coolant_high",
      "label": "냉각수 96°C",
      "values": {
        "coolant_c": 96
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "coolant_urgent",
      "label": "냉각수 100°C",
      "values": {
        "coolant_c": 100
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "oil_low",
      "label": "유압 1.5 kgf/cm²",
      "values": {
        "oil_kgf_cm2": 1.5
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "oil_urgent",
      "label": "유압 0.5 kgf/cm²",
      "values": {
        "oil_kgf_cm2": 0.5
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "joint_drift",
      "label": "개별 범위 안 · 복합 변화",
      "values": {
        "coolant_c": 89,
        "oil_kgf_cm2": 2.6
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "rpm_normal_temperature_high",
      "label": "RPM 정상 · 냉각수 이상",
      "values": {
        "coolant_c": 96,
        "engine_rpm": 1800
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "fuel_low",
      "label": "연료 15%",
      "values": {
        "fuel_pct": 15
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "overload",
      "label": "부하 111%",
      "values": {
        "load_pct": 111
      },
      "phase": "running",
      "missing_fields": []
    },
    {
      "id": "missing",
      "label": "컨트롤러 값 미수신",
      "values": {},
      "phase": "running",
      "missing_fields": [
        "coolant_c",
        "oil_kgf_cm2",
        "engine_rpm"
      ]
    },
    {
      "id": "warming",
      "label": "예열 중 · 판단 보류",
      "values": {
        "coolant_c": 40,
        "oil_kgf_cm2": 1
      },
      "phase": "warming",
      "missing_fields": []
    },
    {
      "id": "stopped",
      "label": "정지 · 판단 보류",
      "values": {
        "engine_rpm": 0,
        "frequency_hz": 0,
        "voltage_ll_v": 0,
        "load_pct": 0,
        "oil_kgf_cm2": 0
      },
      "phase": "stopped",
      "missing_fields": []
    }
  ]
};

const clone = value => JSON.parse(JSON.stringify(value));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const fields = new Map(PROFILE.fields.map(field => [field.key, field]));
const severityRank = { inactive: 0, normal: 1, unknown: 2, watch: 3, urgent: 4 };
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
freeze(PROFILE); freeze(SCENARIOS);

export function createDemoState() {
  return { session: null, last_t_s: null, rules: {}, pattern: { since: null, clear_since: null, active: null } };
}

function reading(snapshot, key) {
  const spec = fields.get(key), input = snapshot?.readings?.[key];
  let issue = null;
  if (!input) issue = 'missing';
  else if (!finite(input.value) || input.value < spec.min || input.value > spec.max) issue = 'invalid_value';
  else if (input.unit !== spec.unit) issue = 'unit_mismatch';
  else if (input.quality !== 'ok') issue = 'invalid_quality';
  else if (!finite(input.age_s) || input.age_s < 0) issue = 'invalid_age';
  else if (input.age_s > PROFILE.timer_policy.freshness_max_age_s) issue = 'stale';
  else if (!['scenario', 'imu', 'generator_controller', 'manual'].includes(input.source)) issue = 'invalid_source';
  return { valid: issue === null, issue, value: input && finite(input.value) ? input.value : null, source: input?.source ?? 'missing', unit: spec.unit, input_unit: input?.unit ?? null, age_s: input?.age_s ?? null };
}

export function makeScenario(id, elapsed_s = 0) {
  const preset = SCENARIOS.scenarios.find(item => item.id === id);
  if (!preset) throw new TypeError(`Unknown scenario: ${id}`);
  if (!finite(elapsed_s) || elapsed_s < 0) throw new TypeError('elapsed_s must be non-negative seconds');
  const values = Object.fromEntries(PROFILE.fields.map(field => [field.key, field.default]));
  Object.assign(values, preset.values);
  // Demonstration assumption: synchronous 4-pole generator. Real RPM and Hz are separately sourced.
  if (Object.hasOwn(preset.values, 'engine_rpm') && !Object.hasOwn(preset.values, 'frequency_hz')) values.frequency_hz = values.engine_rpm / 30;
  const readings = Object.fromEntries(PROFILE.fields.map(field => [field.key, { value: values[field.key], unit: field.unit, source: 'scenario', age_s: 0, quality: 'ok' }]));
  preset.missing_fields.forEach(key => delete readings[key]);
  return { schema_version: 'genguard.alarm-snapshot/1', scenario_id: id, t_s: elapsed_s, phase: preset.phase, readings, events: {}, normal_review: false };
}

export function normalTrainingSamples() {
  // Seeded synthetic TRAINING records, never physical generator observations.
  let seed = 104729;
  const random = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  return Array.from({ length: 80 }, (_, index) => {
    const sample = makeScenario('normal', index);
    const load = 60 + random() * 4, ambient = 27.5 + random() * 2;
    Object.assign(sample, { scenario_id: 'normal-training', normal_review: true });
    const offsets = {
      load_pct: load, ambient_c: ambient,
      coolant_c: 80 + (load - 62) * .18 + (ambient - 28.5) * .25 + (random() - .5) * .8,
      oil_kgf_cm2: 3.2 - (ambient - 28.5) * .02 + (random() - .5) * .1,
      vibration_rms_g: .045 + (load - 62) * .0002 + (random() - .5) * .004,
      envelope_cv: .12 + (random() - .5) * .035,
      engine_rpm: 1800 + (random() - .5) * 12,
      voltage_ll_v: 208 + (random() - .5) * 1.6,
    };
    offsets.frequency_hz = offsets.engine_rpm / 30;
    Object.entries(offsets).forEach(([key, value]) => sample.readings[key].value = value);
    return sample;
  });
}

function stats(values, floor) {
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const sd = Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / Math.max(1, values.length - 1));
  const width = Math.max(3 * sd, floor);
  return { mean, sd, low: mean - width, high: mean + width, n: values.length, width };
}

export function trainBaseline(samples) {
  if (!Array.isArray(samples) || samples.length < PROFILE.pattern_policy.min_samples) throw new TypeError('At least 30 reviewed normal scenario records are required');
  const rows = samples.map((sample, index) => {
    if (sample.phase !== 'running' || sample.normal_review !== true) throw new TypeError(`Training record ${index} is not reviewed steady normal`);
    const row = {};
    for (const field of PROFILE.fields) {
      const item = reading(sample, field.key);
      if (!item.valid || item.source !== 'scenario') throw new TypeError(`Training record ${index}/${field.key}: only fresh valid scenario inputs allowed`);
      row[field.key] = item.value;
    }
    // Prevent obvious threshold violations from becoming a normal model through a mistaken label.
    for (const rule of PROFILE.rules) {
      const value = rule.operator === 'ratio_gt' ? row[rule.field] / PROFILE.vibration_baseline.rms_g : row[rule.field];
      if (matches(value, rule.operator, rule.threshold)) throw new TypeError(`Training record ${index} violates ${rule.id}`);
    }
    return row;
  });
  const range = key => [Math.min(...rows.map(row => row[key])), Math.max(...rows.map(row => row[key]))];
  return freeze({ kind: 'statistical_baseline_demo', model_version: 'mean-sd-context-v1', source: 'scenario', sample_count: rows.length, training_only: true, normal_review_required: true, automatic_retraining: false, context: { load_pct: range('load_pct'), ambient_c: range('ambient_c') }, rows, summary: Object.fromEntries(Object.entries(PROFILE.pattern_policy.floors).map(([key, floor]) => [key, stats(rows.map(row => row[key]), floor)])), note: '모의 정상 로그에서 계산한 통계 기준. 기존 UNO Q 회귀 AI와 별개이며 IF·실제 발전기 학습 성능을 증명하지 않음.' });
}

function matches(value, op, threshold) {
  if (op === 'outside') return value < threshold[0] || value > threshold[1];
  if (op === 'ratio_gt') return value > threshold + Number.EPSILON * Math.max(1, Math.abs(threshold)) * 8;
  if (op === 'gt') return value > threshold;
  if (op === 'gte') return value >= threshold;
  if (op === 'lt') return value < threshold;
  if (op === 'lte') return value <= threshold;
  throw new TypeError(`Unsupported rule operator: ${op}`);
}

function patternResult(metrics, baseline, phase) {
  const base = { status: 'unknown', kind: 'statistical_baseline_demo', expected: {}, explanations: [], abnormal_fields: [], source: 'scenario' };
  if (phase !== 'running') return { ...base, status: 'inactive', reason: '예열·정지 구간의 정상 운전 패턴 판단 보류' };
  if (!baseline || baseline.kind !== 'statistical_baseline_demo' || !Array.isArray(baseline.rows)) return { ...base, reason: '승인한 정상 학습 기준 없음' };
  const keys = ['load_pct', 'ambient_c', ...Object.keys(PROFILE.pattern_policy.floors)];
  if (keys.some(key => !metrics[key].valid)) return { ...base, reason: '학습 비교에 필요한 데이터 미수신 또는 무효' };
  if (keys.some(key => metrics[key].source !== baseline.source)) return { ...base, reason: '실측·모의 출처가 달라 모의 학습 기준을 적용하지 않음' };
  for (const key of ['load_pct', 'ambient_c']) {
    const [low, high] = baseline.context[key], margin = key === 'load_pct' ? 1 : .5;
    if (metrics[key].value < low - margin || metrics[key].value > high + margin) return { ...base, reason: '학습한 부하·외기온 조건 밖: 새로운 정상 기록 필요' };
  }
  const rows = baseline.rows.filter(row => Math.abs(row.load_pct - metrics.load_pct.value) <= PROFILE.pattern_policy.load_tolerance_pct && Math.abs(row.ambient_c - metrics.ambient_c.value) <= PROFILE.pattern_policy.ambient_tolerance_c);
  if (rows.length < PROFILE.pattern_policy.min_context_samples) return { ...base, reason: '유사 조건의 정상 기록 부족' };
  for (const [key, floor] of Object.entries(PROFILE.pattern_policy.floors)) {
    const expected = stats(rows.map(row => row[key]), floor), item = metrics[key];
    base.expected[key] = expected;
    if (item.value < expected.low || item.value > expected.high) {
      base.abnormal_fields.push(key);
      base.explanations.push({ field: key, value: item.value, expected_low: expected.low, expected_high: expected.high, unit: item.unit, source: item.source, text: `${fields.get(key).label}: 현재 ${item.value.toFixed(3)} ${item.unit}, 유사 조건의 모의 정상 기록 ${expected.low.toFixed(3)}–${expected.high.toFixed(3)} ${item.unit}` });
    }
  }
  return { ...base, status: base.abnormal_fields.length ? 'watch' : 'normal', comparison_sample_count: rows.length, reason: base.abnormal_fields.length ? '유사한 부하·외기온에서 학습한 정상 기록과 차이' : '유사 조건의 학습한 범위 내' };
}

function updateAlarm(record, condition, t, hold, fresh, build, id, events) {
  if (!fresh) { record.since = null; record.clear_since = null; if (record.active) record.active.held = true; return; }
  if (record.active) {
    record.active.held = false;
    if (condition) { record.clear_since = null; Object.assign(record.active, build(), { started_t_s: record.active.started_t_s, held: false }); }
    else {
      if (record.clear_since === null) record.clear_since = t;
      if (t - record.clear_since >= PROFILE.timer_policy.clear_hold_s) {
        events.push({ type: 'cleared', id, t_s: t, source: clone(record.active.source) });
        record.active = null; record.clear_since = null; record.since = null;
      }
    }
  } else if (condition) {
    if (record.since === null) record.since = t;
    if (t - record.since >= hold) {
      record.active = { ...build(), started_t_s: t, held: false };
      record.since = null; events.push({ type: 'raised', id, t_s: t, source: clone(record.active.source) });
    }
  } else record.since = null;
}

function deriveMetrics(metrics) {
  const make = (key, value, parents, unit) => {
    const valid = parents.every(parent => metrics[parent].valid) && finite(value);
    metrics[key] = { value: valid ? value : null, unit, valid, issue: valid ? null : 'invalid_parent_or_denominator', source: 'derived', parent_sources: Object.fromEntries(parents.map(parent => [parent, metrics[parent].source])), status: valid ? 'normal' : 'unknown', basis: { kind: 'demo_calculation', note: '10 kW 정격·균형 3상 가정의 계산값. 독립된 실측값이 아님' } };
  };
  make('active_power_kw', PROFILE.header.rated_kw * metrics.load_pct.value / 100, ['load_pct'], 'kW');
  const denominator = Math.sqrt(3) * metrics.voltage_ll_v.value * metrics.pf.value;
  make('current_a', denominator > 0 ? metrics.active_power_kw.value * 1000 / denominator : null, ['load_pct', 'voltage_ll_v', 'pf'], 'A');
}

export function evaluate(snapshot, state, { baseline } = {}) {
  if (!state || !finite(snapshot?.t_s) || snapshot.t_s < 0 || !['running', 'warming', 'warming_up', 'starting', 'stopped'].includes(snapshot.phase)) throw new TypeError('Valid state, t_s and phase are required');
  const session = snapshot.scenario_id ?? 'manual';
  if (state.session !== session) Object.assign(state, createDemoState(), { session });
  if (state.last_t_s !== null && snapshot.t_s < state.last_t_s) throw new RangeError('Time moved backwards; createDemoState() before replay');
  const gap = state.last_t_s !== null && snapshot.t_s - state.last_t_s > PROFILE.timer_policy.max_contiguous_gap_s;
  if (gap) for (const record of [...Object.values(state.rules), state.pattern]) { record.since = null; record.clear_since = null; }
  state.last_t_s = snapshot.t_s;
  const running = snapshot.phase === 'running';
  const metrics = Object.fromEntries(PROFILE.fields.map(field => {
    const item = reading(snapshot, field.key);
    return [field.key, { ...item, status: !item.valid ? 'unknown' : running ? 'normal' : 'inactive', pending: false, basis: { kind: 'demo_assumption', note: field.provenance }, rule_ids: [] }];
  }));
  const events = [], pending = [];
  for (const rule of PROFILE.rules) {
    const item = metrics[rule.field];
    const record = state.rules[rule.id] ??= { since: null, clear_since: null, active: null };
    const ratioValid = rule.operator !== 'ratio_gt' || item.source === PROFILE.vibration_baseline.source;
    const fresh = running && item.valid && ratioValid;
    const value = rule.operator === 'ratio_gt' ? item.value / PROFILE.vibration_baseline.rms_g : item.value;
    const build = () => ({ id: rule.id, severity: rule.severity, title: rule.title, fields: [rule.field], reason: `${fields.get(rule.field).label} ${item.value} ${item.unit}; ${rule.operator} ${JSON.stringify(rule.threshold)}${rule.operator === 'ratio_gt' ? ' × 모의 정상 기준' : ''}`, checks: clone(rule.checks), basis: clone(rule.basis), source: { [rule.field]: item.source }, value: item.value, unit: item.unit, control_action: 'none' });
    updateAlarm(record, fresh && matches(value, rule.operator, rule.threshold), snapshot.t_s, rule.hold_s, fresh, build, rule.id, events);
    item.rule_ids.push(rule.id);
    if (!ratioValid && item.valid && running) { item.status = 'unknown'; item.issue = 'source_baseline_mismatch'; }
    if (record.since !== null) { item.pending = true; pending.push({ id: rule.id, field: rule.field, elapsed_s: snapshot.t_s - record.since, required_s: rule.hold_s }); }
  }
  const pattern = patternResult(metrics, baseline, snapshot.phase);
  const patternFresh = running && ['normal', 'watch'].includes(pattern.status);
  const patternBuild = () => ({ id: 'pattern_change', severity: 'watch', title: '평소 정상 운전 패턴과 차이', fields: [...pattern.abnormal_fields], reason: pattern.explanations.map(item => item.text).join(' / '), checks: [...new Set(pattern.abnormal_fields.flatMap(key => PROFILE.rules.find(rule => rule.field === key)?.checks ?? ['센서 부착·배선 및 운전 조건 확인']))], basis: { kind: 'statistical_baseline_demo', label: '승인한 모의 정상 기록의 통계 비교', note: '정확한 고장 부위·고장 확률·RUL은 판단하지 않음' }, source: Object.fromEntries(pattern.abnormal_fields.map(key => [key, metrics[key].source])), control_action: 'none' });
  updateAlarm(state.pattern, pattern.status === 'watch', snapshot.t_s, PROFILE.pattern_policy.hold_s, patternFresh, patternBuild, 'pattern_change', events);
  if (state.pattern.since !== null) pending.push({ id: 'pattern_change', elapsed_s: snapshot.t_s - state.pattern.since, required_s: PROFILE.pattern_policy.hold_s });
  const alerts = [...Object.values(state.rules).map(record => record.active), state.pattern.active].filter(Boolean).map(clone);
  for (const alert of alerts) for (const key of alert.fields) if (metrics[key]?.valid && running && !alert.held) metrics[key].status = severityRank[alert.severity] > severityRank[metrics[key].status] ? alert.severity : metrics[key].status;
  const invalid = Object.entries(metrics).filter(([, item]) => !item.valid || item.status === 'unknown').map(([key]) => key);
  deriveMetrics(metrics);
  const derivedUnknown = ['active_power_kw', 'current_a'].filter(key => !metrics[key].valid);
  const coverage = { derived_unknown_fields: derivedUnknown, total: PROFILE.fields.length, valid: PROFILE.fields.length - invalid.length, unknown_fields: invalid, complete: invalid.length === 0, pattern_available: patternFresh, actual_generator_connected: false, oem_protection_signal: 'not_connected', source: Object.fromEntries(PROFILE.fields.map(field => [field.key, metrics[field.key].source])) };
  // A stale channel never becomes healthy. Fresh urgent evidence remains prominent, with coverage shown separately.
  let status = !running ? 'inactive' : invalid.length || derivedUnknown.length || pattern.status === 'unknown' ? 'unknown' : 'normal';
  const freshAlerts = alerts.filter(alert => !alert.held);
  for (const alert of freshAlerts) if (severityRank[alert.severity] > severityRank[status]) status = alert.severity;
  return { status, alerts, events, pending_rules: pending, metrics, pattern, coverage, t_s: snapshot.t_s, phase: snapshot.phase, scenario_id: session, source: PROFILE.fields.every(field => metrics[field.key].source === 'scenario' || metrics[field.key].source === 'missing') ? 'scenario_demo' : 'hybrid_demo', safety_status: alerts.some(alert => alert.severity === 'urgent') ? 'reference_alert_active' : 'oem_signal_not_connected', control_action: 'none', rul: null, predicted_failure_part: null, notice: '모의 발전기 판정 시연. 표시된 긴급 기준은 실제 정지 명령이 아닙니다.' };
}
