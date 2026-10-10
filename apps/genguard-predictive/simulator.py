"""Synthetic 10 kW / 208 V / 60 Hz fixture, not a validated engine simulator.

All coefficients, deterioration trajectories and sensor noise are invented for
software experiments. No real generator or manufacturer safety data is present.
"""
from __future__ import annotations

import math
import numpy as np

SCENARIOS = {
    "normal": "정상 안정 운전",
    "load_transient": "정상 부하 변화",
    "cooling_decline": "냉각 성능 저하",
    "oil_decline": "윤활유 압력 저하",
    "voltage_drift": "출력 전압 하락",
    "imbalance": "상별 전류 불균형",
    "charging_decline": "배터리 충전 전압 저하",
    "fuel_decline": "연료 공급 압력 저하",
    "recovery": "점검 후 냉각 회복",
}


def _smooth(t: float, onset: float, duration: float) -> float:
    x = max(0.0, min(1.0, (t - onset) / duration))
    return x * x * (3.0 - 2.0 * x)


def generate_run(scenario: str = "normal", seed: int = 1, duration_s: int = 1200) -> list[dict]:
    """Return flat 2-second telemetry. Future interventions are not input features."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    rng = np.random.default_rng(seed)
    base_load = rng.uniform(38.0, 76.0)
    ambient = rng.uniform(12.0, 36.0)
    load_amp = rng.uniform(1.5, 5.5)
    load_period = rng.uniform(110.0, 270.0)
    phase = rng.uniform(0, math.tau)
    onset = float(rng.uniform(300.0, 420.0))
    duration = float(rng.uniform(250.0, 560.0))
    fault_size = float(rng.uniform(0.75, 1.30))
    intervention_s = onset + rng.uniform(250.0, 420.0)
    load_onset = rng.uniform(270.0, 420.0)
    load_return = load_onset + rng.uniform(290.0, 470.0)
    load_step = rng.uniform(9.0, 20.0) * rng.choice([-1, 1])
    sensor_temp_offset = rng.normal(0, 0.18)
    sensor_oil_offset = rng.normal(0, 0.014)
    sensor_voltage_offset = rng.normal(0, 0.09)
    temp = 68.0 + 0.14 * base_load + 0.18 * (ambient - 24.0)
    lag_load = base_load
    initial_fuel = rng.uniform(65.0, 95.0)
    run_id = f"{scenario}-{seed}"
    frames = []
    for t in range(0, duration_s + 1, 2):
        load = base_load + load_amp * math.sin(t / load_period + phase)
        if scenario == "load_transient":
            load += load_step * (_smooth(t, load_onset, 45) - _smooth(t, load_return, 60))
        load = max(20.0, min(92.0, load))
        air = ambient + 0.45 * math.sin(t / 380.0 + phase)
        lag_load += (load - lag_load) * (1 - math.exp(-2.0 / 8.0))
        degrade = fault_size * _smooth(t, onset, duration)
        recovery = 1.0 - _smooth(t, intervention_s, 150.0) if scenario == "recovery" else 1.0
        cooling_extra = 22.0 * degrade * recovery if scenario in ("cooling_decline", "recovery") else 0.0
        target_temp = 68.0 + 0.14 * load + 0.18 * (air - 24.0) + cooling_extra
        temp += (target_temp - temp) * (1 - math.exp(-2.0 / 90.0))
        oil = 3.25 - 0.004 * (load - 60.0) - 0.023 * (temp - 77.0) + sensor_oil_offset
        if scenario == "oil_decline":
            oil -= 1.65 * degrade
        voltage = 208.0 - 0.025 * (load - 60.0) - 0.025 * (load - lag_load) + sensor_voltage_offset
        if scenario == "voltage_drift":
            voltage -= 18.0 * degrade
        frequency = 60.0 - 0.0006 * (load - 60.0) - 0.008 * (load - lag_load)
        fuel_pressure = 2.8 - 0.003 * (load - 60.0)
        fuel = initial_fuel - 0.0016 * t * (0.5 + load / 100.0)
        if scenario == "fuel_decline":
            fuel_pressure -= 1.6 * degrade
            fuel -= 11.0 * degrade
            frequency -= 0.22 * max(degrade - 0.7, 0.0)
        battery = 13.85 + 0.012 * math.sin(t / 180.0 + phase)
        if scenario == "charging_decline":
            battery -= 2.1 * degrade
        frequency += rng.normal(0, 0.006)
        current = (10000.0 * load / 100.0) / (math.sqrt(3.0) * voltage * 0.8)
        unbalance = 0.24 * degrade if scenario == "imbalance" else 0.0
        frames.append({
            "timestamp_s": float(t), "phase": "running", "data_source": "synthetic", "run_id": run_id,
            "scenario": scenario, "injection_onset_s": onset if scenario not in ("normal", "load_transient") else None,
            "intervention_s": float(intervention_s) if scenario == "recovery" else None,
            "load_pct": float(load), "ambient_c": float(air),
            "coolant_c": float(temp + sensor_temp_offset + rng.normal(0, 0.035)),
            "oil_pressure_kgf_cm2": float(max(0.2, oil + rng.normal(0, 0.004))),
            "voltage_v": float(voltage + rng.normal(0, 0.07)),
            "frequency_hz": float(frequency), "engine_rpm": float(frequency * 30.0),
            "current_a": float(current * (1 + unbalance) + rng.normal(0, 0.035)),
            "current_b": float(current * (1 - unbalance * 0.6) + rng.normal(0, 0.035)),
            "current_c": float(current * (1 - unbalance * 0.4) + rng.normal(0, 0.035)),
            "battery_v": float(battery + rng.normal(0, 0.007)), "fuel_pct": float(max(0, fuel + rng.normal(0, 0.018))),
            "fuel_pressure_bar": float(max(0.2, fuel_pressure + rng.normal(0, 0.008))),
        })
    return frames
