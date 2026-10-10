"""Explicitly synthetic repeated inspections; no measured generator history."""
from __future__ import annotations

import copy
import math
import random
from datetime import datetime, timedelta, timezone

FIELDS = ("coolant_c", "oil_kgf_cm2", "vibration_rms_g", "load_pct", "ambient_c", "engine_rpm", "elapsed_s")
ACQUISITION = {"sample_rate_hz": 833, "window_s": 2, "dc_removed": True, "feature_version": "rms-ac-3axis/1", "accel_range_g": 8}


def normal_values(context, rng=None, run_offset=None):
    """Generator for development evidence, NOT an OEM normal range."""
    rng = rng or random.Random(0)
    offset = run_offset or (0, 0, 0)
    load, ambient, rpm, elapsed = (context[k] for k in ("load_pct", "ambient_c", "engine_rpm", "elapsed_s"))
    return {**context,
            "coolant_c": 80 + .12 * (load - 62) + .3 * (ambient - 28.5) + .003 * (elapsed - 600) + offset[0] + rng.gauss(0, .18),
            "oil_kgf_cm2": 3.2 - .005 * (load - 62) - .008 * (ambient - 28.5) + .00003 * (rpm - 1800) + offset[1] + rng.gauss(0, .018),
            "vibration_rms_g": .045 * math.exp(.005 * (load - 62) + .001 * (rpm - 1800) + offset[2] + rng.gauss(0, .025))}


def make_record(index, context, seed, inspected_at, *, confirmed_normal=True):
    rng = random.Random(seed)
    offset = (rng.gauss(0, .16), rng.gauss(0, .016), rng.gauss(0, .02))
    samples = []
    # Non-overlapping feature windows. Different runs, not shuffled rows, are
    # the independent units for evaluation and minimum-history checks.
    for i in range(30):
        c = {**context, "load_pct": context["load_pct"] + rng.uniform(-.5, .5),
             "ambient_c": context["ambient_c"] + rng.uniform(-.2, .2),
             "engine_rpm": context["engine_rpm"] + rng.uniform(-3, 3),
             "elapsed_s": context["elapsed_s"] - 30 + i * 2}
        samples.append(normal_values(c, rng, offset))
    values = {field: sum(s[field] for s in samples) / len(samples) for field in FIELDS}
    return {"id": f"inspection-{index}", "equipment_id": "GEN-10K-001", "inspected_at": inspected_at,
            "label": "정기 시험 운전", "source": "scenario", "confirmed_normal": confirmed_normal,
            "installation_id": "demo-generator-mount-1", "phase": "running", "values": values,
            "sources": {f: "scenario" for f in FIELDS}, "quality": {}, "samples": samples,
            "sample_duration_s": 2, "acquisition": copy.deepcopy(ACQUISITION),
            "provenance": "합성 점검 기록. 실제 발전기 측정값 및 제조사 기준이 아님."}


def seed_records():
    start = datetime(2026, 7, 4, 10, tzinfo=timezone(timedelta(hours=9)))
    contexts = [(58, 25, 1790, 580), (66, 30, 1810, 620), (60, 31, 1805, 550),
                (65, 25, 1795, 650), (59, 29, 1810, 610), (64, 27, 1790, 580),
                (61, 30, 1800, 620), (62, 28.5, 1800, 600)]
    return [make_record(f"2026-{i + 1:02d}", dict(zip(("load_pct", "ambient_c", "engine_rpm", "elapsed_s"), c)),
                        1100 + i, (start + timedelta(days=i * 14)).isoformat())
            for i, c in enumerate(contexts)]


def seed_current():
    record = copy.deepcopy(seed_records()[-1])
    record.update(id="current-inspection", inspected_at="2026-10-11T10:00:00+09:00", label="이번 점검", confirmed_normal=False)
    record.pop("samples", None)
    record["values"] = {"coolant_c": 80.2, "oil_kgf_cm2": 3.19, "vibration_rms_g": .046,
                        "load_pct": 62, "ambient_c": 28.5, "engine_rpm": 1800, "elapsed_s": 600}
    return record
