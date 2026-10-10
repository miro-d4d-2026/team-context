"""Read-only edge inference. Learned weights stay frozen during monitoring."""
from collections import deque
from pathlib import Path
import json
import math
import numpy as np
from contract import (FIELDS, TARGETS, FEATURE_NAMES, NORMAL_TARGETS, HORIZONS,
                      HISTORY_SECONDS, LABELS, UNITS, PLAUSIBLE, finite,
                      build_features, normal_features)

ROOT = Path(__file__).resolve().parent


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def crossing_time(points, threshold, direction):
    """Interpolate within learned horizon only; never extrapolate a shutdown ETA."""
    def beyond(v):
        return v >= threshold if direction == 'high' else v <= threshold
    if not points or beyond(points[0][1]):
        return 0.0 if points else None
    for (ta, a), (tb, b) in zip(points, points[1:]):
        if beyond(b):
            return float(ta + (tb - ta) * (threshold - a) / (b - a)) if b != a else float(tb)
    return None


class Predictor:
    def __init__(self, model_path=None):
        self.model = load_json(model_path or ROOT / 'models/telemetry.json')
        m = self.model
        if m.get('targets') != TARGETS or m.get('horizons_s') != HORIZONS:
            raise ValueError('예측 모델 항목/시간 계약이 맞지 않습니다.')
        if m.get('feature_names') != FEATURE_NAMES:
            raise ValueError('예측 모델 입력 항목이 맞지 않습니다.')
        self.layers = []
        width = len(FEATURE_NAMES)
        for layer in m['layers']:
            w, b = np.asarray(layer['weights'], float), np.asarray(layer['bias'], float)
            if w.ndim != 2 or w.shape[0] != width or b.shape != (w.shape[1],):
                raise ValueError('모델 행렬 크기 오류')
            if not np.isfinite(w).all() or not np.isfinite(b).all():
                raise ValueError('모델에 유한하지 않은 값이 있습니다.')
            if layer['activation'] not in ('tanh', 'linear', 'relu'):
                raise ValueError('지원하지 않는 활성화 함수')
            self.layers.append((w, b, layer['activation']))
            width = len(b)
        if width != len(TARGETS) * len(HORIZONS):
            raise ValueError('모델 출력 크기 오류')
        for key, size in [('x_mean', len(FEATURE_NAMES)), ('x_scale', len(FEATURE_NAMES)),
                          ('y_mean', width), ('y_scale', width)]:
            arr = np.asarray(m[key], float)
            if arr.shape != (size,) or not np.isfinite(arr).all():
                raise ValueError('모델 정규화 값 오류: ' + key)
            if key.endswith('scale') and (arr <= 0).any():
                raise ValueError('모델 정규화 척도는 양수여야 합니다.')
        self.normal = m['normal_model']

    def forecast(self, history):
        m = self.model
        x = (np.asarray(build_features(history)) - m['x_mean']) / m['x_scale']
        for w, b, activation in self.layers:
            x = x @ w + b
            if activation == 'tanh':
                x = np.tanh(x)
            elif activation == 'relu':
                x = np.maximum(x, 0)
        delta = (x * m['y_scale'] + m['y_mean']).reshape(len(HORIZONS), len(TARGETS))
        current = np.asarray([history[-1][key] for key in TARGETS])
        values = delta + current
        half_width = np.asarray(m['calibration']['half_width'], float)
        if half_width.shape != values.shape or not np.isfinite(values).all() or not np.isfinite(half_width).all() or (half_width < 0).any():
            raise ValueError('예측값/검증 잔차 구간 오류')
        return [{'ahead_s': h, 'values': dict(zip(TARGETS, values[i].tolist())),
                 'lower': dict(zip(TARGETS, (values[i] - half_width[i]).tolist())),
                 'upper': dict(zip(TARGETS, (values[i] + half_width[i]).tolist()))}
                for i, h in enumerate(HORIZONS)]

    def anomaly(self, history):
        m = self.normal
        expected = np.asarray(normal_features(history)) @ np.asarray(m['weights'])
        expected += np.asarray(m.get('residual_mean', [0.] * len(m['targets'])))
        scale = np.asarray(m['residual_scale'])
        observed = np.asarray([history[-1][k] for k in m['targets']])
        z = (observed - expected) / scale
        score = float(np.sqrt(max(0, z @ np.asarray(m['precision']) @ z) / len(z))) if 'precision' in m else float(np.max(np.abs(z)))
        evidence = [{'field': m['targets'][i], 'label': LABELS[m['targets'][i]],
                     'value': float(observed[i]), 'expected': float(expected[i]),
                     'z': float(z[i]), 'unit': UNITS[m['targets'][i]]}
                    for i in np.argsort(-np.abs(z))[:3]]
        return score, float(m['threshold']), evidence


