"""Separate-domain learning for the hybrid demonstration.

Generator channels reuse a marginal of the frozen scenario model. IMU channels
learn a new normal baseline from the actual fixture. Their decisions can be
combined, but no physical generator-to-IMU correlation is claimed or learned.
No state, persistence counter, or automatic retraining is hidden in this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

import numpy as np

GENERATOR_TARGETS = ("engine_rpm", "coolant_c", "oil_bar", "voltage_v")
UI_KEYS = {"oil_bar": "oil_kgf_cm2", "voltage_v": "voltage_ll_v"}
OIL_KGF_TO_BAR = 0.980665
VIBRATION_FIELDS = ("vibration_rms_g", "envelope_cv")
VIBRATION_THRESHOLD = 3.0
VIBRATION_MIN_RMS_G = 0.0005
VIBRATION_LOG_STD_FLOOR = np.array([0.10, 0.15])


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unknown(reason: str, fields=None, **extra) -> dict:
    return {"state": "unknown", "score": None, "threshold": None,
            "evidence": [], "checks": [], "reason": reason,
            "invalid_fields": list(fields or []), **extra}


def _quality_ok(entry: Any, max_age: float = 5.0) -> bool:
    if entry is None or entry == "ok":
        return True
    if isinstance(entry, str):
        return False
    if not isinstance(entry, dict) or entry.get("quality", "ok") != "ok":
        return False
    if "valid" in entry and entry["valid"] is not True:
        return False
    age = entry.get("age_s", 0)
    return _number(age) and 0 <= age <= max_age


class HybridAnalyzer:
    """Read-only adapter; caller owns data provenance, timing and persistence."""

    def __init__(self, profile: dict, bundle: dict):
        self.profile = copy.deepcopy(profile)
        self.bundle_id = bundle["model_id"]
        self.phase = copy.deepcopy(bundle["phases"]["running"])
        self.indices = [profile["targets"].index(k) for k in GENERATOR_TARGETS]
        # Selecting precision directly would CONDITION on synthetic vibration.
        # Invert, select marginal covariance, then invert to exclude it entirely.
        full_covariance = np.linalg.inv(np.asarray(self.phase["precision"], dtype=float))
        covariance = full_covariance[np.ix_(self.indices, self.indices)]
        self.precision = np.linalg.inv(covariance)
        self.weights = np.asarray(self.phase["weights"], dtype=float)[:, self.indices]
        self.residual_mean = np.asarray(self.phase["residual_mean"], dtype=float)[self.indices]
        self.residual_scale = np.asarray(self.phase["residual_scale"], dtype=float)[self.indices]

    def score_generator(self, values: dict, quality: dict | None = None) -> dict:
        quality = quality or {}
        required = list(self.profile["context"]) + list(GENERATOR_TARGETS)
        readings = {}
        invalid = []
        for key in required:
            ui_key = UI_KEYS.get(key, key)
            value = values.get(ui_key)
            if not _number(value):
                invalid.append(ui_key)
                continue
            if key == "oil_bar":
                value *= OIL_KGF_TO_BAR
            spec = self.profile["fields"][key]
            if not spec["valid_min"] <= value <= spec["valid_max"] or not _quality_ok(
                quality.get(ui_key), spec.get("max_age_s", 5)
            ):
                invalid.append(ui_key)
                continue
            readings[key] = value
        if invalid:
            return _unknown("invalid_or_stale_generator_input", invalid, threshold=2.0,
                            source="scenario", model_id=self.bundle_id)
        x = np.array([readings[k] for k in self.profile["context"]])
        outside = [k for i, k in enumerate(self.profile["context"])
                   if not self.phase["context_low"][i] <= x[i] <= self.phase["context_high"][i]]
        if outside:
            return _unknown("unseen_operating_context", outside, threshold=2.0,
                            source="scenario", model_id=self.bundle_id, outside_context=outside)
        design = np.r_[1.0, (x - np.asarray(self.phase["context_mean"])) /
                        np.asarray(self.phase["context_scale"])]
        expected = design @ self.weights + self.residual_mean
        y = np.array([readings[k] for k in GENERATOR_TARGETS])
        z = (y - expected) / self.residual_scale
        score = float(np.sqrt(max(float(z @ self.precision @ z), 0) / len(y)))
        evidence = []
        checks = []
        for i in np.argsort(-np.abs(z)):
            key = GENERATOR_TARGETS[int(i)]
            factor = OIL_KGF_TO_BAR if key == "oil_bar" else 1.0
            evidence.append({"field": UI_KEYS.get(key, key), "value": float(y[i] / factor),
                             "expected": float(expected[i] / factor),
                             "unit": "kgf/cm²" if key == "oil_bar" else self.profile["fields"][key]["unit"],
                             "standardized_residual": float(z[i]), "source": "scenario",
                             "meaning": "조건 대비 편차이며 고장 원인 기여도나 확률이 아님"})
            if score > 2.0 and abs(z[i]) >= 1.0:
                checks.extend(self.profile["fields"][key].get("checks", []))
        return {"state": "change" if score > 2.0 else "within_baseline", "score": score,
                "threshold": 2.0, "evidence": evidence, "checks": list(dict.fromkeys(checks)),
                "reason": "multivariate_change" if score > 2.0 else "within_baseline",
                "source": "scenario", "model_id": self.bundle_id,
                "algorithm": "context-ridge-residual-covariance-marginal-4/1",
                "threshold_provenance": "4채널 시연 설정. 원래 6채널 검증 결과를 승계하지 않음.",
                "scope": "발전기 모의 4채널 조건부 변화 분석. 실측 진동과의 물리적 상관관계를 학습하지 않음."}

    @staticmethod
    def fit_vibration(windows: list[dict]) -> dict:
        return fit_vibration(windows)

    @staticmethod
    def score_vibration(features: dict, baseline: dict | None) -> dict:
        return score_vibration(features, baseline)


def _vibration_vector(features: dict) -> np.ndarray:
    if not isinstance(features, dict):
        raise ValueError("IMU features must be an object")
    rms, cv = features.get("vibration_rms_g"), features.get("envelope_cv")
    if not _number(rms) or not _number(cv):
        raise ValueError("IMU RMS and envelope CV must be finite numbers")
    if not 0 <= rms <= 30 or not 0 <= cv <= 100:
        raise ValueError("IMU feature outside supported input range")
    # A stopped source is scoreable after learning, but cannot establish normal.
    return np.array([math.log(max(rms, 1e-9)), math.log(cv + 0.01)])


def _distance(vector: np.ndarray, mean: np.ndarray, precision: np.ndarray) -> float:
    delta = vector - mean
    return float(np.sqrt(max(float(delta @ precision @ delta), 0) / len(delta)))


def fit_vibration(windows: list[dict]) -> dict:
    """Fit first 10 unique normal windows; validate against next 5.

    This small demonstration baseline is a learned mean/covariance model, not a
    fault classifier. User selects a normal, unchanged mounting/vibration state.
    Floor log standard deviations avoid amplifying tiny sensor noise into alarms.
    """
    if not isinstance(windows, list) or len(windows) != 15:
        raise ValueError("Exactly 15 unique normal windows are required (10 train + 5 validation)")
    ids, vectors, rms_values = [], [], []
    sources = {packet.get("source", "live_imu") for packet in windows}
    if len(sources) != 1 or not sources <= {"live_imu", "recorded_imu"}:
        raise ValueError("Calibration must use one explicit IMU source")
    source = sources.pop()
    for packet in windows:
        identifier = packet.get("window_id", packet.get("packet_id"))
        if not isinstance(identifier, (str, int)) or isinstance(identifier, bool) or not str(identifier):
            raise ValueError("Every calibration window requires a unique window_id")
        if not _quality_ok(packet.get("quality")):
            raise ValueError("Calibration window quality must be ok and fresh")
        features = packet.get("features")
        vector = _vibration_vector(features)
        rms = features["vibration_rms_g"]
        if rms <= VIBRATION_MIN_RMS_G:
            raise ValueError("Calibration vibration is too low; run a stable baseline vibration")
        ids.append(str(identifier))
        vectors.append(vector)
        rms_values.append(rms)
    if len(set(ids)) != 15:
        raise ValueError("Calibration windows must be unique; repeated sensor packets cannot train")
    rms_array = np.asarray(rms_values)
    variation = float(rms_array[:10].std() / rms_array[:10].mean())
    if variation > 0.25:
        raise ValueError("Calibration RMS is unstable (>25% training variation); keep mounting and vibration unchanged")
    train = np.asarray(vectors[:10])
    mean = train.mean(axis=0)
    centered = train - mean
    empirical = centered.T @ centered / len(train)
    shrinkage = 0.25
    covariance = ((1 - shrinkage) * empirical + shrinkage * np.diag(np.diag(empirical))
                  + np.diag(VIBRATION_LOG_STD_FLOOR ** 2))
    precision = np.linalg.inv(covariance)
    train_scores = [_distance(v, mean, precision) for v in train]
    validation_scores = [_distance(v, mean, precision) for v in vectors[10:]]
    if any(s > VIBRATION_THRESHOLD for s in validation_scores):
        raise ValueError("Calibration validation changed from training; restart with an unchanged normal vibration")
    result = {"schema_version": "genguard.imu.baseline/1", "algorithm": "log-feature-gaussian/1",
              "source": source, "features": list(VIBRATION_FIELDS),
              "transform": ["log(max(rms_g,1e-9))", "log(envelope_cv+0.01)"],
              "mean": mean.tolist(), "covariance": covariance.tolist(),
              "precision": precision.tolist(), "threshold": VIBRATION_THRESHOLD,
              "normal_values": {"vibration_rms_g": float(np.exp(mean[0])),
                                "envelope_cv": float(np.exp(mean[1]) - 0.01)},
              "sample_count": 15, "training_count": 10, "validation_count": 5,
              "training_window_ids": ids[:10], "validation_window_ids": ids[10:],
              "training_rms_variation": variation,
              "training_scores": train_scores,
              "validation": {"passed": True, "scores": validation_scores, "max_score": max(validation_scores),
                             "meaning": "보류한 정상 5창의 일관성 확인. 실제 발전기 고장 탐지 정확도가 아님."},
              "hyperparameters": {"covariance_shrinkage": shrinkage,
                                  "log_std_floor": VIBRATION_LOG_STD_FLOOR.tolist(),
                                  "minimum_calibration_rms_g": VIBRATION_MIN_RMS_G,
                                  "max_training_rms_variation": 0.25},
              "parameter_provenance": "모두 진동 시연을 위한 개발 설정. 제조사 고장 한계값이 아님.",
              "scope": "이 부착 위치와 정상 진동원에만 적용. 위치·받침·방향·진동원이 바뀌면 새 기준 수집."}
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    result["baseline_id"] = "imu-" + digest[:20]
    return result


def score_vibration(features: dict, baseline: dict | None) -> dict:
    if not baseline:
        return _unknown("imu_baseline_required", source="live_imu")
    try:
        vector = _vibration_vector(features)
    except ValueError as error:
        return _unknown("invalid_imu_features", source="live_imu", detail=str(error))
    if baseline.get("schema_version") != "genguard.imu.baseline/1":
        return _unknown("incompatible_imu_baseline", source="live_imu")
    source = baseline.get("source", "live_imu")
    mean = np.asarray(baseline["mean"], dtype=float)
    precision = np.asarray(baseline["precision"], dtype=float)
    covariance = np.asarray(baseline["covariance"], dtype=float)
    score = _distance(vector, mean, precision)
    z = (vector - mean) / np.sqrt(np.diag(covariance))
    changed = score > baseline["threshold"]
    evidence = [{"field": key, "value": features[key], "expected": baseline["normal_values"][key],
                 "unit": "g" if key == "vibration_rms_g" else "1", "source": source,
                 "standardized_residual": float(z[i]),
                 "meaning": "실측 정상 진동 학습 기준 대비 편차. 발전기 결함 부위나 확률이 아님"}
                for i, key in enumerate(VIBRATION_FIELDS)]
    evidence.sort(key=lambda item: -abs(item["standardized_residual"]))
    return {"state": "change" if changed else "within_baseline", "score": score,
            "threshold": baseline["threshold"], "source": source,
            "baseline_id": baseline["baseline_id"], "algorithm": baseline["algorithm"],
            "evidence": evidence,
            "checks": ["IMU 부착·받침·진동원 조건이 기준 수집 때와 같은지 확인하세요.",
                       "실제 진동 강도·간헐 동작·외부 접촉의 변화를 확인하세요. 결함 부위 확정이 아닙니다."] if changed else [],
            "reason": "measured_vibration_change" if changed else "within_baseline"}
