"""Versioned, causal telemetry features shared by training and inference."""
import math

DT = 2
HISTORY_SECONDS = 180
HISTORY_S = HISTORY_SECONDS
HORIZONS = [30, 60, 120, 180]
HORIZONS_S = HORIZONS
TARGETS = ['coolant_c', 'oil_pressure_kgf_cm2', 'voltage_v', 'frequency_hz',
           'engine_rpm', 'current_a', 'current_b', 'current_c', 'battery_v',
           'fuel_pct', 'fuel_pressure_bar']
CONTEXT = ['load_pct', 'ambient_c']
FIELDS = TARGETS + CONTEXT
LAGS = [10, 30, 60, 120, 180]
FEATURE_NAMES = FIELDS + [f'{key}_delta_{lag}s' for lag in LAGS for key in FIELDS]
NORMAL_TARGETS = ['coolant_c', 'oil_pressure_kgf_cm2', 'voltage_v', 'frequency_hz',
                  'engine_rpm', 'battery_v', 'fuel_pressure_bar']
NORMAL_FEATURE_NAMES = ['intercept', 'load_pct', 'ambient_c', 'load_ema60',
                        'load_ema180', 'load_squared_div100', 'load_ambient_div100']
LABELS = {'coolant_c': '냉각수 온도', 'oil_pressure_kgf_cm2': '윤활유 압력',
          'voltage_v': '출력 선간전압', 'frequency_hz': '출력 주파수',
          'engine_rpm': '엔진 회전수', 'current_a': 'A상 전류', 'current_b': 'B상 전류',
          'current_c': 'C상 전류', 'battery_v': '배터리 전압', 'fuel_pct': '연료 잔량',
          'fuel_pressure_bar': '연료 압력', 'load_pct': '부하율', 'ambient_c': '외기온'}
UNITS = dict(zip(FIELDS, ['°C', 'kgf/cm²', 'V', 'Hz', 'RPM', 'A', 'A', 'A',
                         'V', '%', 'bar', '%', '°C']))
PLAUSIBLE = dict(zip(FIELDS, [(-40, 180), (0, 15), (0, 500), (0, 100), (0, 4000),
                             (0, 150), (0, 150), (0, 150), (0, 35), (0, 100),
                             (0, 15), (0, 150), (-40, 70)]))


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build_features(history):
    """Current values + strictly backward differences. No scenario/event metadata."""
    if not history:
        raise ValueError('운전 이력이 없습니다.')
    latest = history[-1]
    end = latest['timestamp_s']
    if end - history[0]['timestamp_s'] < HISTORY_SECONDS - 1e-6:
        raise ValueError('180초 이력이 필요합니다.')
    result = [float(latest[k]) for k in FIELDS]
    for lag in LAGS:
        wanted = end - lag
        row = min(history, key=lambda r: abs(r['timestamp_s'] - wanted))
        if abs(row['timestamp_s'] - wanted) > DT + 1e-6:
            raise ValueError('이력에 수집 공백이 있습니다.')
        result.extend(float(latest[k]) - float(row[k]) for k in FIELDS)
    return result


def normal_features(history):
    latest = history[-1]
    start = latest['timestamp_s'] - HISTORY_SECONDS
    rows = [r for r in history if r['timestamp_s'] >= start]
    ema60 = ema180 = float(rows[0]['load_pct'])
    prev = rows[0]['timestamp_s']
    for row in rows[1:]:
        dt = row['timestamp_s'] - prev
        load = float(row['load_pct'])
        ema60 += (load - ema60) * (1 - math.exp(-dt / 60))
        ema180 += (load - ema180) * (1 - math.exp(-dt / 180))
        prev = row['timestamp_s']
    load, ambient = float(latest['load_pct']), float(latest['ambient_c'])
    return [1., load, ambient, ema60, ema180, load * load / 100, load * ambient / 100]
