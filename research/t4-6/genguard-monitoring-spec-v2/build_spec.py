"""Build research/handoff fixtures; does not control equipment or modify v1 runtime."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "spec"
OUT.mkdir(exist_ok=True)

def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

fields = []
def field(id, label, unit, role, origin, acquisition, normal, note, priority="P1"):
    fields.append(dict(id=id, label_ko=label, unit=unit, learning_role=role,
                       requirement_origin=origin, acquisition=acquisition,
                       reference_normal=normal, note=note, priority=priority))

# Sources: A = supplied Army requirement; O/S/M = manufacturer docs in README.
field("voltage_v", "출력 교류 전압", "V_AC_RMS", "target", "A3.4.3.1", "controller_or_isolated_meter", "선택 모델/결선별 정격; Onan 시연 L1-L2 240V", "Army는 상별 L-N/L-L 선택 표시도 보존. 380V를 공통 기본값으로 쓰지 않음.", "P0")
field("current_a", "출력 교류 전류", "A_AC_RMS", "target_optional", "A3.4.3.1", "controller_or_CT_power_meter", "부하 의존; Onan 240V 정격 31.3A", "Army는 상별 전류. 전류만으로 kW 과부하 확정 금지. load와 중복되면 모델에서 제외.", "P0")
field("frequency_hz", "출력 주파수", "Hz", "rule_and_optional_target", "A3.4.3.2", "controller_or_isolated_meter", "60Hz 정격, 경보 허용폭 미확정", "4극 동기식 시연에서 RPM/30과 비교. RPM과 중복되는 특징은 무조건 두 번 가중하지 않음.", "P0")
field("engine_rpm", "엔진 회전수", "rpm", "target", "A3.4.1.2;A3.4.3.2", "controller_or_tachometer", "1800rpm 정격; 제품별 실제 분포 별도", "과속 보호 및 주파수/회전계 겸용 요구. 정격은 정상 평균이 아님.", "P0")
field("runtime_hours", "누적 운전시간", "h", "maintenance_schedule", "A3.4.3.2", "controller_or_run_state_counter", "비감소 기록, 정비 주기와 비교", "부품별 마지막 정비일·정비후 운전시간 별도. 누적 시간이 높다고 고장 아님.", "P0")
field("preheat_on", "예열 표시", "bool", "state_rule", "A3.4.3.5", "controller_or_isolated_status_input", "예열 단계에서 상태 확인", "숫자 이상탐지 학습에서 제외. 예열시간 조건은 모델/외기온별.", "P0")
field("oil_bar", "윤활유 압력", "bar", "target", "A3.4.1.3;A3.4.3.5", "controller_or_pressure_transducer", "Onan 정상 운전온도 1.94~4.42bar (O7)", "압력과 오일량은 다름. 정지/시동/냉간에는 같은 기준 적용 금지.", "P0")
field("coolant_c", "냉각수 온도", "degC", "target", "A3.4.1.4;A3.4.3.5", "controller_or_coolant_temperature_sensor", "Onan 71~91℃, 부하·외기온 의존 (O8)", "국소 표면온도/주변온도와 대체 불가.", "P0")
field("battery_charge_reading", "배터리 충전계", "as_installed", "rule_or_optional_target", "A3.4.3.5", "controller_or_isolated_charging_measurement", "계기의 실제 단위/종류 현장 확인", "요구서는 충전계 종류를 특정하지 않음. 전압계라고 단정하지 않으며 DC전압은 별도 필드.", "P0")
field("voltage_selector", "상전압 선택 위치", "enum", "metadata", "A3.4.3.3", "panel_or_manual", "선택한 상/결선 정보", "스위치 자체 고장학습이 아니라 값 해석 문맥.", "P0")
field("current_selector", "상전류 선택 위치", "enum", "metadata", "A3.4.3.4", "panel_or_manual", "선택한 상 정보", "통합 스위치 허용. 디지털 표시에서 동일 기능 제공.", "P0")
for id, label, clause in [
    ("oem_overspeed", "과속 경보/기관 정지", "3.4.1.2"),
    ("oem_low_oil", "저유압 경보/기관 정지", "3.4.1.3"),
    ("oem_high_coolant", "냉각수 과열 경보/기관 정지", "3.4.1.4"),
    ("oem_overload", "과부하 회로 차단", "3.4.2.2")]:
    field(id, label, "bool_or_code", "event_rule", "A"+clause, "controller_or_isolated_alarm_contact", "비활성/활성/미수신 구분", "실제 보호기 동작 상태와 앱의 추정 기준 초과를 별도로 표시. AI 입력 정답으로 사용 금지.", "P0")

field("vibration_rms_g", "3축 합성 AC 진동 RMS", "g", "target", "GenGuard addition", "live_imu", "장비·부착위치·RPM·부하별 초기 정상 기록", "sqrt(var(ax)+var(ay)+var(az)); DC 제거. 기존 sqrt(mean axis variance)와 단위정의 혼합 금지.", "P0")
field("envelope_cv", "진동의 시간 변동", "1", "target", "GenGuard addition", "derived_from_live_imu", "동일 부착 조건 정상 학습", "2초 창/100ms RMS 구간 CV는 개발 정의. 간헐 진동 변화 탐지용, 고장부위 아님.", "P0")
field("vibration_peak_g", "진동 최대값", "g", "candidate_target", "GenGuard addition", "derived_from_live_imu", "실측 정상 분포", "충격/접촉/포화와 구분. RMS·첨도 등 무조건 모두 투입하지 않고 검증 선택.")
field("vibration_bands", "대역별 진동 에너지", "g_squared", "candidate_target", "GenGuard addition", "derived_from_live_imu", "실제 ODR·대역폭·필터와 함께 고정", "현재 약208Hz 수집이면 Nyquist104Hz 미만; 센서 최대 ODR로 고주파 분석했다고 주장 금지.")
field("active_power_kw", "유효 출력 전력", "kW", "context_source", "load context", "controller_or_true_power_meter", "Onan 정격 7.5kW 이내; 정상 운전점은 부하 의존", "PF·결선 반영된 계측값 사용. 전력에서 load_pct 계산.", "P0")
field("load_pct", "정격 대비 부하율", "%", "context", "load context", "derived_active_power_and_rating", "100*kW/정격kW", "환경에 따른 디레이팅·전류 제한도 별개 확인. 미측정이면 임의 추정해 live라 하지 않음.", "P0")
field("ambient_c", "흡기/주변 온도", "degC", "context", "operating context", "external_sensor_or_controller", "운전환경 범위와 학습 지원영역", "냉각수·국소온도와 별도. 현재 IMU만 있다면 모의 또는 미연결.", "P0")
field("phase", "운전 단계", "enum", "gate", "operating context", "controller_state_or_explicit_demo_state", "stopped/starting/warming_up/steady/cooling_down", "정상 운전 모델은 steady에만. 코드 v1의 running에 매핑할 때 안정화 여부를 어댑터가 보장.", "P0")
field("engine_elapsed_s", "실제 시동 후 경과시간", "s", "alignment_context", "operating context", "timestamp_from_engine_start", "같은 시점끼리 점검 비교", "앱 수집 시작시간 elapsed_s와 같다고 가정 금지. 현재 v1 context에 미구현.", "P0")
field("battery_dc_v", "배터리/DC 계통 전압", "V_DC", "rule_optional_target", "O8;M3-1", "controller_or_isolated_DC_measurement", "O:12.5~15V; M:운전중 충전13.5~15V", "정지 상태/시동중/충전중 기준 분리; 12V는 공칭값.")
field("start_duration_s", "시동 소요시간·시도 횟수", "s_and_count", "run_summary_target", "O7;O23-24", "start_command_and_running_timestamps", "외기온·배터리 상태별 과거 기록", "Onan 1회 크랭킹15초/재시도2분은 절차 상한이며 학습 정상 평균 아님.")
field("surface_c", "베어링하우징 등 국소 표면온도", "degC", "future_target", "GenGuard addition", "contact_RTD_or_thermocouple_plus_interface", "설치 위치별 정상 분포; 보편 기준 없음", "추가 센서 필요. AHT20 주변온도/IMU 내부온도를 냉각수나 베어링 온도로 표시 금지.")
field("fuel_pct", "연료 잔량", "%", "rule", "O11;O24", "tank_level_sensor_or_manual", "용량·경사·운전계획별; 저연료 경보 OEM값 미확인", "진동/연료압만으로 잔량 확정 불가. 20%는 아래 시연 가정.", "P0")
field("oil_level", "오일량", "enum", "manual_or_switch_rule", "O13", "dipstick_or_level_sensor", "Onan 정지 후 제조사 절차로 ADD~FULL", "유압 정상이어도 오일량 점검 필요.")
field("coolant_level", "냉각수량", "enum", "manual_or_switch_rule", "O13;O18", "cold_level_check_or_level_sensor", "온도 조건에 맞는 탱크 표식", "냉각수 온도만으로 양을 측정하지 않음.")
field("leaks", "연료·오일·냉각수·배기 누설", "checklist", "manual_or_detector_rule", "O7;O13", "manual_or_dedicated_detector", "누설 없음", "센서 없으면 미점검. AI 정상점수로 누설없음 대체 금지.", "P0")
field("air_filter", "흡기·공기필터·막힘", "checklist", "manual_or_restriction_sensor", "O13;O16", "manual_or_differential_pressure", "청결/손상 없음; 지시기 기준 해당제품별", "상태 점검 및 정비주기. RPM만으로 필터막힘 단정 금지.")
field("belt_cooling_air", "벨트·냉각팬·통풍·라디에이터", "checklist", "manual", "O13;O17-18", "manual", "이완/손상/막힘 없음; 장력은 해당 벨트 절차", "온도 추세 이상 때 점검 후보.")
field("fuel_water_filter", "연료 수분·필터·호스", "checklist", "manual_or_switch_rule", "O20-22", "manual_or_water_in_fuel_switch", "수분/누유/손상 여부 및 교환 이력", "연료압과는 다른 정보.")
field("mounts_wiring", "고정볼트·방진마운트·배선·단자", "checklist", "manual", "O13", "manual", "풀림/손상/접촉불량 없음", "진동 급변시 센서 고정부터 확인.")
field("service_history", "정비·오일/필터 교환 이력", "record", "schedule_and_annotation", "O11;O33", "technician_entry", "시간 또는 달력주기 중 먼저 도래", "잔여 정비시간은 RUL과 다른 값. 고장 확정/수명 학습 라벨 아님.", "P0")
field("oil_temp_c", "윤활유 온도", "degC", "future_target", "dataset_optional", "controller_or_temperature_sensor", "선택 모델 공개 운전범위 미확인", "다른 엔진 데이터의 평균을 이 발전기 정상범위로 가져오지 않음.", "P2")
field("fuel_pressure", "연료 압력", "as_calibrated", "future_target", "dataset_optional", "controller_or_pressure_sensor", "회로 위치/엔진별, 현 자료로 미확정", "저압 공급계통과 고압 분사계통 구분 필수.", "P2")
field("coolant_pressure", "냉각수 압력", "as_calibrated", "future_target", "dataset_optional", "controller_or_pressure_sensor", "위치/온도/펌프/회로별, 현 자료로 미확정", "압력캡 방출 설정값을 운전 정상 평균으로 쓰지 않음.", "P2")
field("telemetry_quality", "연결·시각·누락·포화·출처", "status", "gate", "GenGuard addition", "device_and_gateway", "fresh/valid/compatible", "stale이면 unknown. 통신두절을 발전기 정지로 단정하지 않음.", "P0")
field("power_supply_ats", "상용/발전 전원·ATS 위치", "enum", "event_rule_optional", "original_user_problem", "ATS_controller_or_isolated_contacts", "명령과 실제 위치 비교; 선택 모델 기본 제공 아님", "전원 상실을 보고하려면 감시노드 독립/보조전원 필요. 로컬 저장만으로 마지막 통보 보장 불가.")
field("output_breaker_state", "출력 차단기 상태", "enum", "event_rule", "A3.4.2;O9;O26", "controller_or_isolated_contact", "open/closed/tripped/unknown", "엔진 운전과 실제 전력 공급을 구분. 고의 개방과 트립도 구별.", "P0")
field("loadside_voltage_v", "차단기 뒤 부하측 전압", "V_AC_RMS", "rule_optional", "O9;O26", "isolated_meter_at_identified_point", "공급 의도 및 결선에 따라 판단", "발전기 단자측 전압과 다른 측정점. 차단 후에도 발전기 단자전압은 정상일 수 있음.")
onan_fields = {"voltage_v", "current_a", "frequency_hz", "engine_rpm", "oil_bar", "coolant_c",
               "battery_dc_v", "active_power_kw", "start_duration_s", "oil_level", "coolant_level"}
for item in fields:
    item["reference_profile_id"] = "cummins-onan-7.5hdkal-1-reference" if item["id"] in onan_fields else None
    item["reference_normal_is_executable_rule"] = False
    if item["id"] in {"voltage_v", "current_a", "loadside_voltage_v"}:
        item["required_sample_metadata"] = ["timestamp", "phase_or_line_pair", "measurement_point", "selector_position"]
write("field-catalog.json", dict(schema_version="genguard.research.fields/2", runtime_drop_in=False,
      additional_asset_and_inspection_definitions="../army-coverage.json", fields=fields))

ranges = {
  "schema_version":"genguard.research.references/2", "operational_use_approved":False,
  "army_requirement":{
    "kind":"procurement_acceptance_requirement", "not_a_manufacturer_model":True,
    "rating":{"continuous_kw_min":10,"maximum_kw_min":11,"rpm":1800,"hz":60,"phases":3,"voltage_options_v":[[120,208],[240,416]]},
    "protection":[
      {"id":"overspeed","value_rated_pct":110,"derived_rpm":1980,"response_within_s":10,"action":"alarm_and_engine_stop","ref":"A3.4.1.2"},
      {"id":"low_oil","operator":"lte","original_value":0.6,"original_unit":"kg/cm² (원문)","interpreted_unit":"kgf/cm²","derived_bar":0.588399,"response_within_s":60,"action":"alarm_and_engine_stop","ref":"A3.4.1.3","conversion_note":"압력 단위를 kgf/cm²로 해석한 환산. 1kgf/cm²=0.980665bar."},
      {"id":"high_coolant","trip_setting_interval_c":[95,100],"response_within_s":60,"action":"alarm_and_engine_stop","ref":"A3.4.1.4","note":"95~100은 정상범위가 아니라 과열 보호 요구 구간. 정확한 장비 설정 확인 필요."},
      {"id":"overload","operator":"gte","value_rated_pct":110,"example_kw_if_rated_10kw":11,"response_within_s":60,"action":"circuit_disconnect","ref":"A3.4.2.2","note":"실제 명판 연속정격으로 계산. 구매요구는10kW이상."}
    ],
    "timing_note":"N초 이내 동작 요구는 앱이 N초 기다려야 한다는 뜻이 아님. 실제 보호기 신호 우선.",
    "digital_interface_guaranteed":False
  },
  "manufacturer_reference":{
    "model":"Cummins Onan 7.5HDKAL-1", "manual_scope":"HDKAL Spec B-J; 실물 spec/serial 미확인",
    "profile_id":"cummins-onan-7.5hdkal-1-reference",
    "rating":{"kw":7.5,"rating_kind":"intermittent","rpm":1800,"hz":60,"phases":1,"voltage_v":[120,240],"rated_current_a":{"120V":62.5,"240V":31.3},"source":"S1","note":"외기온·고도·연료 등 사양서 조건 및 출력 저감 별도. 이번 시연에서 저감 계산 미구현. 군 연속정격과 구분."},
    "operating_ranges":[
      {"field":"coolant_c","range":[71,91],"unit":"degC","kind":"manufacturer_gauge_range","condition":"부하·외기온 의존","ref":"O8/PDF12"},
      {"field":"oil_bar","range":[1.94,4.42],"unit":"bar","kind":"converted_manufacturer_gauge_range","condition":"정상 운전온도","original":"194~442kPa","ref":"O7/PDF11"},
      {"field":"battery_dc_v","range":[12.5,15],"unit":"V_DC","kind":"manufacturer_gauge_range","condition":"배터리 상태/충전상태 의존","ref":"O8/PDF12"},
      {"field":"battery_dc_v","range":[13.5,15],"unit":"V_DC","kind":"manufacturer_charging_check","condition":"엔진 운전 중 충전","ref":"M3-1/PDF19"}
    ],
    "voltage_regulation":{"no_to_full_load_pct":2.5,"constant_load_pct":1,"kind":"performance_spec_not_alarm","ref":"S1/PDF2"},
    "high_coolant_shutdown":{"value_c":106,"delay_s":None,"ref":"O23/PDF27;M2-4/PDF14"},
    "low_oil_shutdown":{"values_bar":[0.97,0.62],"kind":"document_conflict","refs":["O23/PDF27","M2-2/PDF12"],"operational_selected_value":None},
    "normal_population_averages_available":False,
    "note":"공식 문서의 참고값이며 실제 장비 등록·경보 설정 승인이 아님."
  }
}
write("reference-profiles.json", ranges)

rule_spec = [
  ("rpm_deviation","engine_rpm","outside",[1710,1890],10,"watch","정격 대비 ±5%를 택한 시연 규칙"),
  ("frequency_deviation","frequency_hz","outside",[57,63],10,"watch","정격 대비 ±5%를 택한 시연 규칙"),
  ("coolant_above_reference","coolant_c","gt",91,10,"watch","온도 상단은 문서값; 경보화·지속시간은 시연 가정"),
  ("oil_outside_reference","oil_bar","outside",[1.94,4.42],10,"watch","범위는 문서값; 경보화·지속시간은 시연 가정"),
  ("voltage_deviation","voltage_v","outside",[228,252],5,"watch","240V ±5% 선택, 제조사 경보 설정 아님"),
  ("fuel_low","fuel_pct","lt",20,5,"watch","20%는 시연 가정"),
  ("vibration_relative","vibration_ratio","gt",1.5,6,"watch","1.5배·6초는 개발 기준, 발전기 안전 한계 아님")
]
write("demo-rules.json", dict(schema_version="genguard.research.demo_rules/2", runtime_drop_in=False,
  model="Cummins Onan 7.5HDKAL-1 reference scenario", control_action="none",
  evaluation_precondition="유효/최신 값, 올바른 모델·단위·결선, phase=steady; 온도·유압은 정상 운전온도 조건",
  missing_policy="unknown; 0/직전 정상값으로 대체 금지",
  rules=[dict(id=a,field=b,op=c,value=d,persistence_s=e,severity=f,provenance=g) for a,b,c,d,e,f,g in rule_spec],
  derived_fields={"vibration_ratio":{"formula":"current_vector_ac_rms_g / approved_normal_vector_ac_rms_g",
       "precondition":"동일 장비·부착·샘플링·운전 조건의 승인된 정상 기준",
       "invalid_policy":"기준 미확보/0/invalid/다른설치면 unknown",
       "scenario_baseline_g":0.10,"baseline_source":"demo_assumption"}},
  model_policy="별도 정상 운전으로 threshold 보정 후 연속 창 판단. 점수는 고장 확률 아님.",
  unresolved_protection="저유압 OEM값 충돌 때문에 이 연구파일로 자동정지 기준 확정하지 않음"))

baseline = dict(engine_rpm=1800, frequency_hz=60, coolant_c=80, oil_bar=3.2,
                voltage_v=240, current_a=15.625, active_power_kw=3.75,
                load_pct=50, ambient_c=25, vibration_rms_g=0.10,
                envelope_cv=0.15, battery_dc_v=14.1, fuel_pct=65)
scenarios = []
def case(id, title, changes, seconds, expected, mechanism, note):
    scenarios.append(dict(id=id,title_ko=title,hold_s=seconds,changes=changes,
                         expected_ui=expected,mechanism=mechanism,note=note))
case("normal", "기준 운전", {}, 30,"관측 항목 기준 이탈 없음", "rules_and_model", "아래 기준값 모두 설명용 합성값. 실제 장비 정상 평균 아님.")
case("vibration", "다른 값 유지·진동 변화", {"vibration_rms_g":0.22,"envelope_cv":0.60},20,"진동 패턴 변화: 부착·접촉·고정 상태 우선 확인", "baseline_or_model", "실측 시에는 진동 값 두 개만 센서에서 대체하고 hybrid_demo로 표시. 베어링 고장 확정 금지.")
case("rpm", "회전수와 주파수 동반 증가", {"engine_rpm":1950,"frequency_hz":65},20,"회전수/주파수 기준 편차: 부하·조속 상태 확인", "demo_rule", "4극 동기식 관계를 유지한 합성 예. 1950rpm은 군 과속 보호1980rpm과 다름.")
case("coolant", "RPM 정상·냉각수 높음", {"coolant_c":96},30,"RPM 기준 이탈 없음 / 냉각수 문서상 범위 초과 / 종합 주의", "reference_range_and_demo_rule", "이 Onan 시연은 106℃ 정지 참고값 미도달. 군 프로필이면95~100℃ 보호요구구간이라 같은 문구 사용 금지.")
case("combined", "개별 문서 범위 안·평소 조합과 다름", {"coolant_c":89,"oil_bar":2.1},30,"표시 범위 안이지만 같은 부하의 과거 패턴과 차이: 냉각·윤활 점검 권고", "conditional_model_candidate", "검증된 모델이 실제 점수/지속조건을 통과할 때만 이 문구 출력. 스냅샷만으로 IF탐지 보장하지 않음.")
case("oem_alarm", "기존 보호기 경보 수신", {"oem_low_oil":True},1,"기존 컨트롤러 저유압 경보 수신", "event_rule", "AI 정상 점수와 관계없이 우선 표시. 값 예측과 실제 보호기 동작 구분.")
case("output_trip", "RPM 정상·출력 차단", {"output_breaker_state":"tripped","loadside_voltage_v":0,"current_a":0,"active_power_kw":0,"load_pct":0},1,"엔진 운전 중 / 출력 차단기 트립 / 부하측 전원 없음", "event_and_electrical_rule", "단자측 voltage_v=240V와 부하측0V는 측정점이 다름. 낮아진 부하는 전력차단 결과일 수 있고 단순새부하로 경보를 숨기지 않음.")
case("low_fuel", "연료 부족", {"fuel_pct":12},20,"연료 잔량 낮음: 보충 계획 확인", "demo_rule", "20%는 가정. 진동 변화에서 연료량을 추론한 결과 아님.")
case("telemetry_stale", "RPM 마지막 값 정상·현재 수신 끊김", {"quality_override":{"engine_rpm":"stale","coolant_c":"stale","oil_bar":"stale"}},15,"컨트롤러 데이터 미수신: 현재 상태 판정 불가", "quality_gate", "정상도 발전기 고장 확정도 아님. 진동 개별 판정은 남은 신호로 계속 가능.")
case("warming_up", "시동 후 워밍업", {"phase":"warming_up","coolant_c":35},30,"워밍업 중: 정상 운전 모델 판정 대기", "phase_gate", "cold조건은 학습/정상 온도 범위 비교에서 제외. OEM 경보 수신은 계속 우선. 시동전 예열램프와 구분.")
case("new_load", "학습하지 않은 부하", {"load_pct":90,"active_power_kw":6.75,"current_a":28.125},30,"학습 조건 부족: 이 부하의 패턴 판정 보류", "support_gate", "부하 변화 자체를 고장이라 하지 않음. 문서/정격 규칙은 계속 적용.")
case("inconsistent_rpm", "RPM만 변하고 독립 주파수는 유지", {"engine_rpm":1950},20,"RPM·주파수 불일치: 센서·시각·연동 확인", "cross_sensor_rule_candidate", "직결4극동기식의 독립 측정값에 한정. 주파수에서 계산한RPM과 비교하면 자기검증에 불과.")
case("repeat_trend", "동일 시점의 점검 추세", {"historical_coolant_at_10min_c":[79,80,82,85,89]},1,"최근 점검의 온도 상승 추세 확인", "run_summary_trend_candidate", "부하50%·외기25℃·시동후10분으로 조건 맞춘 합성 예. 과거 관측값을 그대로 iforest에 넣는 것만으로 시간 추세 학습 안 됨.")
write("demo-scenarios.json",dict(schema_version="genguard.research.scenarios/2", runtime_drop_in=False,
  model="Cummins Onan 7.5HDKAL-1", source_kind="scenario", is_example=True,
  baseline=baseline, baseline_origin="designer_selected_not_manufacturer_average", phase="steady",
  electrical_assumption="단상240V 균형/PF1 합성; kW=240*I/1000. 실물 계통 일반화 금지.",
  cases=scenarios))

rows = ["# 감시·점검·학습 항목 전체 목록", "", "A는 군 구매요구서, O는 Onan 운용설명서, M은 서비스 설명서입니다. 출처 URL은 README 및 sources-manufacturer.json 참조. P0=첫 화면/필수 흐름, P1=확장 점검, P2=해당 신호 확보 후 검토. 모든 항목의 센서가 현재 연결됐다는 뜻이 아닙니다.", "", "| ID / 항목 | 근거 | 학습 역할 | 정상 기준/수집 | 주의점 |", "|---|---|---|---|---|"]
for f in fields:
    rows.append(f"| `{f['id']}` {f['label_ko']} ({f['priority']}) | {f['requirement_origin']} | {f['learning_role']} | {f['reference_normal']} / {f['acquisition']} | {f['note']} |")
(ROOT / "FIELD_CATALOG.md").write_text("\n".join(rows)+"\n")
print(json.dumps({"fields":len(fields),"scenarios":len(scenarios),"files":4}))
