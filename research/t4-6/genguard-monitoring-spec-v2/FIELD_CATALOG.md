# 감시·점검·학습 항목 전체 목록

A는 군 구매요구서, O는 Onan 운용설명서, M은 서비스 설명서입니다. 출처 URL은 README 및 sources-manufacturer.json 참조. P0=첫 화면/필수 흐름, P1=확장 점검, P2=해당 신호 확보 후 검토. 모든 항목의 센서가 현재 연결됐다는 뜻이 아닙니다.

| ID / 항목 | 근거 | 학습 역할 | 정상 기준/수집 | 주의점 |
|---|---|---|---|---|
| `voltage_v` 출력 교류 전압 (P0) | A3.4.3.1 | target | 선택 모델/결선별 정격; Onan 시연 L1-L2 240V / controller_or_isolated_meter | Army는 상별 L-N/L-L 선택 표시도 보존. 380V를 공통 기본값으로 쓰지 않음. |
| `current_a` 출력 교류 전류 (P0) | A3.4.3.1 | target_optional | 부하 의존; Onan 240V 정격 31.3A / controller_or_CT_power_meter | Army는 상별 전류. 전류만으로 kW 과부하 확정 금지. load와 중복되면 모델에서 제외. |
| `frequency_hz` 출력 주파수 (P0) | A3.4.3.2 | rule_and_optional_target | 60Hz 정격, 경보 허용폭 미확정 / controller_or_isolated_meter | 4극 동기식 시연에서 RPM/30과 비교. RPM과 중복되는 특징은 무조건 두 번 가중하지 않음. |
| `engine_rpm` 엔진 회전수 (P0) | A3.4.1.2;A3.4.3.2 | target | 1800rpm 정격; 제품별 실제 분포 별도 / controller_or_tachometer | 과속 보호 및 주파수/회전계 겸용 요구. 정격은 정상 평균이 아님. |
| `runtime_hours` 누적 운전시간 (P0) | A3.4.3.2 | maintenance_schedule | 비감소 기록, 정비 주기와 비교 / controller_or_run_state_counter | 부품별 마지막 정비일·정비후 운전시간 별도. 누적 시간이 높다고 고장 아님. |
| `preheat_on` 예열 표시 (P0) | A3.4.3.5 | state_rule | 예열 단계에서 상태 확인 / controller_or_isolated_status_input | 숫자 이상탐지 학습에서 제외. 예열시간 조건은 모델/외기온별. |
| `oil_bar` 윤활유 압력 (P0) | A3.4.1.3;A3.4.3.5 | target | Onan 정상 운전온도 1.94~4.42bar (O7) / controller_or_pressure_transducer | 압력과 오일량은 다름. 정지/시동/냉간에는 같은 기준 적용 금지. |
| `coolant_c` 냉각수 온도 (P0) | A3.4.1.4;A3.4.3.5 | target | Onan 71~91℃, 부하·외기온 의존 (O8) / controller_or_coolant_temperature_sensor | 국소 표면온도/주변온도와 대체 불가. |
| `battery_charge_reading` 배터리 충전계 (P0) | A3.4.3.5 | rule_or_optional_target | 계기의 실제 단위/종류 현장 확인 / controller_or_isolated_charging_measurement | 요구서는 충전계 종류를 특정하지 않음. 전압계라고 단정하지 않으며 DC전압은 별도 필드. |
| `voltage_selector` 상전압 선택 위치 (P0) | A3.4.3.3 | metadata | 선택한 상/결선 정보 / panel_or_manual | 스위치 자체 고장학습이 아니라 값 해석 문맥. |
| `current_selector` 상전류 선택 위치 (P0) | A3.4.3.4 | metadata | 선택한 상 정보 / panel_or_manual | 통합 스위치 허용. 디지털 표시에서 동일 기능 제공. |
| `oem_overspeed` 과속 경보/기관 정지 (P0) | A3.4.1.2 | event_rule | 비활성/활성/미수신 구분 / controller_or_isolated_alarm_contact | 실제 보호기 동작 상태와 앱의 추정 기준 초과를 별도로 표시. AI 입력 정답으로 사용 금지. |
| `oem_low_oil` 저유압 경보/기관 정지 (P0) | A3.4.1.3 | event_rule | 비활성/활성/미수신 구분 / controller_or_isolated_alarm_contact | 실제 보호기 동작 상태와 앱의 추정 기준 초과를 별도로 표시. AI 입력 정답으로 사용 금지. |
| `oem_high_coolant` 냉각수 과열 경보/기관 정지 (P0) | A3.4.1.4 | event_rule | 비활성/활성/미수신 구분 / controller_or_isolated_alarm_contact | 실제 보호기 동작 상태와 앱의 추정 기준 초과를 별도로 표시. AI 입력 정답으로 사용 금지. |
| `oem_overload` 과부하 회로 차단 (P0) | A3.4.2.2 | event_rule | 비활성/활성/미수신 구분 / controller_or_isolated_alarm_contact | 실제 보호기 동작 상태와 앱의 추정 기준 초과를 별도로 표시. AI 입력 정답으로 사용 금지. |
| `vibration_rms_g` 3축 합성 AC 진동 RMS (P0) | GenGuard addition | target | 장비·부착위치·RPM·부하별 초기 정상 기록 / live_imu | sqrt(var(ax)+var(ay)+var(az)); DC 제거. 기존 sqrt(mean axis variance)와 단위정의 혼합 금지. |
| `envelope_cv` 진동의 시간 변동 (P0) | GenGuard addition | target | 동일 부착 조건 정상 학습 / derived_from_live_imu | 2초 창/100ms RMS 구간 CV는 개발 정의. 간헐 진동 변화 탐지용, 고장부위 아님. |
| `vibration_peak_g` 진동 최대값 (P1) | GenGuard addition | candidate_target | 실측 정상 분포 / derived_from_live_imu | 충격/접촉/포화와 구분. RMS·첨도 등 무조건 모두 투입하지 않고 검증 선택. |
| `vibration_bands` 대역별 진동 에너지 (P1) | GenGuard addition | candidate_target | 실제 ODR·대역폭·필터와 함께 고정 / derived_from_live_imu | 현재 약208Hz 수집이면 Nyquist104Hz 미만; 센서 최대 ODR로 고주파 분석했다고 주장 금지. |
| `active_power_kw` 유효 출력 전력 (P0) | load context | context_source | Onan 정격 7.5kW 이내; 정상 운전점은 부하 의존 / controller_or_true_power_meter | PF·결선 반영된 계측값 사용. 전력에서 load_pct 계산. |
| `load_pct` 정격 대비 부하율 (P0) | load context | context | 100*kW/정격kW / derived_active_power_and_rating | 환경에 따른 디레이팅·전류 제한도 별개 확인. 미측정이면 임의 추정해 live라 하지 않음. |
| `ambient_c` 흡기/주변 온도 (P0) | operating context | context | 운전환경 범위와 학습 지원영역 / external_sensor_or_controller | 냉각수·국소온도와 별도. 현재 IMU만 있다면 모의 또는 미연결. |
| `phase` 운전 단계 (P0) | operating context | gate | stopped/starting/warming_up/steady/cooling_down / controller_state_or_explicit_demo_state | 정상 운전 모델은 steady에만. 코드 v1의 running에 매핑할 때 안정화 여부를 어댑터가 보장. |
| `engine_elapsed_s` 실제 시동 후 경과시간 (P0) | operating context | alignment_context | 같은 시점끼리 점검 비교 / timestamp_from_engine_start | 앱 수집 시작시간 elapsed_s와 같다고 가정 금지. 현재 v1 context에 미구현. |
| `battery_dc_v` 배터리/DC 계통 전압 (P1) | O8;M3-1 | rule_optional_target | O:12.5~15V; M:운전중 충전13.5~15V / controller_or_isolated_DC_measurement | 정지 상태/시동중/충전중 기준 분리; 12V는 공칭값. |
| `start_duration_s` 시동 소요시간·시도 횟수 (P1) | O7;O23-24 | run_summary_target | 외기온·배터리 상태별 과거 기록 / start_command_and_running_timestamps | Onan 1회 크랭킹15초/재시도2분은 절차 상한이며 학습 정상 평균 아님. |
| `surface_c` 베어링하우징 등 국소 표면온도 (P1) | GenGuard addition | future_target | 설치 위치별 정상 분포; 보편 기준 없음 / contact_RTD_or_thermocouple_plus_interface | 추가 센서 필요. AHT20 주변온도/IMU 내부온도를 냉각수나 베어링 온도로 표시 금지. |
| `fuel_pct` 연료 잔량 (P0) | O11;O24 | rule | 용량·경사·운전계획별; 저연료 경보 OEM값 미확인 / tank_level_sensor_or_manual | 진동/연료압만으로 잔량 확정 불가. 20%는 아래 시연 가정. |
| `oil_level` 오일량 (P1) | O13 | manual_or_switch_rule | Onan 정지 후 제조사 절차로 ADD~FULL / dipstick_or_level_sensor | 유압 정상이어도 오일량 점검 필요. |
| `coolant_level` 냉각수량 (P1) | O13;O18 | manual_or_switch_rule | 온도 조건에 맞는 탱크 표식 / cold_level_check_or_level_sensor | 냉각수 온도만으로 양을 측정하지 않음. |
| `leaks` 연료·오일·냉각수·배기 누설 (P0) | O7;O13 | manual_or_detector_rule | 누설 없음 / manual_or_dedicated_detector | 센서 없으면 미점검. AI 정상점수로 누설없음 대체 금지. |
| `air_filter` 흡기·공기필터·막힘 (P1) | O13;O16 | manual_or_restriction_sensor | 청결/손상 없음; 지시기 기준 해당제품별 / manual_or_differential_pressure | 상태 점검 및 정비주기. RPM만으로 필터막힘 단정 금지. |
| `belt_cooling_air` 벨트·냉각팬·통풍·라디에이터 (P1) | O13;O17-18 | manual | 이완/손상/막힘 없음; 장력은 해당 벨트 절차 / manual | 온도 추세 이상 때 점검 후보. |
| `fuel_water_filter` 연료 수분·필터·호스 (P1) | O20-22 | manual_or_switch_rule | 수분/누유/손상 여부 및 교환 이력 / manual_or_water_in_fuel_switch | 연료압과는 다른 정보. |
| `mounts_wiring` 고정볼트·방진마운트·배선·단자 (P1) | O13 | manual | 풀림/손상/접촉불량 없음 / manual | 진동 급변시 센서 고정부터 확인. |
| `service_history` 정비·오일/필터 교환 이력 (P0) | O11;O33 | schedule_and_annotation | 시간 또는 달력주기 중 먼저 도래 / technician_entry | 잔여 정비시간은 RUL과 다른 값. 고장 확정/수명 학습 라벨 아님. |
| `oil_temp_c` 윤활유 온도 (P2) | dataset_optional | future_target | 선택 모델 공개 운전범위 미확인 / controller_or_temperature_sensor | 다른 엔진 데이터의 평균을 이 발전기 정상범위로 가져오지 않음. |
| `fuel_pressure` 연료 압력 (P2) | dataset_optional | future_target | 회로 위치/엔진별, 현 자료로 미확정 / controller_or_pressure_sensor | 저압 공급계통과 고압 분사계통 구분 필수. |
| `coolant_pressure` 냉각수 압력 (P2) | dataset_optional | future_target | 위치/온도/펌프/회로별, 현 자료로 미확정 / controller_or_pressure_sensor | 압력캡 방출 설정값을 운전 정상 평균으로 쓰지 않음. |
| `telemetry_quality` 연결·시각·누락·포화·출처 (P0) | GenGuard addition | gate | fresh/valid/compatible / device_and_gateway | stale이면 unknown. 통신두절을 발전기 정지로 단정하지 않음. |
| `power_supply_ats` 상용/발전 전원·ATS 위치 (P1) | original_user_problem | event_rule_optional | 명령과 실제 위치 비교; 선택 모델 기본 제공 아님 / ATS_controller_or_isolated_contacts | 전원 상실을 보고하려면 감시노드 독립/보조전원 필요. 로컬 저장만으로 마지막 통보 보장 불가. |
| `output_breaker_state` 출력 차단기 상태 (P0) | A3.4.2;O9;O26 | event_rule | open/closed/tripped/unknown / controller_or_isolated_contact | 엔진 운전과 실제 전력 공급을 구분. 고의 개방과 트립도 구별. |
| `loadside_voltage_v` 차단기 뒤 부하측 전압 (P1) | O9;O26 | rule_optional | 공급 의도 및 결선에 따라 판단 / isolated_meter_at_identified_point | 발전기 단자측 전압과 다른 측정점. 차단 후에도 발전기 단자전압은 정상일 수 있음. |
