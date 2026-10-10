"""Frozen display scope, distinct measurement methods, and mock auxiliary values.

The sources are genguard-alarm-demo-v1/display-fields.json and profile.json.
This module never manufactures missing physical signals or model predictions.
"""
from __future__ import annotations

import copy
import math


AUX_FIELDS = {
    'voltage_ll_v': (0, 500), 'battery_v': (0, 30), 'fuel_pct': (0, 100),
    'humidity_pct': (0, 100), 'pf': (0, 1), 'engine_hours': (0, 1_000_000),
    'envelope_cv': (0, 5),
}
AUX_DEFAULTS = {
    'voltage_ll_v': 208, 'battery_v': 13.8, 'fuel_pct': 70,
    'humidity_pct': 47.7, 'pf': 0.8, 'engine_hours': 1024, 'envelope_cv': 0.12,
}

SOURCE_LABELS = {
    'scenario': '모의 값', 'derived': '계산값', 'live_imu': '실측 IMU',
    'recorded_imu': '실측 기록', 'unconnected': '미연결', 'manual': '미점검',
    'missing': '미수신',
}

_DEFINITIONS = [
    ('engine_rpm', '엔진 회전수', 'rpm', '운전', 'fixed', False, 0,
     '현재값 참고 확인 · 지속시간 미검증. 1,710–1,890 rpm은 시연 범위.'),
    ('coolant_c', '냉각수 온도', '°C', '핵심', 'trajectory', True, 1,
     '같은 부하·외기온·회전수에서 같은 운전 구간의 상승량·분당 상승률을 비교.'),
    ('oil_kgf_cm2', '윤활유 압력', 'kgf/cm²', '핵심', 'trajectory', True, 2,
     '시동 이후 정상 안정화 곡선과 비교. 따뜻해지며 자연스럽게 낮아지는 압력과 추가 하락을 구분.'),
    ('voltage_ll_v', '출력 전압 · 선간', 'V', '전기', 'fixed', False, 1,
     '208 V 선간 기준. 현재값 참고 확인 · 지속시간 미검증.'),
    ('frequency_hz', '출력 주파수', 'Hz', '전기', 'derived', False, 2,
     '직결 4극 발전기 가정: RPM÷30. 별도 주파수 센서값이나 독립 학습 입력이 아님.'),
    ('load_pct', '부하율', '%', '전기', 'fixed_context', False, 1,
     '정상 곡선 비교 조건. 정격 10 kW 기준 현재값 참고 확인 · 지속시간 미검증.'),
    ('ambient_c', '주변 온도', '°C', '환경', 'context', False, 1,
     '정상 운전 곡선의 비교 조건. 냉각수·윤활유·국소 표면온도를 대신하지 않음.'),
    ('vibration_rms_g', '진동 RMS · 중력 제거', 'g', '핵심', 'vibration', True, 3,
     '같은 센서 부착·수집 조건에서 진동 세기와 변화를 비교. 보편적인 고장 g 기준은 없음.'),
    ('envelope_cv', '진동 간헐성', '', '진동', 'vibration_pattern', False, 3,
     '2초 창 안 100 ms 진동 포락선의 변동 계수. 실측 진동 분류는 해당 실험의 학습 모델 사용.'),
    ('battery_v', '배터리 전압', 'V', '전기', 'fixed', False, 1,
     '12 V 계통 가정. 현재값 참고 확인 · 지속시간 미검증. 충전전류와 다른 값.'),
    ('fuel_pct', '연료 잔량', '%', '운전', 'fixed', False, 1,
     '잔량 20% 미만은 시연 점검 기준. 현재값 참고 확인 · 지속시간 미검증.'),
    ('pf', '역률', '', '전기', 'calculation_context', False, 2,
     '균형 3상 전류 환산 조건. 이번 고장 모델 학습·단독 경보 제외.'),
    ('active_power_kw', '출력', 'kW', '전기', 'derived', False, 2,
     '모의 부하율×10 kW÷100. 별도 전력계 측정값이 아님.'),
    ('current_a', '상전류 · 균형 가정', 'A', '전기', 'derived', False, 1,
     'P÷(√3×선간전압×역률). 독립 전류 측정이나 상불평형 판정이 아님.'),
    ('phase_currents_a', '상별 전류', 'A', '전기', 'unconnected', False, 1,
     '상별 독립 입력 미연결. 계산한 균형 전류를 실제 L1/L2/L3 계측으로 표시하지 않음.'),
    ('voltage_measurement_point', '전압 측정 위치', '', '운전', 'display', False, 0,
     '시연은 L1–L2 선간 208 V 선택. 상–중성점 120 V와 구분.'),
    ('current_selector_position', '전류 측정 상', '', '운전', 'display', False, 0,
     '시연 선택 위치 L1. 실제 선택 스위치 신호 미연결.'),
    ('engine_hours', '누적 운전시간', 'h', '운전', 'display', False, 1,
     '모의 누적시간 표시. 잔여수명 또는 제조사 정비 주기를 추정한 값이 아님.'),
    ('preheat_active', '예열', '', '운전', 'display', False, 0,
     '모의 운전 단계 표시. 실제 예열 회로의 신호가 아님.'),
    ('battery_charge_current_a', '배터리 충전전류', 'A', '전기', 'unconnected', False, 1,
     '군 요구서 충전계 표시 항목 보존. 배터리 전압에서 충전전류를 추정하지 않음.'),
    ('humidity_pct', '상대 습도', '% RH', '환경', 'display', False, 1,
     '환경 참고용 모의 표시. 이번 판정·학습 제외.'),
    ('surface_temperature_c', '국소 표면온도', '°C', '환경', 'unconnected', False, 1,
     '추가 센서 미연결. 냉각수나 주변 온도로 대체하지 않음.'),
    ('oil_temperature_c', '윤활유 온도', '°C', '운전', 'unconnected', False, 1,
     '유온 미수집. 같은 유온이었다는 설명이나 유온 기반 모델 판단을 생성하지 않음.'),
    ('oem_protection', '기존 보호기 신호', '', '보호', 'unconnected', False, 0,
     '기존 보호기 미연결. 앱 경고에서 실제 엔진 정지 발생을 추정하지 않음.'),
    ('breaker_state', '출력 차단기', '', '보호', 'unconnected', False, 0,
     '차단기 접점 미연결. 엔진 운전과 전력 공급 상태를 구분.'),
]

