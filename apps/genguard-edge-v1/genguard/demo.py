"""Reproducible synthetic integration experiment; NOT generator accuracy."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import platform
import resource
import time

import numpy as np

from .core import EdgeRuntime, atomic_json, load_profile

ROOT = Path(__file__).resolve().parents[1]


def frames(session_id, seed, condition="normal", count=300):
    rng = np.random.default_rng(seed)
    ambient_offset = rng.normal(0, 0.5)
    temperature_offset = rng.normal(0, 0.1)
    for i in range(count):
        t = (i + 1) * 2.0
        load = 50 + 20 * np.sin(t / 70) + rng.normal(0, 0.25)
        ambient = 25 + ambient_offset + 0.5 * np.sin(t / 110)
        shared = rng.normal(0, 0.3)
        values = {
            "load_pct": load, "ambient_c": ambient,
            "engine_rpm": 1800 - 0.02 * (load - 50) + rng.normal(0, 0.8),
            "coolant_c": 68 + 0.16 * load + 0.2 * (ambient - 25) + 0.002 * t + temperature_offset + shared + rng.normal(0, 0.04),
            "oil_bar": 3.3 - 0.003 * load - 0.01 * (ambient - 25) - 0.0001 * t - shared * 0.09 + rng.normal(0, 0.006),
            "voltage_v": 241 - 0.02 * load + rng.normal(0, 0.4),
            "current_a": 0.3125 * load + rng.normal(0, 0.12),
            "vibration_rms_g": np.exp(-3.8 + 0.006 * load + rng.normal(0, 0.035)),
            "envelope_cv": 0.1 + 0.0005 * load + rng.normal(0, 0.008),
        }
        if t >= 240:
            if condition == "thermal_pressure":
                ramp = min((t - 240) / 120, 1)
                values["coolant_c"] += 10 * ramp
                values["oil_bar"] -= 0.7 * ramp
            elif condition == "vibration":
                values["vibration_rms_g"] *= 3
                values["envelope_cv"] += 0.6
            elif condition == "unseen_load":
                values["load_pct"] = 95
        units = {"load_pct": "%", "ambient_c": "degC", "engine_rpm": "rpm", "coolant_c": "degC",
                 "oil_bar": "bar", "voltage_v": "V", "current_a": "A", "vibration_rms_g": "g", "envelope_cv": "1"}
        readings = {key: {"value": float(value), "unit": units[key], "source": "scenario", "age_s": 0.0, "quality": "ok"}
                    for key, value in values.items()}
        if t >= 240 and condition == "stale_controller":
            readings["coolant_c"]["age_s"] = 60.0
        yield {"schema_version": "genguard.edge.window/1", "asset_id": "GEN-DEMO-001",
               "session_id": session_id, "installation_id": "synthetic-fixture-v1", "seq": i + 1,
               "elapsed_s": t, "duration_s": 2.0, "phase": "running", "measured_at": None,
               "readings": readings}


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "fusion.sqlite").exists() or (output / "vibration.sqlite").exists():
        raise ValueError("Use a new output directory to preserve earlier evidence")
    profile = load_profile(ROOT / "profiles/fusion-demo.json")
    vib_profile = json.loads(json.dumps(profile))
    vib_profile["profile_id"] = "vibration-feature-comparator-v1"
    vib_profile["targets"] = ["vibration_rms_g", "envelope_cv"]
    atomic_json(output / "vibration-profile.json", vib_profile)
    engines = {"fusion": EdgeRuntime(output / "fusion.sqlite", profile),
               "vibration": EdgeRuntime(output / "vibration.sqlite", vib_profile)}
    fit_ids = [f"fit-{i}" for i in range(1, 5)]
    calibration_ids = ["cal-1", "cal-2"]
    validation_ids = ["val-1", "val-2"]
    model_reports = {}
    traces, results, latencies = [], [], []
    try:
        for seed, sid in enumerate(fit_ids + calibration_ids + validation_ids, start=1):
            rows = list(frames(sid, seed))
            for engine in engines.values():
                engine.start_session(sid, "synthetic-fixture-v1")
                for frame in rows:
                    engine.ingest(frame)
                engine.close_session(sid)
                engine.review_session(sid, "normal", "synthetic-generator", "생성기가 정상으로 설정한 합성 운전. 실제 장비 점검 기록 아님.")
        for name, engine in engines.items():
            bundle = engine.train_candidate(fit_ids, calibration_ids, validation_ids)
            atomic_json(output / f"{name}-model.json", bundle)
            model_reports[name] = {"model_id": bundle["model_id"], "training_seconds": bundle["training_seconds"],
                                   "validation": bundle["validation"], "model_bytes": (output / f"{name}-model.json").stat().st_size}
            engine.activate(bundle["model_id"])
        for seed, condition in enumerate(["normal", "thermal_pressure", "vibration", "stale_controller", "unseen_load"], start=101):
            sid = f"test-{condition}"
            rows = list(frames(sid, seed, condition))
            trial = {"scenario": condition, "source_kind": "scenario", "is_example": True, "injection_start_s": 240 if condition != "normal" else None}
            per_model = {}
            for name, engine in engines.items():
                engine.start_session(sid, "synthetic-fixture-v1")
                predictions = []
                for frame in rows:
                    start = time.perf_counter()
                    prediction = engine.ingest(frame)
                    if name == "fusion":
                        latencies.append((time.perf_counter() - start) * 1000)
                    predictions.append(prediction)
                    traces.append({"scenario": condition, "model": name, "elapsed_s": frame["elapsed_s"],
                                   "state": prediction["state"], "score": prediction["current_comparison"]["score"],
                                   "threshold": prediction["current_comparison"]["threshold"],
                                   "coolant_c": frame["readings"]["coolant_c"]["value"],
                                   "oil_bar": frame["readings"]["oil_bar"]["value"],
                                   "vibration_rms_g": frame["readings"]["vibration_rms_g"]["value"]})
                engine.close_session(sid)
                alarms = [p["elapsed_s"] for p in predictions if p["persistent_change"]]
                pre = [p for p in predictions if p["elapsed_s"] < 240]
                per_model[name] = {"first_persistent_change_s": min(alarms) if alarms else None,
                                   "pre_injection_change_windows": sum(p["state"] in {"inspection_suggested", "change_observed"} for p in pre),
                                   "unknown_windows": sum(p["state"] == "unknown" for p in predictions),
                                   "total_windows": len(predictions)}
                if name == "fusion":
                    atomic_json(output / f"example-{condition}-result.json", predictions[155])
            trial["models"] = per_model
            results.append(trial)
        with (output / "traces.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(traces[0]))
            writer.writeheader(); writer.writerows(traces)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_mib = rss / (1024 ** 2 if platform.system() == "Darwin" else 1024)
        report = {"scope": "합성 운전 데이터의 통합 동작 실험. 실제 발전기 성능/보드 성능 아님.",
                  "hardware": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version(), "uno_q_measured": False},
                  "profiles": {"fusion_targets": profile["targets"], "vibration_targets": vib_profile["targets"],
                               "shared_context": profile["context"]},
                  "models": model_reports, "trials": results,
                  "ingest_including_sqlite_ms": {"median": float(np.median(latencies)), "p95": float(np.quantile(latencies, .95)), "max": max(latencies)},
                  "process_peak_rss_mib": rss_mib,
                  "notes": ["Both comparators see load/ambient context; the difference is target sensor channels.",
                            "Predeclared profiles and seeds; no tuning on these test runs.",
                            "Synthetic injected changes are test cases, not diagnosed faults."]}
        atomic_json(output / "report.json", report)
        return report
    finally:
        for engine in engines.values():
            engine.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
