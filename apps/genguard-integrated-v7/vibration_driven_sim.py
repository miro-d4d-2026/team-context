"""Drive an explicitly synthetic generator from live IMU activity.

Only vibration is measured. Generator values, timing and pattern thresholds in
this module are demonstration assumptions, not a generator diagnostic model.
"""
from __future__ import annotations

from collections import deque
import math


VALUE_FIELDS = ('coolant_c', 'oil_kgf_cm2', 'engine_rpm', 'load_pct',
                'voltage_ll_v', 'battery_v', 'fuel_pct', 'ambient_c')
PHASE_LABELS = {'stopped': '정지', 'starting': '시동 중', 'running': '운전 중',
                'irregular': '구동 불안정 시연', 'coasting': '정지 중',
                'signal_lost': '센서 수신 대기', 'waiting': '진동 확인 중'}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


class VibrationDrivenSimulator:
    """Clock-based, two-window-confirmed simulation; safe to poll repeatedly."""

    def __init__(self):
        self.origin = self.last_now = self.next_history_at = None
        self.sim_time = 0.0
        self.pattern = 'unknown'
        self.candidate = None
        self.candidate_count = 0
        self.last_window = None
        self.last_window_at = None
        self.last_fresh = False
        self.ever_fresh = False
        self.active = False
        self.motion_started = 0.0
        self.start_rpm = 0.0
        self.coast_rpm = self.coast_load = 0.0
        self.thermal_at = 0.0
        self.thermal_c = self.thermal_target = 28.5
        self.thermal_tau = 60.0
        self.history = deque(maxlen=180)

    @staticmethod
    def _reference(baseline):
        baseline = baseline or {}
        reference = baseline.get('normal_values', {}).get('vibration_rms_g', baseline.get('rms_g'))
        if _finite(reference) and reference > 0:
            return float(reference), 'installation_baseline'
        return .055, 'demo_default'

    def _candidate_pattern(self, rms, cv, reference):
        # Demonstration thresholds allow the measured phone fixture's resting
        # background (~.005-.010 g); these are not generator fault thresholds.
        start = max(.025, reference * .45)
        stop = max(.015, reference * .30)
        if rms <= stop:
            return 'idle'
        if cv > .6:
            return 'intermittent'
        if rms >= reference * 1.8:
            return 'strong'
        if rms >= start:
            return 'normal'
        # The gap between start and stop prevents noise from repeatedly starting
        # a stopped demonstration or stopping an already running one.
        # Retain only the on/off state here, never an old strong/intermittent
        # label after the measured pattern that supported it has disappeared.
        return 'normal' if self.active else 'idle'

    def _values_at(self, stamp):
        age = max(0.0, stamp - self.motion_started)
        if self.active:
            rpm = self.start_rpm + (1800 - self.start_rpm) * _clamp(age / 8)
            load = 62 * _clamp((age - 8) / 8)
            if self.pattern == 'intermittent':
                # This oscillation is a scripted visual example, not measured RPM.
                factor = .68 + .22 * math.sin(stamp * math.pi / 2)
                rpm *= factor
                load *= factor
        else:
            fraction = 1 - _clamp(age / 4)
            rpm, load = self.coast_rpm * fraction, self.coast_load * fraction
        coolant = self.thermal_target + (self.thermal_c - self.thermal_target) * math.exp(
            -max(0.0, stamp - self.thermal_at) / self.thermal_tau)
        ratio = _clamp(rpm / 1800)
        return {'coolant_c': coolant, 'oil_kgf_cm2': 3.6 * ratio,
                'engine_rpm': rpm, 'load_pct': load, 'voltage_ll_v': 208 * ratio,
                'battery_v': 12.6 + 1.4 * ratio, 'fuel_pct': 78.0, 'ambient_c': 28.5}

    def _change_pattern(self, pattern):
        previous = self._values_at(self.sim_time)
        active = pattern not in ('idle', 'unknown')
        if active and not self.active:
            self.motion_started = self.sim_time
            self.start_rpm = previous['engine_rpm']
        elif self.active and not active:
            self.motion_started = self.sim_time
            self.coast_rpm, self.coast_load = previous['engine_rpm'], previous['load_pct']
        self.thermal_c = previous['coolant_c']
        self.thermal_at = self.sim_time
        self.thermal_target = 80.0 if active else 28.5
        self.thermal_tau = 90.0 if active else 60.0
        self.active, self.pattern = active, pattern

    def _advance(self, now, fresh):
        previous_now = self.last_now
        elapsed = now - previous_now
        # A missing interval never becomes invented operating time on reconnect.
        continuous = fresh and self.last_fresh and elapsed <= 5
        if self.next_history_at <= now:
            first = max(self.next_history_at, math.floor(now) - 179)
            # Keep sampling on this instance's fixed clock grid, not poll count.
            first = self.next_history_at + max(0, math.ceil(first - self.next_history_at))
            while first <= now:
                values = self._values_at(self.sim_time + max(0, first - previous_now)) if continuous else dict.fromkeys(VALUE_FIELDS)
                self.history.append({'elapsed_s': round(first - self.origin, 3),
                                     'valid': continuous, **values})
                first += 1
            self.next_history_at = first
        if continuous:
            self.sim_time += elapsed
        self.last_now = now

    def update(self, imu: dict, baseline: dict | None, now: float) -> dict:
        if not _finite(now):
            raise ValueError('now must be a finite monotonic timestamp')
        now = float(now)
        if self.origin is None:
            self.origin = self.last_now = now
            self.next_history_at = now + 1
        now = max(now, self.last_now)
        features = imu.get('features') or {}
        rms = features.get('vibration_rms_g', features.get('rms_g'))
        cv = features.get('envelope_cv')
        fresh = (imu.get('state') == 'fresh' and imu.get('source') == 'live_imu'
                 and _finite(rms) and rms >= 0 and _finite(cv) and cv >= 0
                 and imu.get('window_id') is not None)
        window = imu.get('window_id')
        new_window = fresh and window != self.last_window
        if new_window:
            self.last_window_at = now
        # Also defend against a caller incorrectly retaining a fresh duplicate.
        if fresh and self.last_window_at is not None and now - self.last_window_at > 5:
            fresh = False
        self._advance(now, fresh)
        reference, reference_source = self._reference(baseline)
        if fresh:
            self.ever_fresh = True
            if new_window:
                candidate = self._candidate_pattern(rms, cv, reference)
                if candidate == self.candidate:
                    self.candidate_count += 1
                else:
                    self.candidate, self.candidate_count = candidate, 1
                if self.candidate_count >= 2 and candidate != self.pattern:
                    self._change_pattern(candidate)
                self.last_window = window
        else:
            self.candidate, self.candidate_count = None, 0
        self.last_fresh = fresh
        if not fresh:
            phase = 'signal_lost' if self.ever_fresh else 'waiting'
        elif self.pattern == 'unknown':
            phase = 'waiting'
        elif self.active:
            phase = 'starting' if self.sim_time - self.motion_started < 8 else (
                'irregular' if self.pattern == 'intermittent' else 'running')
        else:
            phase = 'coasting' if self.sim_time - self.motion_started < 4 and self.coast_rpm > 0 else 'stopped'
        message = {
            'waiting': '실측 진동 2개 구간을 확인하면 발전기 모의 계측이 시작됩니다.',
            'signal_lost': '새 센서값이 없어 모의 계측을 보류합니다. 발전기 정지로 판단하지 않습니다.',
            'stopped': '진동이 멈춰 모의 RPM·전압·부하는 0입니다. 냉각수는 서서히 식습니다.',
            'coasting': '진동 정지를 확인해 모의 회전수·출력·유압이 내려가고 있습니다.',
            'starting': '실측 진동에 맞춰 모의 회전수·전압·유압이 상승한 뒤 부하가 연결됩니다.',
            'running': '진동에 연동한 모의 발전기가 운전 중입니다. 냉각수는 서서히 상승합니다.',
            'irregular': '간헐 진동에 연동한 구동 불안정 시연입니다. RPM·전압 변화는 모의 값입니다.',
        }[phase]
        if fresh and self.pattern == 'strong':
            message = '강한 실측 진동을 감지했습니다. 모의 RPM은 유지하며 실제 회전수 저하를 단정하지 않습니다.'
        return {'source': 'imu_driven_simulation', 'phase': phase, 'phase_label': PHASE_LABELS[phase],
                'pattern': self.pattern if fresh else 'unknown', 'values': self._values_at(self.sim_time),
                'elapsed_s': round(now - self.origin, 3), 'history': list(self.history),
                'message': message, 'fresh': fresh,
                'assumptions': {'time_compressed': True, 'reference_rms_g': reference,
                                'reference_source': reference_source, 'confirmation_windows': 2,
                                'start_threshold_g': max(.025, reference * .45),
                                'stop_threshold_g': max(.015, reference * .30)}}
