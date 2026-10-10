#!/usr/bin/env python3
"""Validate the T4-6 v1 wire contract, including cross-field invariants.

Install requirements.txt, then run this file with no arguments to validate
the bundled examples, or pass --pair RAW_JSON ANALYSIS_JSON to check a pair.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from functools import lru_cache
import json
import math
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


CONTRACT_DIR = Path(__file__).resolve().parent
SCHEMA_FILES = {
    "raw_window": "raw-window.schema.json",
    "analysis_result": "analysis-result.schema.json",
}


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON number is outside the finite range: {value}")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    """Read one object, rejecting duplicate keys, NaN, Infinity and overflow."""
    with Path(path).open("r", encoding="utf-8") as stream:
        record = json.load(
            stream,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    if not isinstance(record, dict):
        raise ValueError("The JSON document must contain one object")
    return record


@lru_cache(maxsize=2)
def _validator(message_type: str) -> Draft202012Validator:
    schema = load_json(CONTRACT_DIR / SCHEMA_FILES[message_type])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _json_path(parts: Any) -> str:
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts
    )


def _finite_errors(value: Any, path: str = "$") -> list[str]:
    # bool is deliberately not treated as a number; schema types reject it
    # wherever the contract requires a numeric value.
    if isinstance(value, float) and not math.isfinite(value):
        return [f"{path}: all numeric values must be finite"]
    if isinstance(value, dict):
        return [
            error
            for key, item in value.items()
            for error in _finite_errors(item, f"{path}.{key}")
        ]
    if isinstance(value, (list, tuple)):
        return [
            error
            for index, item in enumerate(value)
            for error in _finite_errors(item, f"{path}[{index}]")
        ]
    return []


def _utc_errors(record: dict[str, Any]) -> list[str]:
    value = record["measured_at"]
    if value is None:
        return []
    if not value.endswith("Z"):
        return ["$.measured_at: UTC timestamp must end with Z"]
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return ["$.measured_at: timestamp must be an actual UTC date and time"]
    return []


def _raw_errors(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    samples = record["samples"]
    if record["sample_count"] != len(samples):
        errors.append("$.sample_count: must equal the number of samples")
    offsets = [sample[0] for sample in samples]
    if offsets[0] != 0:
        errors.append("$.samples[0][0]: first sample offset must be 0 microseconds")
    increasing = all(right > left for left, right in zip(offsets, offsets[1:]))
    if not increasing:
        errors.append("$.samples: sample offsets must be strictly increasing")
    if increasing:
        measured_rate = (len(samples) - 1) * 1_000_000 / (offsets[-1] - offsets[0])
        if not math.isclose(record["sample_rate_hz"], measured_rate, rel_tol=0.001):
            errors.append(
                "$.sample_rate_hz: must match (N-1)*1000000/(last-first) "
                f"within 0.1%; computed {measured_rate:.9g} Hz"
            )
    clipped = record["quality"]["clipped_samples"]
    if clipped is not None and clipped > record["sample_count"]:
        errors.append("$.quality.clipped_samples: cannot exceed sample_count")
    limit = record["full_scale_g"] * 1.01
    for index, sample in enumerate(samples):
        if any(abs(value) > limit for value in sample[1:]):
            errors.append(
                f"$.samples[{index}]: acceleration exceeds full_scale_g "
                "with the permitted 1% tolerance"
            )
            break
    return errors


def _analysis_errors(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    features = record["features"]
    baseline = record["baseline"]
    state = record["health_state"]
    reasons = set(record["reason_codes"])
    quality = record["quality_state"]
    flags = set(record["quality_flags"])
    window = record["input_window"]
    rul = record["rul"]

    if (baseline["id"] is None) != (baseline["rms_g"] is None):
        errors.append("$.baseline: id and rms_g must both be null or both have values")
    ratio = features["rms_ratio"]
    if ratio is not None:
        if features["rms_g"] is None or baseline["rms_g"] is None:
            errors.append("$.features.rms_ratio: requires both rms_g and baseline.rms_g")
        else:
            expected = features["rms_g"] / baseline["rms_g"]
            if not math.isclose(ratio, expected, rel_tol=0.001, abs_tol=1e-12):
                errors.append("$.features.rms_ratio: must equal rms_g/baseline.rms_g within 0.1%")

    if state == "calibrating":
        if baseline["id"] is not None or baseline["rms_g"] is not None or ratio is not None:
            errors.append("$.health_state: calibrating requires null baseline values and rms_ratio")
        if "baseline_collecting" not in reasons:
            errors.append("$.reason_codes: calibrating requires baseline_collecting")
    if state in {"normal", "watch", "warning"}:
        if quality != "ok" or window is None:
            errors.append("$.health_state: normal/watch/warning require good-quality usable input")
        if any(value is None for value in (baseline["id"], baseline["rms_g"], features["rms_g"], ratio)):
            errors.append("$.health_state: normal/watch/warning require baseline, rms_g and rms_ratio")
    required_reason = {"normal": "within_baseline", "watch": "rms_elevated", "warning": "persistent_change"}
    if state in required_reason and required_reason[state] not in reasons:
        errors.append(f"$.reason_codes: {state} requires {required_reason[state]}")
    allowed_reasons = {
        "calibrating": {"baseline_collecting"},
        "normal": {"within_baseline"},
        "watch": {"rms_elevated"},
        "warning": {"rms_elevated", "persistent_change"},
        "unknown": {"poor_signal", "baseline_unstable", "model_not_ready", "insufficient_history"},
    }
    if not reasons.issubset(allowed_reasons[state]):
        errors.append(f"$.reason_codes: contains reasons incompatible with {state}")
    if state == "unknown" and not reasons.intersection(
        {"poor_signal", "baseline_unstable", "model_not_ready", "insufficient_history"}
    ):
        errors.append("$.reason_codes: unknown requires a signal, baseline, model or history reason")

    if quality == "invalid":
        if state != "unknown":
            errors.append("$.health_state: invalid quality requires unknown")
        if not flags:
            errors.append("$.quality_flags: invalid quality requires at least one flag")
        if "poor_signal" not in reasons:
            errors.append("$.reason_codes: invalid quality requires poor_signal")
        if rul is not None:
            errors.append("$.rul: must be null when quality is invalid")
    elif flags:
        errors.append("$.quality_flags: must be empty when quality_state is ok")
    if "poor_signal" in reasons and quality != "invalid":
        errors.append("$.quality_state: poor_signal requires invalid quality")

    if state == "warning":
        if record["warning_event_id"] is None:
            errors.append("$.warning_event_id: warning requires a UUID")
    elif record["warning_event_id"] is not None:
        errors.append("$.warning_event_id: must be null outside warning state")

    if window is None:
        if state != "unknown" or quality != "invalid" or "sensor_missing" not in flags:
            errors.append("$.input_window: null requires unknown, invalid quality and sensor_missing")
        if any(features[key] is not None for key in ("rms_g", "rms_ratio", "kurtosis", "dominant_frequency_hz")):
            errors.append("$.features: missing input requires null feature values")
    elif features["dominant_frequency_hz"] is not None:
        if features["dominant_frequency_hz"] >= window["sample_rate_hz"] / 2:
            errors.append("$.features.dominant_frequency_hz: must be below sample_rate_hz/2")

    if record["source_kind"] == "xjtu_replay":
        if features["axes"] != ["x", "y"]:
            errors.append("$.features.axes: xjtu_replay requires x and y axes")
        match = re.fullmatch(r"XJTU-SY/(Bearing[123]_[1-5])/record-([1-9][0-9]*)", record["source_ref"])
        if match is None:
            errors.append("$.source_ref: xjtu_replay requires XJTU-SY/BearingX_Y/record-N")
        elif window is not None and int(match.group(2)) != window["window_seq"]:
            errors.append("$.source_ref: record-N must match input_window.window_seq")
        if rul is not None:
            if window is None or rul["as_of_minute"] != window["window_seq"] - 1:
                errors.append("$.rul.as_of_minute: must equal input_window.window_seq minus 1")
            if match is not None and rul["bearing_id"] != match.group(1):
                errors.append("$.rul.bearing_id: must match the bearing in source_ref")
    else:
        if features["axes"] != ["x", "y", "z"]:
            errors.append("$.features.axes: live_imu and scenario require x, y and z axes")
        if rul is not None:
            errors.append("$.rul: v1 permits RUL only for xjtu_replay")
    return errors


def validate_record(record: Any) -> list[str]:
    """Return readable schema and semantic errors; an empty list means valid."""
    if not isinstance(record, dict):
        return ["$: record must be an object"]
    errors = _finite_errors(record)
    message_type = record.get("message_type")
    if not isinstance(message_type, str) or message_type not in SCHEMA_FILES:
        return errors + ["$.message_type: must be raw_window or analysis_result"]
    schema_errors = sorted(
        _validator(message_type).iter_errors(record),
        key=lambda error: (_json_path(error.absolute_path), error.message),
    )
    errors.extend(f"{_json_path(error.absolute_path)}: {error.message}" for error in schema_errors)
    if errors:
        return errors
    errors.extend(_utc_errors(record))
    errors.extend(_raw_errors(record) if message_type == "raw_window" else _analysis_errors(record))
    return errors


def validate_pair(raw: Any, result: Any) -> list[str]:
    """Check both records, provenance echoes, quality propagation and RMS."""
    errors = [f"raw {error}" for error in validate_record(raw)]
    errors.extend(f"result {error}" for error in validate_record(result))
    if errors:
        return errors
    if raw["message_type"] != "raw_window" or result["message_type"] != "analysis_result":
        return ["pair: expected a raw_window followed by an analysis_result"]
    for key in (
        "node_id", "asset_id", "boot_id", "source_kind", "source_ref",
        "measured_at", "monotonic_start_us", "is_example",
    ):
        if raw[key] != result[key]:
            errors.append(f"pair $.{key}: analysis result must echo the raw value")
    window = result["input_window"]
    if window is None:
        errors.append("pair $.input_window: a paired raw window requires input_window metadata")
    else:
        for key in ("window_seq", "sample_count", "sample_rate_hz"):
            if raw[key] != window[key]:
                errors.append(f"pair $.input_window.{key}: must match the raw window")

    quality = raw["quality"]
    expected_flags: set[str] = set()
    if quality["dropped_samples"] is None or quality["clipped_samples"] is None:
        expected_flags.add("quality_unknown")
    if quality["dropped_samples"] is not None and quality["dropped_samples"] > 0:
        expected_flags.add("sample_loss")
    if quality["clipped_samples"] is not None and quality["clipped_samples"] > 0:
        expected_flags.add("clipping")
    if quality["timestamps"] == "reconstructed":
        expected_flags.add("timing_unverified")
    if expected_flags:
        if result["quality_state"] != "invalid":
            errors.append("pair $.quality_state: raw quality problems require invalid")
        for flag in sorted(expected_flags - set(result["quality_flags"])):
            errors.append(f"pair $.quality_flags: raw quality requires {flag}")

    reported_rms = result["features"]["rms_g"]
    if reported_rms is not None:
        samples = raw["samples"]
        count = len(samples)
        means = [math.fsum(row[axis] for row in samples) / count for axis in (1, 2, 3)]
        mean_axis_variance = math.fsum(
            (row[axis + 1] - means[axis]) ** 2
            for row in samples for axis in range(3)
        ) / (count * 3)
        computed_rms = math.sqrt(mean_axis_variance)
        if not math.isclose(reported_rms, computed_rms, rel_tol=1e-4, abs_tol=1e-6):
            errors.append(
                "pair $.features.rms_g: must equal sqrt(mean(axis variances)) "
                f"after removing each axis mean; computed {computed_rms:.9g} g"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="*", help="JSON records to validate (default: examples/*.json)")
    parser.add_argument("--pair", type=Path, nargs=2, metavar=("RAW", "RESULT"), help="also validate a raw/result pair")
    args = parser.parse_args(argv)
    files = args.files
    if not files and args.pair is None:
        files = sorted(path for path in (CONTRACT_DIR / "examples").glob("*.json") if "_schema" not in path.name)
        if not files:
            parser.error("No examples/*.json records found; provide files or --pair RAW RESULT")
    failed = False
    for path in files:
        try:
            errors = validate_record(load_json(path))
        except (OSError, ValueError) as exc:
            errors = [str(exc)]
        print(f"{'FAIL' if errors else 'PASS'} {path}")
        for error in errors:
            print(f"  - {error}")
        failed |= bool(errors)
    if args.pair is not None:
        raw_path, result_path = args.pair
        try:
            errors = validate_pair(load_json(raw_path), load_json(result_path))
        except (OSError, ValueError) as exc:
            errors = [str(exc)]
        print(f"{'FAIL' if errors else 'PASS'} pair {raw_path} -> {result_path}")
        for error in errors:
            print(f"  - {error}")
        failed |= bool(errors)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