class Monitor:
    def __init__(self, model_path=None, profile_path=None):
        self.predictor = Predictor(model_path)
        self.profile = load_json(profile_path or ROOT / 'profile.json')
        self.reset()

    def reset(self):
        self.history = deque(maxlen=1000)
        self.run_id = None
        self.last_t = None
        self.timers = {}
        self.previous_state = 'collecting'
        self.recovery_since = None

    def _hold(self, key, active, timestamp, duration):
        if not active:
            self.timers.pop(key, None)
            return False
        self.timers.setdefault(key, timestamp)
        return timestamp - self.timers[key] >= duration

    def _base(self):
        return {'state': 'unknown', 'reason': '', 'history_seconds': 0,
                'anomaly_score': None, 'anomaly_threshold': None, 'evidence': [],
                'forecast': [], 'crossings': [], 'alerts': [], 'guidance': [],
                'observation': '', 'stop_eta_s': None, 'failure_probability': None,
                'model_id': self.predictor.model.get('model_id', 'genguard-forecast-v1'),
                'model_training_source': 'synthetic', 'online_training': False,
                'forecast_assumption': '최근 관측 흐름에 따른 모의 예측. 미래 부하 조작·정비 조치를 미리 알지 못합니다.',
                'interval_meaning': '별도 보정 운전 기록의 항목·시간별 절대 예측오차 구간. 고장 확률이 아닙니다.'}

    def update(self, sample, now_s=None):
        out = self._base()
        if not isinstance(sample, dict) or not finite(sample.get('timestamp_s')):
            self.history.clear(); self.timers.clear()
            out['reason'] = '유효한 측정 시각이 필요합니다.'
            out['observation'] = out['reason']
            return out
        t = sample['timestamp_s']
        if self.run_id != sample.get('run_id'):
            self.reset(); self.run_id = sample.get('run_id')
        if self.last_t is not None and t <= self.last_t:
            out['reason'] = '중복 또는 역순 시각: 새 추론을 보류합니다.'
            out['observation'] = out['reason']
            return out
        gap = self.last_t is not None and t - self.last_t > self.profile['max_gap_s']
        self.last_t = t
        if gap:
            self.history.clear(); self.timers.clear()
        if now_s is not None and (not finite(now_s) or not 0 <= now_s - t <= self.profile['max_gap_s']):
            self.history.clear(); self.timers.clear()
            out['reason'] = '오래되었거나 미래 시각인 계측값입니다.'
            out['observation'] = out['reason']
            return out
        invalid = [k for k in FIELDS if not finite(sample.get(k)) or
                   not PLAUSIBLE[k][0] <= sample[k] <= PLAUSIBLE[k][1]]
        if sample.get('profile_id', self.profile['profile_id']) != self.profile['profile_id']:
            self.history.clear(); self.timers.clear()
            out['reason'] = '장비 프로필 불일치: 이 장비의 예측·경고 정책 적용을 보류합니다.'
            out['observation'] = out['reason']
            return out
        phase = sample.get('phase')
        if phase != 'running':
            self.history.clear(); self.timers.clear()
            out['reason'] = '예열·정지 중: 안정 운전 모델의 분석을 보류합니다.'
            out['observation'] = out['reason']
            return out
        # Independent valid channels still issue current urgent alarms while AI input is incomplete.
        for i, rule in enumerate(self.profile['rules']):
            key = rule['field']; value = sample.get(key)
            valid = key not in invalid and finite(value)
            def reached(level):
                if not valid or level not in rule:
                    return False
                return value >= rule[level] if rule['direction'] == 'high' else value <= rule[level]
            urgent = reached('urgent')
            inspection = self._hold(f'fixed:{i}', reached('inspection'), t, self.profile['alarm_hold_s'])
            if urgent or inspection:
                level = 'urgent' if urgent else 'inspection'
                out['alerts'].append({'field': key, 'level': level, 'source': 'fixed_rule',
                                      'message': f'{LABELS[key]}: 현재 시연 {"긴급" if urgent else "점검"} 기준 도달'})
                out['guidance'].append(rule['guidance'])
        if invalid:
            self.history.clear()
            self.timers = {k: v for k, v in self.timers.items() if k.startswith('fixed:')}
            out['reason'] = '계측 누락/형식/범위 확인: ' + ', '.join(invalid)
            return self._finish(out, t, ready=False)
        if gap:
            self.history.clear()
            self.timers = {k: v for k, v in self.timers.items() if k.startswith('fixed:')}
        self.history.append({k: sample[k] for k in FIELDS + ['timestamp_s']})
        while len(self.history) > 1 and t - self.history[1]['timestamp_s'] >= HISTORY_SECONDS:
            self.history.popleft()
        span = t - self.history[0]['timestamp_s']
        out['history_seconds'] = min(HISTORY_SECONDS, span)
        if span < HISTORY_SECONDS:
            out['state'] = 'collecting'
            out['reason'] = f'연속 계측 이력 수집 중 · {int(span)}/{HISTORY_SECONDS}초'
            return self._finish(out, t, ready=False)
        domain = self.predictor.model.get('domain', {})
        domain = domain.get('context_limits', domain)
        outside = [k for k, bounds in domain.items() if k in sample and isinstance(bounds, list)
                   and len(bounds) == 2 and not bounds[0] <= sample[k] <= bounds[1]]
        if outside:
            out['reason'] = '학습 운전 조건 밖: ' + ', '.join(outside)
            self.history.clear()
            self.timers = {k: v for k, v in self.timers.items() if k.startswith('fixed:')}
            return self._finish(out, t, ready=False)
        try:
            out['forecast'] = self.predictor.forecast(list(self.history))
            score, threshold, evidence = self.predictor.anomaly(list(self.history))
            out.update(anomaly_score=score, anomaly_threshold=threshold, evidence=evidence)
        except (ValueError, OverflowError, FloatingPointError) as exc:
            out['reason'] = '모델 계산 보류: ' + str(exc)
            self.timers = {k: v for k, v in self.timers.items() if k.startswith('fixed:')}
            return self._finish(out, t, ready=False)
        anomalous = self._hold('anomaly', score > threshold, t, self.profile['alarm_hold_s'])
        if anomalous:
            leading = evidence[0]
            out['alerts'].append({'field': leading['field'], 'level': 'inspection', 'source': 'learned_baseline',
                                  'message': f'{leading["label"]}: 조건별 정상 예상값과 다른 변화가 지속됩니다.'})
            out['guidance'].extend(r['guidance'] for r in self.profile['rules'] if r['field'] == leading['field'])
        for i, rule in enumerate(self.profile['rules']):
            field = rule['field']
            if field not in TARGETS:
                continue
            for level in ('inspection', 'urgent'):
                if level not in rule:
                    continue
                paths = {kind: [(0., float(sample[field]))] + [(p['ahead_s'], p[kind][field]) for p in out['forecast']]
                         for kind in ('values', 'lower', 'upper')}
                eta = crossing_time(paths['values'], rule[level], rule['direction'])
                active = eta is not None and eta > 0
                key = f'forecast:{i}:{level}'
                sustained = self._hold(key, active, t, self.profile['forecast_hold_s'])
                if active:
                    early_kind, late_kind = ('upper', 'lower') if rule['direction'] == 'high' else ('lower', 'upper')
                    out['crossings'].append({'field': field, 'label': LABELS[field], 'threshold': rule[level],
                                             'direction': rule['direction'], 'level': level, 'eta_s': round(eta, 1),
                                             'earliest_s': crossing_time(paths[early_kind], rule[level], rule['direction']),
                                             'latest_s': crossing_time(paths[late_kind], rule[level], rule['direction']),
                                             'sustained': sustained, 'kind': 'demo_threshold_crossing'})
                    if sustained:
                        out['alerts'].append({'field': field, 'level': 'inspection', 'source': 'learned_forecast',
                                              'message': f'{LABELS[field]}: 현재 추이 유지 시 시연 {"긴급" if level == "urgent" else "점검"} 기준에 약 {round(eta)}초 뒤 도달 예상'})
                        out['guidance'].append(rule['guidance'])
        # Phase-current imbalance is a transparent engineering rule, not a learned fault label.
        currents = [sample[k] for k in ('current_a', 'current_b', 'current_c')]
        imbalance = 100 * max(abs(v - np.mean(currents)) for v in currents) / max(float(np.mean(currents)), .1)
        out['current_imbalance_pct'] = float(imbalance)
        imbalance_path = [(0., float(imbalance))]
        for p in out['forecast']:
            currents_pred = [p['values'][k] for k in ('current_a', 'current_b', 'current_c')]
            mean = float(np.mean(currents_pred))
            imbalance_path.append((p['ahead_s'], 100 * max(abs(v - mean) for v in currents_pred) / max(mean, .1)))
        imbalance_eta = crossing_time(imbalance_path, 10, 'high')
        predicting_imbalance = imbalance_eta is not None and imbalance_eta > 0
        imbalance_sustained = self._hold('forecast:imbalance', predicting_imbalance, t, self.profile['forecast_hold_s'])
        if predicting_imbalance:
            out['crossings'].append({'field': 'current_a', 'label': '상별 전류 편차', 'threshold': 10,
                                     'direction': 'high', 'level': 'inspection', 'eta_s': round(imbalance_eta, 1),
                                     'earliest_s': None, 'latest_s': None, 'sustained': imbalance_sustained,
                                     'kind': 'derived_forecast_imbalance_pct'})
            if imbalance_sustained:
                out['alerts'].append({'field': 'current_a', 'level': 'inspection', 'source': 'learned_forecast',
                                      'message': f'전류 예측 기준 상별 편차가 약 {round(imbalance_eta)}초 뒤 시연 점검값 10%에 도달 예상'})
                out['guidance'].append('상별 연결 부하와 전류 측정 상태를 확인하세요.')
        if self._hold('imbalance', imbalance > 10, t, 10):
            out['alerts'].append({'field': 'current_a', 'level': 'inspection', 'source': 'demo_imbalance_rule',
                                  'message': '상별 전류 편차가 시연 점검값 10%를 초과해 지속됩니다.'})
            out['guidance'].append('상별 연결 부하와 전류 측정 상태를 확인하세요.')
        if score > threshold:
            out['reason'] = '조건 대비 변화 지속' if anomalous else '순간 변화 관찰 · 지속 여부 확인 중'
        else:
            out['reason'] = '학습한 정상 운전 패턴과 유사합니다.'
        out['state'] = 'normal'
        return self._finish(out, t, ready=True)

    def _finish(self, out, timestamp, ready):
        levels = {a['level'] for a in out['alerts']}
        if 'urgent' in levels:
            out['state'] = 'urgent'; self.recovery_since = None
        elif 'inspection' in levels:
            out['state'] = 'inspection'; self.recovery_since = None
        elif ready and self.previous_state in ('inspection', 'urgent'):
            if self.recovery_since is None:
                self.recovery_since = timestamp
            if timestamp - self.recovery_since < self.profile['recovery_hold_s']:
                out['state'] = 'inspection'
                out['reason'] = '회복 추이 확인 중 · 20초 안정 유지 후 정상 복귀'
                out['guidance'].append('추가 상승·하락 없이 안정되는지 관찰하세요.')
            else:
                self.recovery_since = None
        elif not ready:
            self.recovery_since = None
        self.previous_state = out['state']
        out['guidance'] = list(dict.fromkeys(out['guidance']))
        out['crossings'].sort(key=lambda x: x['eta_s'])
        out['observation'] = out['alerts'][0]['message'] if out['alerts'] else out['reason']
        return out
