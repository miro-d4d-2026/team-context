"""Synthetic 20-minute operation profiles for a bounded hackathon demonstration.

These numbers describe a chosen scenario, not a generator manufacturer's normal
limits. Five-minute observations cannot establish second-scale fault behavior.
"""
from __future__ import annotations

import copy
import random
from datetime import datetime, timedelta, timezone

FIELDS = ("coolant_c", "oil_kgf_cm2", "vibration_rms_g")
TIMES = (0, 300, 600, 900, 1200)
ACQUISITION = {"sample_rate_hz": 833, "window_s": 2, "dc_removed": True,
               "feature_version": "rms-ac-3axis/1", "accel_range_g": 8}
CONTEXT = {"load_pct": 62, "ambient_c": 28.5, "engine_rpm": 1800}


def make_record(index, context, seed, inspected_at, *, confirmed_normal=True):
    rng = random.Random(seed)
    load, ambient, rpm = (context[k] for k in CONTEXT)
    coolant = 50 + .3 * (ambient - 28.5) + rng.uniform(-.15, .15)
    oil = 4 + .001 * (rpm - 1800) - .01 * (ambient - 28.5) + rng.uniform(-.015, .015)
    samples = []
    for i, elapsed in enumerate(TIMES):
        if i:
            coolant += (10, 8, 5, 5)[i - 1] + .12 * (load - 62) + .06 * (ambient - 28.5) + rng.uniform(-.16, .16)
            oil += (-.4, -.3, -.1, 0)[i - 1] - .001 * (load - 62) + rng.uniform(-.012, .012)
        vibration = .045 * (1 + .003 * (load - 62) + .0005 * (rpm - 1800)) + rng.uniform(-.0007, .0007)
        samples.append({"elapsed_s": elapsed, "coolant_c": round(coolant, 4),
                        "oil_kgf_cm2": round(oil, 5), "vibration_rms_g": round(vibration, 6)})
    return {"id": f"operation-{index}", "equipment_id": "GEN-10K-001", "inspected_at": inspected_at,
            "label": "정기 시험 운전", "confirmed_normal": confirmed_normal, "source": "scenario",
            "context": copy.deepcopy(context), "samples": samples, "installation_id": "demo-generator",
            "acquisition": copy.deepcopy(ACQUISITION),
            "provenance": "모의 운전 기록. 실제 발전기 계측 및 제조사 정상 범위가 아님."}


def seed_records():
    start = datetime(2026, 7, 4, 10, tzinfo=timezone(timedelta(hours=9)))
    contexts = [(58, 25, 1790), (66, 30, 1810), (60, 31, 1805), (65, 25, 1795),
                (59, 29, 1810), (64, 27, 1790), (61, 30, 1800), (62, 28.5, 1800)]
    return [make_record(f"2026-{i + 1:02d}", dict(zip(CONTEXT, values)), 6700 + i,
                        (start + timedelta(days=i * 14)).isoformat()) for i, values in enumerate(contexts)]


def seed_current():
    return {"id": "current-operation", "equipment_id": "GEN-10K-001", "inspected_at": "2026-10-11T10:00:00+09:00",
            "label": "이번 시험 운전", "confirmed_normal": False, "source": "scenario",
            "context": copy.deepcopy(CONTEXT), "installation_id": "demo-generator", "acquisition": copy.deepcopy(ACQUISITION),
            "samples": [{"elapsed_s": t, "coolant_c": coolant, "oil_kgf_cm2": oil, "vibration_rms_g": vibration}
                        for t, coolant, oil, vibration in zip(TIMES, (50, 60, 68, 73, 78), (4, 3.6, 3.3, 3.2, 3.2), (.045, .046, .045, .044, .045))],
            "provenance": "직접 수정하는 모의 운전 값. 실측 발전기 데이터가 아님."}


def scenarios():
    normal = seed_current()
    heat = copy.deepcopy(normal)
    for sample, value in zip(heat["samples"], (50, 60, 80, 85, 90)):
        sample["coolant_c"] = value
    heat["label"] = "5–10분 수온 급상승"
    oil = copy.deepcopy(normal)
    for sample, value in zip(oil["samples"], (4, 3.6, 3.3, 2.3, 2.2)):
        sample["oil_kgf_cm2"] = value
    oil["label"] = "10–15분 유압 급락"
    vibration = copy.deepcopy(normal)
    for sample, value in zip(vibration["samples"], (.045, .046, .045, .09, .09)):
        sample["vibration_rms_g"] = value
    vibration["label"] = "10–15분 진동 증가"
    combined = copy.deepcopy(heat)
    for s, o, v in zip(combined["samples"], oil["samples"], vibration["samples"]):
        s["oil_kgf_cm2"], s["vibration_rms_g"] = o["oil_kgf_cm2"], v["vibration_rms_g"]
    combined["label"] = "복합 상태 변화"
    return {"normal": normal, "coolant_rise": heat, "oil_drop": oil, "vibration_increase": vibration, "combined": combined}
