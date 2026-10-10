"""Presentation of explicitly synthetic telemetry driven by a physical IMU.

The real vibration model is retained. Generated temperature/RPM/pressure are
not evidence of a measured generator and never enter historical model training.
"""
from __future__ import annotations
import copy
from operation_catalog import telemetry_for, AUX_DEFAULTS

SIM_SOURCE = 'imu_driven_simulation'


def linked_overview(result, simulation, imu):
    result = copy.deepcopy(result)
    sim = copy.deepcopy(simulation)
    sim.update(enabled=True, time_scale_note='시연 속도 · 시동 약 8초, 수온은 수십 초에 걸쳐 상승·하강')
    result['linked_simulation'] = sim
    values, phase = sim['values'], sim['phase']
    lost = phase in ('signal_lost', 'waiting')
    transitional = phase in ('stopped', 'starting', 'coasting')
    old_vibration = next(c for c in result['analysis']['channels'] if c['field'] == 'vibration_rms_g')
    vibration = copy.deepcopy(old_vibration)
    if lost:
        vibration.update(state='unavailable', summary='최신 IMU 수신을 기다립니다. 정지 여부를 판단하지 않습니다.')
    elif sim['pattern'] == 'idle':
        vibration.update(state='normal', summary='정지 수준의 진동입니다. 모의 발전기를 정지하고 냉각합니다.')
    elif vibration['state'] == 'inspect':
        message = {'strong': '평소보다 진동이 커졌습니다. 부착 상태와 구동 상태를 점검하세요.',
                   'intermittent': '진동이 불규칙하게 반복됩니다. 실제 회전수와 구동 상태를 확인하세요.'}.get(sim['pattern'])
        if message:
            vibration.update(summary=message, reason=message)
    vibration['envelope_cv'] = (imu.get('features') or {}).get('envelope_cv')
    context = {k: values[k] for k in ('engine_rpm', 'load_pct', 'ambient_c')}
    context['phase'] = 'running' if phase == 'irregular' else phase
    auxiliary = dict(AUX_DEFAULTS, **{k: values[k] for k in AUX_DEFAULTS if k in values})
    rows = telemetry_for(context, values, auxiliary, mode='hybrid', vibration={
        'rms_g': vibration.get('current'), 'envelope_cv': vibration['envelope_cv'],
        'source': vibration['source'], 'state': vibration['state']})
    simulated_channels = []
    for row in rows:
        key = row['key']
        if key in ('vibration_rms_g', 'envelope_cv'):
            row.update(state=vibration['state'], note=vibration['summary'], learning=not lost,
                       method='installation_baseline', basis='사용자가 등록한 실제 진동 기준')
            if key == 'vibration_rms_g':
                row['analysis'] = copy.deepcopy(vibration)
            continue
        if row['source'] in ('unconnected', 'manual', 'missing'):
            continue
        # A calculated electrical value is still derived from synthetic inputs.
        row.update(source=SIM_SOURCE if row['source'] != 'derived' else 'derived',
                   source_label='진동 연동 모의' if row['source'] != 'derived' else '모의 계산값',
                   input_source=SIM_SOURCE, learning=False,
                   basis='IMU로 시동·정지·패턴을 구분한 시연 가정 · 실제 발전기 계측 아님')
        if key in ('coolant_c', 'oil_kgf_cm2'):
            row['note'] = ('가동 중 서서히 상승 · 정지 후 서서히 냉각' if key == 'coolant_c'
                           else '시동 후 유압 형성 · 정지 후 감소') + ' · 모의'
        if key == 'engine_rpm':
            row['note'] = '진동으로 정한 모의 구동 상태 · 실제 RPM 측정 아님'
        if key == 'current_a' and values['load_pct'] == 0:
            row['value'] = 0.0
        if lost:
            row.update(state='unavailable', quality='stale', note='IMU 수신 대기 · 마지막 모의 값, 진행 보류')
        elif transitional and key in ('engine_rpm', 'oil_kgf_cm2', 'voltage_ll_v', 'battery_v'):
            row.update(state='info', note=row.get('note', '') + ' · 시동·정지 중 운전 기준 보류')
        if key in ('coolant_c', 'oil_kgf_cm2'):
            channel = {'field': key, 'label': row['label'], 'unit': row['unit'],
                       'current': row['value'], 'previous': None, 'state': 'unavailable' if lost else 'normal',
                       'method': SIM_SOURCE, 'method_label': '진동 연동 모의', 'source': SIM_SOURCE,
                       'reference_source': None, 'summary': row['note'], 'reason': row['note'],
                       'intervals': [], 'checks': [], 'observed_rate': None, 'expected_rate': None,
                       'observed_delta': None, 'expected_delta': None, 'rate_delta': None,
                       'model_expected': None, 'expected_range': None, 'delta': None}
            row.update(analysis=channel, method=SIM_SOURCE)
            simulated_channels.append(channel)
    alerts = []
    if not lost and not transitional:
        for row in rows:
            if row['source'] in (SIM_SOURCE, 'derived') and row['state'] in ('inspect', 'urgent'):
                alerts.append({'id': 'linked_' + row['key'], 'field': row['key'],
                    'title': row['label'] + ' · 모의 값 변화', 'source': SIM_SOURCE,
                    'severity': 'urgent' if row['state'] == 'urgent' else 'watch',
                    'reason': '간헐 진동에 연동한 모의 구동 불안정입니다. 실제 계측값이 아닙니다.',
                    'checks': ['실제 RPM·출력 계측은 추가 연결 필요']})
    recommendations = []
    if vibration['state'] == 'inspect' and not lost:
        recommendations.append({'field': 'vibration_rms_g', 'title': '실측 진동 점검',
            'reason': vibration['summary'], 'checks': ['IMU 부착 상태 확인', '실제 구동 상태와 회전수 확인']})
    status = 'unavailable' if lost else 'urgent' if any(a['severity'] == 'urgent' for a in alerts) else \
             'inspect' if alerts or recommendations else 'unavailable' if vibration['state'] == 'unavailable' and not transitional else 'normal'
    headline = ('센서 연결을 확인하세요' if lost else
                '진동 변화가 감지됐습니다' if recommendations else
                '모의 구동 값이 불안정합니다' if alerts else
                '진동을 기준으로 모의 운전 중입니다' if phase in ('running', 'irregular') else
                '모의 시동 중입니다' if phase == 'starting' else '모의 발전기 정지 · 냉각 중')
    if status == 'unavailable' and not lost:
        headline = '모의 운전 중 · 정상 진동 기준 등록 필요'
    result['telemetry'] = rows
    result['reference_id'] = None
    result['analysis'] = {'state': status, 'channels': simulated_channels + [vibration],
        'recommendations': recommendations, 'reference_id': None,
        'conditions': {'matched': False, 'reason': 'linked_simulation_has_no_measured_generator_history'},
        'model': {'training_record_count': 0, 'method': 'simulation_rules_and_live_vibration_baseline'},
        'note': '진동은 학습한 실측 기준과 비교합니다. 나머지는 모의 운전 규칙이며 이전 발전기 기록의 AI 판단이 아닙니다.'}
    result['operational_status'] = {'state': status, 'headline': headline,
        'summary': vibration['summary'] if recommendations else sim['message'],
        'alerts': alerts, 'missing_active': []}
    result['scope'] = '진동만 실제 IMU 계측입니다. RPM·수온·유압·전기 값은 진동에 연동한 시연값이며 발전기 측정·예측 결과나 학습 자료가 아닙니다.'
    return result
