"""Synthetic whole-run fixtures, never real generator measurements.

Seeds identify independent simulated runs. No run or window may be shared
between fitting, calibration and final evaluation.
"""
from __future__ import annotations

import numpy as np

SCENARIOS = (
    ("normal", "정상 유지", "작은 계측 잡음만 있는 안정 운전"),
    ("coolant_drift", "수온 점진 상승", "일정 부하에서 수온이 서서히 상승하는 가정"),
    ("oil_drift", "유압 점진 하락", "일정 부하에서 윤활유 압력이 서서히 하락하는 가정"),
    ("joint_drift", "수온·유압 동시 악화", "두 계측값이 함께 악화하는 가정"),
    ("transient", "일시 변화 후 복귀", "짧은 수온 상승과 유압 하락 후 정상으로 복귀"),
    ("load_change", "부하 변경", "부하 변경 뒤 수온과 유압이 다른 운전점에 안정화"),
    ("step", "계측값 갑작스런 변경", "웹 값을 한 번에 바꾼 경우. 미래 징후로 취급하지 않음"),
    ("recovery", "악화 후 회복", "악화하다가 경계 전에 회복하는 가정"),
)


def scenario_catalog() -> list[dict]:
    return [{"id": name, "label": label, "description": description,
             "source": "scenario", "duration_s": 900, "step_s": 10}
            for name, label, description in SCENARIOS]


def _noise(rng, size, scale):
    values = np.zeros(size)
    for i in range(1, size):
        values[i] = 0.65 * values[i - 1] + rng.normal(0, scale)
    return values


def generate_scenario(name: str, seed: int = 2026) -> list[dict]:
    """Return 15 simulated minutes at 10-second spacing.

    Shape equations create the DATA, not the forecast. The learner gets only
    historical values, never scenario name, future onset or random parameters.
    """
    names = [x[0] for x in SCENARIOS]
    if name not in names:
        raise ValueError(f"Unknown scenario: {name}")
    rng = np.random.default_rng(np.random.SeedSequence([seed, names.index(name)]))
    t = np.arange(0, 901, 10, dtype=float)
    n = len(t)
    load = np.full(n, rng.uniform(50, 65))
    ambient = np.full(n, rng.uniform(25, 31))
    # Same declared normal background as the frozen v1 model's generator.py,
    # so comparisons to existing anomaly detection do not start off-normal.
    # The drift/intervention equations below are new synthetic assumptions.
    coolant = np.full(n, 80 + 0.10 * (load[0] - 62) + 0.25 * (ambient[0] - 28.5)
                      + rng.normal(0, 0.05))
    oil = np.full(n, 3.2 - 0.004 * (load[0] - 62) - 0.004 * (ambient[0] - 28.5)
                  + rng.normal(0, 0.003))
    onset = rng.uniform(160, 240)
    elapsed = np.maximum(t - onset, 0)
    coolant_rate = rng.uniform(0.017, 0.027)
    oil_rate = rng.uniform(0.0018, 0.0028)
    acceleration = rng.uniform(0, 0.000025)
    rising = coolant_rate * elapsed + acceleration * elapsed ** 2 / 2
    falling = oil_rate * elapsed + acceleration * elapsed ** 2 / 25
    if name in ("coolant_drift", "joint_drift"):
        coolant += rising
    if name in ("oil_drift", "joint_drift"):
        oil -= falling
    if name == "transient":
        pulse = np.exp(-0.5 * ((t - rng.uniform(400, 500)) / rng.uniform(35, 55)) ** 2)
        coolant += rng.uniform(4, 8) * pulse
        oil -= rng.uniform(0.35, 0.75) * pulse
    if name == "load_change":
        load_delta = rng.uniform(10, 12)
        load[t >= 300] += load_delta
        settled = 1 - np.exp(-np.maximum(t - 300, 0) / 55)
        coolant += 0.10 * load_delta * settled
        oil -= 0.004 * load_delta * settled
    if name == "step":
        coolant[t >= 300] += rng.uniform(4, 7)
        oil[t >= 300] -= rng.uniform(0.35, 0.60)
    if name == "recovery":
        # No forecaster can know an unannounced intervention in advance.
        # Some pre-recovery advisories may therefore be false in hindsight.
        peak_t = rng.uniform(460, 520)
        rise_at_peak = coolant_rate * (peak_t - onset)
        fall_at_peak = oil_rate * (peak_t - onset)
        decay = np.exp(-np.maximum(t - peak_t, 0) / 65)
        coolant += np.where(t <= peak_t, coolant_rate * elapsed, rise_at_peak * decay)
        oil -= np.where(t <= peak_t, oil_rate * elapsed, fall_at_peak * decay)
    coolant += _noise(rng, n, 0.035)
    oil += _noise(rng, n, 0.003)
    rpm = 1800 - 0.015 * (load - 62) + _noise(rng, n, 0.3)
    rows = []
    for i in range(n):
        rows.append({"t_s": float(t[i]), "phase": "running", "quality": {},
                     "values": {"coolant_c": float(coolant[i]),
                                "oil_kgf_cm2": float(max(oil[i], 0.05)),
                                "load_pct": float(load[i]), "ambient_c": float(ambient[i]),
                                "engine_rpm": float(rpm[i]), "voltage_ll_v": float(208 - 0.015 * (load[i] - 62)),
                                "frequency_hz": 60.0, "battery_v": 13.8,
                                "fuel_pct": 70.0, "pf": 0.8}})
    return rows