_MANUAL = [
    ('oil_level', '오일량'), ('coolant_level', '냉각수량'),
    ('leaks', '연료·오일·냉각수 누설'), ('air_filter', '흡기·공기필터'),
    ('fuel_filter', '연료필터'), ('cooling_airflow', '라디에이터·통풍'),
    ('belt_hose', '벨트·호스'), ('mounting', '체결·방진마운트'), ('wiring', '배선·단자'),
]

_CHECKS = {
    'engine_rpm': ['부하 변화와 RPM 센서 확인', '연료 공급·조속기 점검 요청'],
    'coolant_c': ['같은 부하·외기온·운전 구간인지 확인', '냉각수량·누수·라디에이터·냉각팬 점검'],
    'oil_kgf_cm2': ['유압 센서·배선 확인', '윤활유량·누유·필터·펌프 점검'],
    'load_pct': ['명판 정격과 부하율 계산 확인', '부하 분산·대체 전원 필요 여부 확인'],
    'voltage_ll_v': ['선간 측정 위치·결선 조건 확인', '부하 변화·배선·전압 조정기 점검'],
    'battery_v': ['12 V 계통 및 운전 상태 확인', '충전 계통·단자·배터리 점검'],
    'fuel_pct': ['잔량 표시·센서 확인', '연료 보충 계획 확인'],
}


def catalog():
    """All 25 frozen display fields, followed by 9 unperformed manual checks."""
    rows = []
    for key, label, unit, section, method, learning, decimals, note in _DEFINITIONS:
        rows.append({'key': key, 'label': label, 'unit': unit, 'section': section,
                     'method': method, 'learning': learning, 'decimals': decimals,
                     'note': note, 'basis': 'genguard-alarm-demo-v1/display-fields.json',
                     'checks': copy.deepcopy(_CHECKS.get(key, []))})
    rows.extend({'key': key, 'label': label, 'unit': '', 'section': '수기 점검',
                 'method': 'manual', 'learning': False, 'decimals': 0,
                 'note': '담당자 확인 필요. 센서값으로 점검 완료를 대신하지 않음.',
                 'basis': 'genguard-alarm-demo-v1/display-fields.json#manual_checks',
                 'checks': []} for key, label in _MANUAL)
    return rows


def _values(record):
    if not isinstance(record, dict):
        return {}
    return record.get('values', record)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fixed(key, value):
    """Point evidence only; no continuous hold-time claim is made here."""
    if not _number(value):
        return 'unavailable', '유효한 측정값 없음'
    urgent = ((key == 'engine_rpm' and value >= 1980)
              or (key == 'coolant_c' and value >= 100)
              or (key == 'oil_kgf_cm2' and value <= 0.6)
              or (key == 'load_pct' and value >= 110))
    if urgent:
        return 'urgent', '군 구매요구 보호 참고값 · 앱 알림 · 실제 보호동작 확인 아님'
    ranges = {'engine_rpm': (1710, 1890), 'oil_kgf_cm2': (2, 4.5),
              'voltage_ll_v': (197.6, 218.4), 'battery_v': (12.5, 15)}
    outside = key in ranges and not ranges[key][0] <= value <= ranges[key][1]
    warning = outside or (key == 'coolant_c' and value >= 92) or (key == 'load_pct' and value > 100) or (key == 'fuel_pct' and value < 20)
    return ('inspect' if warning else 'normal'), '시연 가정 · 현재값 참고 확인 · 지속시간 미검증'


