"""Generate deterministic, synthetic contract fixtures; never field measurements."""

import copy
import json
import math
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parent
BOOT_ID = "f683fa37-34ae-4d9e-a231-e66f6b486e1b"
WARNING_ID = "c1f6f120-6da7-4194-b40c-6e0e1c5d7965"


def make_raw(count=832):
    configured_hz = 416
    samples = []
    for i in range(count):
        t = i / configured_hz
        samples.append([
            round(t * 1_000_000),
            round(0.10 * math.sin(2 * math.pi * 40 * t), 9),
            round(0.04 * math.cos(2 * math.pi * 40 * t), 9),
            round(1.0 + 0.02 * math.sin(2 * math.pi * 80 * t), 9),
        ])
    observed_hz = (count - 1) * 1_000_000 / samples[-1][0]
    return {
        "schema_version": "1.0.0",
        "message_type": "raw_window",
        "is_example": True,
        "node_id": "example-node-01",
        "asset_id": "example-generator-01",
        "sensor_id": "example-imu-01",
        "boot_id": BOOT_ID,
        "window_seq": 31,
        "source_kind": "scenario",
        "source_ref": "contract-fixture/40hz-sine-not-a-generator",
        "measured_at": "2026-10-10T06:00:00.000Z",
        "monotonic_start_us": 60_000_000,
        "acceleration_unit": "g",
        "axis_order": ["x", "y", "z"],
        "gravity_removed": False,
        "full_scale_g": 4,
        "configured_sample_rate_hz": configured_hz,
        "sample_rate_hz": round(observed_hz, 6),
        "sample_count": count,
        "quality": {"dropped_samples": 0, "clipped_samples": 0, "timestamps": "measured"},
        "samples": samples,
    }


def make_result(raw):
    centered = []
    for column in range(1, 4):
        axis = [row[column] for row in raw["samples"]]
        mean = fmean(axis)
        centered.append([v - mean for v in axis])
    variances = [fmean(v * v for v in axis) for axis in centered]
    rms = math.sqrt(fmean(variances))
    kurtosis = fmean(
        fmean(v**4 for v in axis) / variance**2
        for axis, variance in zip(centered, variances)
    )
    return {
        "schema_version": "1.0.0",
        "message_type": "analysis_result",
        "is_example": True,
        "node_id": raw["node_id"],
        "asset_id": raw["asset_id"],
        "boot_id": raw["boot_id"],
        "record_seq": 1042,
        "source_kind": raw["source_kind"],
        "source_ref": raw["source_ref"],
        "measured_at": raw["measured_at"],
        "monotonic_start_us": raw["monotonic_start_us"],
        "input_window": {
            "window_seq": raw["window_seq"],
            "sample_count": raw["sample_count"],
            "sample_rate_hz": raw["sample_rate_hz"],
        },
        "features": {
            "axes": ["x", "y", "z"],
            "rms_g": round(rms, 9),
            "rms_ratio": 2.0,
            "kurtosis": round(kurtosis, 9),
            "dominant_frequency_hz": round(80 * raw["sample_rate_hz"] / raw["sample_count"], 6),
        },
        "baseline": {"id": "example-baseline-v1", "rms_g": round(rms / 2, 9)},
        "health_state": "warning",
        "reason_codes": ["rms_elevated", "persistent_change"],
        "quality_state": "ok",
        "quality_flags": [],
        "model_version": "example-rms-rule-v1",
        "warning_event_id": WARNING_ID,
        "rul": None,
        "additional_measurements": {
            "surface_temperature_c": None, "current_a": None, "oil_level_pct": None,
        },
    }


def main():
    raw = make_raw()
    warning = make_result(raw)
    normal = copy.deepcopy(warning)
    normal.update(node_id="example-node-02", asset_id="example-generator-02", record_seq=1, health_state="normal", reason_codes=["within_baseline"], warning_event_id=None)
    # Independent UI fixture: different registered baseline, not a claimed recovery.
    normal["baseline"] = {"id": "example-normal-baseline", "rms_g": normal["features"]["rms_g"]}
    normal["features"]["rms_ratio"] = 1.0

    watch = copy.deepcopy(warning)
    watch.update(node_id="example-node-03", asset_id="example-generator-03", record_seq=1, health_state="watch", reason_codes=["rms_elevated"], warning_event_id=None)

    calibrating = copy.deepcopy(warning)
    calibrating.update(node_id="example-node-04", asset_id="example-generator-04", record_seq=1, health_state="calibrating", reason_codes=["baseline_collecting"], warning_event_id=None)
    calibrating["baseline"] = {"id": None, "rms_g": None}
    calibrating["features"]["rms_ratio"] = None

    invalid = copy.deepcopy(warning)
    invalid.update(node_id="example-node-05", asset_id="example-generator-05", record_seq=1, health_state="unknown", reason_codes=["poor_signal"], quality_state="invalid", quality_flags=["sample_loss"], warning_event_id=None)
    invalid["features"] = {"axes": ["x", "y", "z"], "rms_g": None, "rms_ratio": None, "kurtosis": None, "dominant_frequency_hz": None}

    replay = copy.deepcopy(warning)
    replay.update(node_id="example-replay-01", asset_id="example-bearing-1-5", record_seq=1, source_kind="xjtu_replay", source_ref="XJTU-SY/Bearing1_5/record-31", measured_at=None, monotonic_start_us=0, health_state="unknown", reason_codes=["model_not_ready"], warning_event_id=None)
    replay["input_window"] = {"window_seq": 31, "sample_count": 32768, "sample_rate_hz": 25600}
    replay["features"] = {"axes": ["x", "y"], "rms_g": 0.08, "rms_ratio": None, "kurtosis": None, "dominant_frequency_hz": None}
    replay["baseline"] = {"id": None, "rms_g": None}
    replay["rul"] = {
        "value": 20.5, "unit": "min", "target": "bearing_test_endpoint",
        "dataset": "XJTU-SY", "bearing_id": "Bearing1_5", "as_of_minute": 30,
        "model_version": "example-only-rf-v1", "evaluation_id": "example-not-evaluated",
        "validation_scope": "exploratory",
    }
    small = make_raw(8)
    small.update(window_seq=1, monotonic_start_us=0, measured_at="2026-10-10T05:59:00.000Z", source_ref="contract-fixture/eight-samples-format-only")
    examples = {
        "raw-window-small.json": small,
        "raw-window-2s.json": raw,
        "result-warning.json": warning,
        "result-normal.json": normal,
        "result-watch.json": watch,
        "result-calibrating.json": calibrating,
        "result-invalid.json": invalid,
        "result-rul-example.json": replay,
    }
    folder = ROOT / "examples"
    folder.mkdir(exist_ok=True)
    for name, record in examples.items():
        (folder / name).write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Generated {len(examples)} synthetic contract examples. No field data or fitted-model results.")


if __name__ == "__main__":
    main()
