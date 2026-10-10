"""Convert the attributed public engine archive to GenGuard replay frames.

Never joins this engine dataset to the team's unrelated iPhone IMU recording.
Ground truth stays in a separate labels file, never an inference feature.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
from marine_dataset import DATA_DIR, ARCHIVE_NAME, iter_records

FIELD_MAP = {
    "engine_rpm": ("engine_rpm", "rpm"),
    "ambient_c": ("ambient_c", "degC"),
    "shaft_power_kw": ("shaft_power_kw", "kW"),
    "coolant_c": ("coolant_out_1_c", "degC"),
    "coolant_in_c": ("coolant_in_c", "degC"),
    "oil_temp_c": ("lube_in_c", "degC"),
    "oil_out_temp_c": ("lube_out_c", "degC"),
    "oil_pressure_signal_v": ("oil_pump_sensor_v", "V"),
    "coolant_pressure_signal_v": ("fresh_water_pressure_sensor_v", "V"),
}

# Fixed before running a model. No source file occurs in multiple roles.
SPLITS = {
    "fit": [
        "AF_Clogging/AF_Clogging_40_Load.csv", "AF_Clogging/AF_Clogging_60_Load.csv",
        "AF_Clogging/AF_Clogging_75_Load.csv", "AF_Clogging/AF_Clogging_85_Load.csv",
        "AC_Fouling/AC_Fouling_75_Load.csv", "AC_Fouling/AC_Fouling_85_Load.csv",
    ],
    "calibration": ["AC_Fouling/AC_Fouling_40_Load.csv", "AC_Fouling/AC_Fouling_60_Load.csv"],
    "validation": ["Turbine_Degradation/Turbine_Degradation_40_Load.csv",
                   "Turbine_Degradation/Turbine_Degradation_60_Load.csv"],
    "test": ["Turbine_Degradation/Turbine_Degradation_85_Load.csv",
             "Pump_Cavitation/CW_Pump_Cavitation_60_Load.csv",
             "Pump_Cavitation/CW_Pump_Cavitation_85_Load.csv"],
    "excluded": ["Reference_Data.csv", "Injector_Nozzle/Clogged_Injector_Nozzle1_40_60_85_Load.csv",
                 "Injector_Nozzle/Clogged_Injector_Nozzle2_LoadProgram.csv"],
}


def nominal_load(filename):
    """Nominal experiment condition, not measured electrical load."""
    if filename == "Reference_Data.csv" or filename.startswith("Injector_Nozzle/"):
        return None
    match = re.search(r"_(40|60|75|85)_Load\.csv$", filename)
    return float(match.group(1)) if match else None


def split_for_file(filename):
    for role, files in SPLITS.items():
        if filename in files:
            return role
    raise ValueError(f"Unknown source file: {filename}")


def reading(value, unit):
    return {"value": value, "unit": unit, "source": "public_replay", "age_s": 0.0,
            "quality": "missing" if value is None else "ok"}


def labeled_frame_iter(zip_path, filename):
    """Yield (frame, audit label) pairs; each label segment gets a session ID.

    Source clock is retained for scenario files; switching to the anomaly
    segment does not reset elapsed_s. Each source row is an instantaneous
    telemetry observation. duration_s is the preceding sampling interval,
    not a claim that readings are interval means. First source row is omitted
    because it has no preceding interval. Reference is supplementary only;
    its first timestamp is rebased to zero for the transport contract.
    """
    previous_time = None
    first_time = None
    counters = {}
    run = Path(filename).stem.lower().replace("_", "-")
    role = split_for_file(filename)
    for record in iter_records(Path(zip_path), [filename]):
        time_s = record["time_s"]
        if first_time is None:
            first_time = time_s
        if previous_time is None:
            previous_time = time_s
            continue
        duration = time_s - previous_time
        previous_time = time_s
        if duration <= 0:
            raise ValueError(f"Non-increasing source time in {filename}")
        truth = record["anomaly_state"]
        segment = "reference" if truth is None else ("normal" if truth == 0 else "anomaly")
        session_id = f"public-{run}-{segment}"
        counters[session_id] = counters.get(session_id, 0) + 1
        readings = {target: reading(record["readings"][native], unit)
                    for target, (native, unit) in FIELD_MAP.items()}
        # The original archive repeatedly contains 999 degC for lube inlet.
        # Preserve the raw number but exclude it as invalid measurement;
        # its exact sentinel meaning is not documented by the data authors.
        if readings["oil_temp_c"]["value"] == 999.0:
            readings["oil_temp_c"]["quality"] = "invalid"
        readings["load_pct"] = reading(nominal_load(filename), "%")
        # Missing hardware channels are explicit; never imputed with zero.
        for key, unit in [("oil_bar", "bar"), ("voltage_v", "V"), ("current_a", "A"),
                          ("vibration_rms_g", "g"), ("envelope_cv", "1")]:
            readings[key] = reading(None, unit)
        frame = {
            "schema_version": "genguard.edge.window/1", "asset_id": "MATSUI-MU323DGSC-RESEARCH",
            "session_id": session_id, "installation_id": "marine-engine-fault-v1",
            "seq": counters[session_id],
            "elapsed_s": time_s - first_time if record["reference_record"] else time_s,
            "duration_s": duration, "phase": "running", "measured_at": None,
            "readings": readings,
        }
        label = {
            "session_id": session_id, "seq": counters[session_id], "source_file": filename,
            "source_csv_row": record["source_csv_row"], "source_time_s": time_s,
            "anomaly_state": truth, "split": role, "segment": segment,
            "included_in_primary_benchmark": role != "excluded" and (role == "test" or truth == 0),
        }
        yield frame, label


def frame_iter(zip_path, filename, segment=None):
    """Inference frames only; optional segment: normal/anomaly/reference."""
    for frame, label in labeled_frame_iter(zip_path, filename):
        if segment is None or label["segment"] == segment:
            yield frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DATA_DIR / ARCHIVE_NAME)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "replay")
    parser.add_argument("--include-excluded", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    files = [name for role, names in SPLITS.items() for name in names
             if args.include_excluded or role != "excluded"]
    counts = {}
    for name in files:
        run = Path(name).stem
        with gzip.open(args.output / f"{run}.frames.jsonl.gz", "wt", encoding="utf-8") as f, \
             gzip.open(args.output / f"{run}.labels.jsonl.gz", "wt", encoding="utf-8") as l:
            count = 0
            for frame, label in labeled_frame_iter(args.archive, name):
                f.write(json.dumps(frame, ensure_ascii=False, allow_nan=False) + "\n")
                l.write(json.dumps(label, ensure_ascii=False, allow_nan=False) + "\n")
                count += 1
        counts[name] = count
    (args.output / "manifest.json").write_text(json.dumps({"splits": SPLITS, "counts": counts,
        "scope": "Real marine-engine telemetry replay, without IMU or electrical output channels",
        "labels_are_not_features": True, "first_row_omitted": True}, indent=2) + "\n")
    print(json.dumps({"files": len(files), "frames": sum(counts.values()), "output": str(args.output)}))


if __name__ == "__main__":
    main()