def telemetry_for(context, latest, auxiliary=None, mode='scenario', vibration=None):
    """Compose a display inventory without inventing real sensor measurements.

    ``context``/``latest`` may be flat values or records with a ``values`` dict.
    Explicit None is retained. Defaults only seed omitted auxiliary mock fields.
    The caller overlays actual trajectory-model states on the relevant rows.
    """
    context_values, latest_values = _values(context), _values(latest)
    values = dict(context_values, **latest_values)
    aux = dict(AUX_DEFAULTS)
    aux.update({key: value for key, value in (auxiliary or {}).items() if key in AUX_FIELDS})
    values.update(aux)
    vibration = vibration or {}
    sources = {key: 'scenario' for key in values}
    quality = {}
    for record in (context, latest):
        if isinstance(record, dict):
            sources.update(record.get('sources') or {})
            quality.update(record.get('quality') or {})

    if mode in ('hybrid', 'replay'):
        source = vibration.get('source') or ('recorded_imu' if mode == 'replay' else sources.get('vibration_rms_g', 'missing'))
        if source not in ('live_imu', 'recorded_imu'):
            source = 'missing'
        if 'rms_g' in vibration or 'vibration_rms_g' in vibration:
            values['vibration_rms_g'] = vibration.get('rms_g', vibration.get('vibration_rms_g'))
        values['envelope_cv'] = vibration.get('envelope_cv')
        if source == 'missing':
            values['vibration_rms_g'] = None
            values['envelope_cv'] = None
        sources.update(vibration_rms_g=source, envelope_cv=source)
        if vibration.get('state') in ('stale', 'missing', 'invalid'):
            quality.update(vibration_rms_g=vibration['state'], envelope_cv=vibration['state'])

    rpm, load, voltage, pf = (values.get(key) for key in ('engine_rpm', 'load_pct', 'voltage_ll_v', 'pf'))
    values['frequency_hz'] = rpm / 30 if _number(rpm) else None
    values['active_power_kw'] = load / 10 if _number(load) else None
    power = values['active_power_kw']
    values['current_a'] = (power * 1000 / (math.sqrt(3) * voltage * pf)
                           if all(_number(v) for v in (power, voltage, pf)) and voltage > 0 and pf > 0 else None)
    phase = context_values.get('phase', context.get('phase') if isinstance(context, dict) else None)
    phase = phase or ('running' if _number(rpm) and rpm > 0 else 'stopped')
    if _number(rpm) and rpm == 0:
        phase = 'stopped'
    values.update(voltage_measurement_point='L1-L2', current_selector_position='L1',
                  preheat_active=phase in ('preheat', 'warming'))

    result = catalog()
    fixed_fields = {'engine_rpm', 'coolant_c', 'oil_kgf_cm2', 'voltage_ll_v', 'battery_v', 'fuel_pct', 'load_pct'}
    for row in result:
        key, method = row['key'], row['method']
        source = ('unconnected' if method == 'unconnected' else 'manual' if method == 'manual'
                  else 'derived' if method == 'derived' else sources.get(key, 'scenario'))
        value = None if method in ('unconnected', 'manual') else values.get(key)
        valid = value is not None and not (isinstance(value, (int, float)) and not isinstance(value, bool) and not _number(value))
        state = 'unavailable' if not valid else 'info'
        basis = row['basis']
        if valid and key in fixed_fields:
            if key in ('engine_rpm', 'oil_kgf_cm2') and phase not in ('running', 'stable'):
                state, basis = 'info', '운전 단계 확인 중 · 안정운전 고정 기준 적용 보류'
            else:
                state, basis = _fixed(key, value)
        if method == 'manual':
            state = 'unchecked'
        if quality.get(key) not in (None, 'ok', 'fresh'):
            state = 'unavailable'
            basis = '최신 유효 측정값 없음 · 현재 상태 판정 보류'
        if source == 'missing':
            state = 'unavailable'
        if key == 'envelope_cv':
            row['learning'] = mode == 'replay' and valid
        row.update(value=value if valid else None, source=source,
                   source_label=SOURCE_LABELS.get(source, '출처 확인 필요'), state=state, basis=basis,
                   judgment_scope='현재값 참고 확인 · 지속시간 미검증' if key in fixed_fields else row['note'],
                   quality=quality.get(key, 'ok' if valid else 'missing'))
    return result
