"""Measured phone-stimulus pattern ML, never a generator health diagnosis.

Training: repeat recording cycles 1/2; rejection calibration: repeat cycle 3.
Independent evaluation: random recording. Standard-library inference/training.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
CLASSES = ("idle", "baseline", "increased", "intermittent")
LABELS = dict(zip(CLASSES, ("무진동", "기준 연속 진동", "강한 연속 진동", "간헐 진동")))
FEATURE_DEFINITION = "axis-demean-vector-rms__100ms-bin-demean-cv-v1"


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rms(axes):
    if not axes or any(len(a) != 3 or not all(_finite(v) for v in a) for a in axes):
        raise ValueError("유한한 XYZ 가속도 값이 필요합니다")
    means = [statistics.fmean(a[k] for a in axes) for k in range(3)]
    return math.sqrt(statistics.fmean(sum((a[k] - means[k]) ** 2 for k in range(3)) for a in axes))


def extract_features(times, axes, origin=None):
    """Exact supplied definition: demean each axis; 100ms bins demean again."""
    if len(times) != len(axes) or len(times) < 2:
        raise ValueError("시간과 XYZ 배열의 길이를 확인하세요")
    if not all(_finite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("측정 시각은 유한하며 엄격히 증가해야 합니다")
    origin = times[0] if origin is None else origin
    if not _finite(origin):
        raise ValueError("특징 구간의 시작 시각이 유효하지 않습니다")
    bins = defaultdict(list)
    for t, a in zip(times, axes):
        bins[math.floor((t - origin) / 0.1)].append(a)
    envelope = [_rms(a) for _, a in sorted(bins.items()) if len(a) >= 5]
    if len(envelope) < 2:
        raise ValueError("100ms 구간에 샘플이 충분하지 않습니다")
    value = _rms(axes)
    return {"rms_vector_g": value, "rms_contract_g": value / math.sqrt(3),
            "envelope_cv": statistics.pstdev(envelope) / max(statistics.fmean(envelope), 1e-9),
            "sample_rate_hz": (len(times) - 1) / (times[-1] - times[0]),
            "sample_count": len(times), "feature_definition": FEATURE_DEFINITION,
            "max_gap_ms": max(b - a for a, b in zip(times, times[1:])) * 1000}


def _point(rms_vector_g, envelope_cv, model):
    values = {"log_rms": math.log(max(rms_vector_g, 1e-9)), "envelope_cv": envelope_cv}
    return [(values[k] - mean) / scale for k, mean, scale in zip(model["features"], model["mean"], model["std"])]


def _nearest(rms_vector_g, envelope_cv, model):
    point = _point(rms_vector_g, envelope_cv, model)
    distances = [math.dist(point, c) for c in model["centroids"]]
    index = min(range(len(distances)), key=distances.__getitem__)
    return model["classes"][index], distances[index]


def replay_windows(dataset="random"):
    """Actual recorded features. expected_pattern is evaluation-only metadata."""
    if dataset not in ("repeat", "random"):
        raise ValueError("dataset must be repeat or random")
    return json.loads((ROOT / "data" / f"vibration-{dataset}.json").read_text(encoding="utf-8"))["windows"]


def rms_trend(windows):
    """Descriptive 12-second rising rule; no growing-pattern training data exist."""
    result = {"source": "rule", "state": "insufficient_history", "label": "진동 변화 이력 수집 중",
              "trained_pattern": False, "generator_fault_diagnosis": False,
              "slope_g_per_s": None, "relative_change": None}
    if len(windows) < 7:
        return result
    points = windows[-12:]
    times = [p["timestamp_s"] for p in points]
    values = [p["rms_vector_g"] for p in points]
    if (not all(_finite(x) for x in times + values) or any(v < 0 for v in values)
            or any(b <= a or b - a > 3 for a, b in zip(times, times[1:]))
            or times[-1] - times[0] < 12):
        return result
    mt, mv = statistics.fmean(times), statistics.fmean(values)
    slope = sum((t - mt) * (v - mv) for t, v in zip(times, values)) / sum((t - mt) ** 2 for t in times)
    early, late = statistics.fmean(values[:3]), statistics.fmean(values[-3:])
    relative = (late - early) / max(early, 1e-9)
    rising = relative >= 0.25 and late - early >= 0.002 and slope > 0
    result.update(state="rising" if rising else "no_sustained_rise",
                  label="진동 세기 증가 관찰 · 규칙 기반" if rising else "지속 상승 조건 미충족",
                  slope_g_per_s=slope, relative_change=relative)
    return result


class VibrationMonitor:
    def __init__(self, model_path=None):
        self.model = json.loads(Path(model_path or ROOT / "models" / "vibration.json").read_text(encoding="utf-8"))
        if (self.model.get("schema_version") != "genguard.phone-pattern/1"
                or self.model.get("feature_definition") != FEATURE_DEFINITION):
            raise ValueError("지원하지 않는 진동 모델 계약입니다")
        self.classifier = self.model["models"]["rms_and_pattern"]
        if self.classifier.get("classes") != list(CLASSES):
            raise ValueError("진동 분류 순서가 올바르지 않습니다")
        self._history = []

    def _result(self, reason=None):
        return {"pattern_id": "unknown", "label": "판단 보류", "source": "measured_feature_input",
                "health_state": "unknown", "model_distance": None, "confidence": None,
                "unknown_reason": reason, "forced_choice": False, "generator_fault_diagnosis": False,
                "rul": None, "features": None, "quality_notes": [],
                "model_id": self.model["model_id"], "pattern_scope": "phone_stimulus_only"}

    def classify_features(self, rms_vector_g, envelope_cv, profile_confirmed=True):
        result = self._result()
        if not all(_finite(v) and v >= 0 for v in (rms_vector_g, envelope_cv)):
            result["unknown_reason"] = "invalid_features"
            return result
        result["features"] = {"rms_vector_g": rms_vector_g, "rms_contract_g": rms_vector_g / math.sqrt(3),
                              "envelope_cv": envelope_cv, "feature_definition": FEATURE_DEFINITION}
        if profile_confirmed is not True:
            result["unknown_reason"] = "installation_profile_unconfirmed"
            return result
        pattern, distance = _nearest(rms_vector_g, envelope_cv, self.classifier)
        result["model_distance"] = distance
        result["acceptance_radius"] = self.classifier["acceptance_radius"]
        if distance > self.classifier["acceptance_radius"]:
            result["unknown_reason"] = "outside_recorded_pattern_support"
            return result
        result.update(pattern_id=pattern, label=LABELS[pattern])
        return result

    def process_samples(self, samples, meta=None):
        """Inspect latest 2s of gateway buffer. Server must separately check freshness.

        meta: profile_confirmed(bool), accel_range_g(optional), fresh(optional).
        Sensor gaps >10ms, sequence gaps, invalid values or saturation reject.
        Missing full-scale is reported as unverified, never assumed non-saturated.
        """
        meta = meta if isinstance(meta, dict) else {}
        result = self._result()
        result["source"] = "live_imu"
        result["freshness_checked"] = "fresh" in meta
        def reject(reason):
            result["unknown_reason"] = reason
            self._history.clear()
            result["trend"] = rms_trend([])
            return result
        if meta.get("fresh") is False:
            return reject("stale_input")
        if not isinstance(samples, (list, tuple)) or len(samples) < 2:
            return reject("collecting_2s_window")
        try:
            if any(not isinstance(s, dict) or not _finite(s.get("elapsed_s")) for s in samples):
                return reject("invalid_timestamps")
            if any(b["elapsed_s"] <= a["elapsed_s"] for a, b in zip(samples, samples[1:])):
                return reject("nonincreasing_timestamps")
            end = samples[-1]["elapsed_s"]
            window = [s for s in samples if end - s["elapsed_s"] < 2]
            times = [s["elapsed_s"] for s in window]
            if len(window) < 2 or times[-1] - times[0] < 1.85:
                return reject("collecting_2s_window")
            axes = [[s[k] for k in ("ax_g", "ay_g", "az_g")] for s in window]
            if any(not all(_finite(v) for v in a) for a in axes):
                return reject("invalid_acceleration")
            if any(not isinstance(s.get("seq"), int) or isinstance(s["seq"], bool)
                   or not 0 <= s["seq"] < 2**32 for s in window):
                return reject("invalid_sequence")
            if any((b["seq"] - a["seq"]) % 2**32 != 1 for a, b in zip(window, window[1:])):
                return reject("sequence_gap")
            if any(not isinstance(s.get("id"), int) or isinstance(s["id"], bool) for s in window):
                return reject("invalid_sample_id")
            if any(b["id"] <= a["id"] for a, b in zip(window, window[1:])):
                return reject("nonincreasing_sample_id")
            features = extract_features(times, axes)
            result["features"] = features
            if features["max_gap_ms"] > 10:
                return reject("sample_gap_over_10ms")
            nominal_hz = self.model["signal_quality_policy"]["sample_rate_hz"]
            if not nominal_hz[0] <= features["sample_rate_hz"] <= nominal_hz[1]:
                return reject("sampling_profile_mismatch")
            fullscale = meta.get("accel_range_g")
            notes = []
            if _finite(fullscale) and fullscale > 0:
                if any(abs(v) >= fullscale * 0.999 for a in axes for v in a):
                    return reject("sensor_saturation_or_range_proximity")
            else:
                notes.append("풀스케일 정보 없음 · 포화 여부 미검증")
            result = self.classify_features(features["rms_vector_g"], features["envelope_cv"],
                                            meta.get("profile_confirmed") is True)
            result.update(source="live_imu", features=features, quality_notes=notes,
                          freshness_checked="fresh" in meta, timestamp_s=end)
            if self._history and end < self._history[-1]["timestamp_s"]:
                self._history.clear()
            if result["unknown_reason"]:
                self._history.clear()
            elif not self._history or end - self._history[-1]["timestamp_s"] >= 1.85:
                if self._history and (end <= self._history[-1]["timestamp_s"] or end - self._history[-1]["timestamp_s"] > 3):
                    self._history.clear()
                self._history.append({"timestamp_s": end, "rms_vector_g": features["rms_vector_g"]})
                self._history = self._history[-12:]
            result["trend"] = rms_trend(self._history)
            return result
        except (KeyError, TypeError, ValueError, OverflowError):
            return reject("invalid_samples")


def _fit(rows, calibration, keys):
    def raw(row, key):
        return math.log(max(row["rms_vector_g"], 1e-9)) if key == "log_rms" else row[key]
    mean = [statistics.fmean(raw(r, k) for r in rows) for k in keys]
    std = [max(statistics.pstdev(raw(r, k) for r in rows), 1e-9) for k in keys]
    model = {"features": keys, "mean": mean, "std": std, "classes": list(CLASSES)}
    model["centroids"] = [[statistics.fmean(_point(r["rms_vector_g"], r["envelope_cv"], model)[i]
                             for r in rows if r["expected_pattern"] == c) for i in range(len(keys))] for c in CLASSES]
    def true_distances(data):
        return [math.dist(_point(r["rms_vector_g"], r["envelope_cv"], model),
                          model["centroids"][CLASSES.index(r["expected_pattern"])]) for r in data]
    fit_dist, cal_dist = true_distances(rows), true_distances(calibration)
    # Frozen policy before random evaluation; small-sample guard, not a coverage guarantee.
    model["acceptance_radius"] = 1.25 * max(fit_dist + cal_dist)
    model["rejection_calibration"] = {"method": "1.25 * max(true_class_distance in fit + repeat calibration)",
                                      "fit_max_distance": max(fit_dist), "calibration_max_distance": max(cal_dist),
                                      "engineering_margin": 1.25, "calibration_windows": len(calibration),
                                      "probability_calibrated": False, "random_test_used": False}
    return model


def _evaluate(rows, model, reject):
    columns = list(CLASSES) + ["unknown"]
    confusion = [[0] * len(columns) for _ in CLASSES]
    for r in rows:
        prediction, distance = _nearest(r["rms_vector_g"], r["envelope_cv"], model)
        if reject and distance > model["acceptance_radius"]:
            prediction = "unknown"
        confusion[CLASSES.index(r["expected_pattern"])][columns.index(prediction)] += 1
    correct = sum(confusion[i][i] for i in range(len(CLASSES)))
    unknown = sum(row[-1] for row in confusion)
    recalls = [confusion[i][i] / sum(row) for i, row in enumerate(confusion)]
    return {"count": len(rows), "correct": correct, "fraction_correct_all_windows": correct / len(rows),
            "macro_recall_all_windows": statistics.fmean(recalls), "unknown": unknown,
            "accepted_count": len(rows) - unknown,
            "fraction_correct_accepted": correct / (len(rows) - unknown) if len(rows) > unknown else None,
            "confusion_rows": list(CLASSES), "confusion_columns": columns, "confusion": confusion,
            "unknown_counts_as_incorrect_in_overall_metric": True, "rejection_enabled": reject}


def train_and_report():
    """Reproducibly train only repeat; freeze/save before reading random labels."""
    repeat = replay_windows("repeat")
    fit = [r for r in repeat if r["cycle"] in (1, 2)]
    calibration = [r for r in repeat if r["cycle"] == 3]
    model = {"schema_version": "genguard.phone-pattern/1", "model_id": "phone-pattern-repeat12-cal3-v1",
             "algorithm": "standardized-nearest-class-centroid", "feature_definition": FEATURE_DEFINITION,
             "scope": "Commanded phone vibration stimulus on recorded fixture; never generator faults",
             "training": {"dataset": "repeat", "cycles": [1, 2], "windows": len(fit),
                          "calibration_cycles": [3], "calibration_windows": len(calibration),
                          "fit_and_calibration_recordings": 1, "growing_pattern_examples": 0},
             "signal_quality_policy": {"window_s": 2, "minimum_window_span_s": 1.85,
                                       "max_gap_ms": 10, "sample_rate_hz": [180, 240],
                                       "rate_bounds_source": "engineering guard around recorded ~209Hz; not learned safety limits"},
             "models": {name: _fit(fit, calibration, keys) for name, keys in
                        (("rms_only", ["log_rms"]), ("rms_and_pattern", ["log_rms", "envelope_cv"]))},
             "provenance": {"repeat_features_sha256": hashlib.sha256((ROOT / "data/vibration-repeat.json").read_bytes()).hexdigest()},
             "limitations": ["Command mode is label, not independently measured actuator state.",
                             "One recording split by cycles is not independent-installation calibration.",
                             "Unknown rejection is a small-sample distance guard, not reliable novelty coverage.",
                             "RMS is axis-demeaned vector RMS in g; contract RMS equals vector RMS / sqrt(3).",
                             "No rising-pattern training, generator diagnosis, failure probability or RUL."]}
    (ROOT / "models/vibration.json").write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    random = replay_windows("random")
    report = {"model_id": model["model_id"], "data_source": "actual_phone_and_imu_recordings",
              "fit_windows": len(fit), "calibration_windows": len(calibration), "test_windows": len(random),
              "fit_cycles": [1, 2], "calibration_cycles": [3], "test_recordings": 1,
              "test_used_for_training_or_radius": False, "generator_health_evaluated": False,
              "growing_pattern_training_examples": 0, "uno_q_model_execution_measured": False,
              "evaluation_scope": "48 correlated 2s windows in one separate random-order recording; not 48 independent trials",
              "models": {name: {"repeat_calibration": _evaluate(calibration, m, True),
                                 "random_with_unknown_rejection": _evaluate(random, m, True),
                                 "random_forced_choice_comparator": _evaluate(random, m, False)} for name, m in model["models"].items()},
              "provenance": {dataset: json.loads((ROOT / f"data/vibration-{dataset}.json").read_text())["provenance"]
                             for dataset in ("repeat", "random")},
              "limitations": model["limitations"]}
    (ROOT / "reports/vibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", action="store_true", help="Fit supplied repeat cycles and evaluate held-out random recording")
    args = parser.parse_args()
    if args.train:
        report = train_and_report()
        print(json.dumps({"fit": report["fit_windows"], "calibration": report["calibration_windows"],
                          "random": report["models"]["rms_and_pattern"]["random_with_unknown_rejection"]}, ensure_ascii=False, indent=2))
    else:
        parser.print_help()
