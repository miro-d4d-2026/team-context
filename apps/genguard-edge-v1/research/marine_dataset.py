"""Read the attributed Marine Engine Fault archive without inventing channels.

This is a research loader, not a generator/IMU synchronization adapter.
Only Python's standard library is required. Original CSV values are preserved;
no interpolation, resampling, filling, or unit conversion is performed.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import math
import statistics
from pathlib import Path
from zipfile import ZipFile

DATA_DIR = Path(__file__).resolve().parents[1] / "data/public/marine-engine-fault"
ARCHIVE_NAME = "Marine_Engine_Fault_Data_v1.zip"
EXPECTED_MD5 = "f4d246c1bb46e05b26e56221acc2606c"
EXPECTED_SHA256 = "3fba7aa0c288ae1bc05383fb54bf67cbde209d61b26e3e811d4bc2f22f97442b"
ROOT = "Marine_Engine_Fault_Data/"

# These are exact source names and units, not equivalences to field generator IO.
CHANNELS = {
    "engine_rpm": ("Engine Speed", "rpm"),
    "ambient_c": ("Engine room Temp.", "°C"),
    "shaft_power_kw": ("Shaft Power", "kW"),
    "coolant_in_c": ("Cooling Water Temp. Engine In", "°C"),
    "coolant_out_1_c": ("Cooling Water Temp. Engine Out I", "°C"),
    "lube_in_c": ("LO Temp. Engine In", "°C"),
    "lube_out_c": ("LO Temp. Engine Out", "°C"),
    "oil_pump_sensor_v": ("LO Circulating Pump Press.", "V"),
    "fresh_water_pressure_sensor_v": ("Fresh Cooling Water Press.", "V"),
}


def verify_archive(path: Path) -> dict:
    digest = hashlib.sha256()
    md5 = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            md5.update(block)
    actual_sha = digest.hexdigest()
    actual_md5 = md5.hexdigest()
    if actual_sha != EXPECTED_SHA256 or actual_md5 != EXPECTED_MD5:
        raise ValueError("Archive differs from the inspected Zenodo release")
    return {"sha256": actual_sha, "md5": actual_md5, "size_bytes": path.stat().st_size}


def source_files(archive: ZipFile) -> list[str]:
    return sorted(
        info.filename for info in archive.infolist()
        if info.filename.startswith(ROOT) and info.filename.endswith(".csv")
        and Path(info.filename).name not in {"dataset_index.csv", "variable_dictionary.csv"}
    )


def load_file(archive: ZipFile, name: str):
    """Yield source rows plus full-name / symbol / unit headers."""
    with archive.open(name) as binary:
        with io.TextIOWrapper(binary, encoding="utf-8") as handle:
            reader = csv.reader(handle)
            full_names, symbols, units = next(reader), next(reader), next(reader)
            if len(set(full_names)) != len(full_names):
                raise ValueError(f"Duplicate full variable names in {name}")
            for i, row in enumerate(reader, start=4):
                if len(row) != len(full_names):
                    raise ValueError(f"Malformed row {i}: {name}")
                yield full_names, symbols, units, dict(zip(full_names, row)), i


def iter_records(path: Path = DATA_DIR / ARCHIVE_NAME, file_names=None):
    """Yield engine-native records. Empty readings remain None.

    `time_s` is a source-relative test clock, not a UTC timestamp.
    `anomaly_state` is None for the separate reference file. It is only a
    retrospective label and must never be used as an inference feature.
    Records from different `source_file` values are different runs and cannot
    be joined by equal row number or equal relative time.
    """
    verify_archive(path)
    with ZipFile(path) as archive:
        for name in source_files(archive):
            relative = name[len(ROOT):]
            if file_names is not None and relative not in file_names:
                continue
            for full_names, _, units, row, source_row in load_file(archive, name):
                unit_by_name = dict(zip(full_names, units))
                readings = {}
                for target, (source, expected_unit) in CHANNELS.items():
                    if unit_by_name.get(source) != expected_unit:
                        raise ValueError(f"Unexpected unit for {source} in {relative}")
                    value = float(row[source]) if row[source] else None
                    if value is not None and not math.isfinite(value):
                        raise ValueError(f"Non-finite reading in {relative}:{source_row}")
                    readings[target] = value
                label = row.get("Anomaly State")
                if label is not None and label not in {"0", "1"}:
                    raise ValueError(f"Unexpected anomaly state: {label}")
                yield {
                    "source_kind": "public_engine_testbench",
                    "asset_id": "MATSUI-MU323DGSC-RESEARCH",
                    "source_file": relative,
                    "source_csv_row": source_row,
                    "time_s": float(row.get("Time_rel", row.get("Time"))),
                    "reference_record": relative == "Reference_Data.csv",
                    "anomaly_state": None if label is None else int(label),
                    "readings": readings,
                    "unavailable": ["imu", "output_voltage_v", "output_current_a", "oil_pressure_bar"],
                }


def inspect(path: Path) -> dict:
    archive_digest = verify_archive(path)
    summaries = []
    with ZipFile(path) as archive:
        for name in source_files(archive):
            rows = list(load_file(archive, name))
            names, symbols, units, _, _ = rows[0]
            data = [x[3] for x in rows]
            time_key = "Time_rel" if "Time_rel" in names else "Time"
            times = [float(row[time_key]) for row in data]
            labels = collections.Counter(row.get("Anomaly State", "reference") for row in data)
            normal = {}
            for channel, _ in CHANNELS.values():
                values = [float(row[channel]) for row in data
                          if row.get("Anomaly State", "0") == "0" and row[channel]]
                if values:
                    normal[channel] = {"min": min(values), "max": max(values),
                                       "mean": statistics.mean(values), "std": statistics.pstdev(values)}
            summaries.append({
                "file": name[len(ROOT):], "rows": len(data), "columns": len(names),
                "labels": dict(labels), "time_range": [min(times), max(times)],
                "time_nonincreasing": sum(b <= a for a, b in zip(times, times[1:])),
                "delta_counts": dict(collections.Counter(b-a for a, b in zip(times, times[1:])).most_common()),
                "normal": normal,
                "channels": [{"name": a, "symbol": b, "unit": c} for a, b, c in zip(names, symbols, units)],
                "sha256": hashlib.sha256(archive.read(name)).hexdigest(),
            })
    return {"archive": archive_digest, "total_rows": sum(x["rows"] for x in summaries),
            "files": summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DATA_DIR / ARCHIVE_NAME)
    parser.add_argument("--inspection", type=Path, default=DATA_DIR / "inspection.json")
    args = parser.parse_args()
    report = inspect(args.archive)
    args.inspection.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"files": len(report["files"]), "rows": report["total_rows"],
                      "report": str(args.inspection)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
