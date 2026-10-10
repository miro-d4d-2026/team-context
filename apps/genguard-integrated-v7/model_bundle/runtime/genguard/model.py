"""Small conditional Gaussian model, exported as JSON (never pickle).

Learn expected sensor values from operating context using ridge regression,
then learn covariance between residuals. Score is a scaled Mahalanobis distance,
NOT a fault probability. Hyperparameters are engineering choices, not OEM limits.
"""
from __future__ import annotations

import numpy as np

ALGORITHM = "context-ridge-residual-covariance/1"


def feature_value(value, spec):
    if spec.get("transform") == "log":
        return float(np.log(max(float(value), 1e-9)))
    return float(value)


def inverse_value(value, spec):
    return float(np.exp(np.clip(value, -50, 50))) if spec.get("transform") == "log" else float(value)


def vectors(frame, profile):
    context = [frame["elapsed_s"] if key == "elapsed_s" else frame["readings"][key]["value"]
               for key in profile["context"]]
    target = [feature_value(frame["readings"][key]["value"], profile["fields"][key])
              for key in profile["targets"]]
    return np.asarray(context, dtype=float), np.asarray(target, dtype=float)


def fit_phase(frames, profile):
    pairs = [vectors(frame, profile) for frame in frames]
    x, y = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    mean = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 1e-6)
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.eye(design.shape[1]) * profile["model"]["ridge_alpha"]
    penalty[0, 0] = 0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    residual = y - design @ weights
    residual_mean = residual.mean(axis=0)
    floors = np.array([profile["fields"][key]["scale_floor"] for key in profile["targets"]])
    residual_scale = np.maximum(residual.std(axis=0), floors)
    standardized = (residual - residual_mean) / residual_scale
    covariance = standardized.T @ standardized / len(standardized)
    shrink = profile["model"]["covariance_shrinkage"]
    covariance = (1 - shrink) * covariance + shrink * np.eye(len(floors))
    precision = np.linalg.inv(covariance)
    tolerances = np.array([profile["context_tolerance"][key] for key in profile["context"]])
    return {
        "context_mean": mean.tolist(), "context_scale": scale.tolist(),
        "context_low": (x.min(axis=0) - tolerances).tolist(),
        "context_high": (x.max(axis=0) + tolerances).tolist(),
        "weights": weights.tolist(), "residual_mean": residual_mean.tolist(),
        "residual_scale": residual_scale.tolist(), "precision": precision.tolist(),
        "fit_count": len(frames), "threshold": None,
    }


def score_phase(frame, profile, phase_model):
    x, y = vectors(frame, profile)
    low, high = np.asarray(phase_model["context_low"]), np.asarray(phase_model["context_high"])
    outside = [profile["context"][i] for i in range(len(x)) if not low[i] <= x[i] <= high[i]]
    design = np.r_[1.0, (x - phase_model["context_mean"]) / phase_model["context_scale"]]
    expected = design @ np.asarray(phase_model["weights"]) + phase_model["residual_mean"]
    z = (y - expected) / phase_model["residual_scale"]
    score = float(np.sqrt(max(float(z @ np.asarray(phase_model["precision"]) @ z), 0) / len(y)))
    evidence = []
    for i in np.argsort(-np.abs(z))[:3]:
        key = profile["targets"][int(i)]
        evidence.append({"field": key, "value": frame["readings"][key]["value"],
                         "expected": inverse_value(expected[i], profile["fields"][key]),
                         "unit": profile["fields"][key]["unit"], "standardized_residual": float(z[i]),
                         "meaning": "조건 대비 편차이며 고장 원인 기여도나 확률이 아님"})
    return {"score": score, "outside_context": outside, "evidence": evidence}


def predict(frame, profile, bundle):
    phase = bundle["phases"].get(frame["phase"])
    if phase is None:
        return {"state": "unknown", "reason": "untrained_phase", "score": None,
                "threshold": None, "evidence": []}
    result = score_phase(frame, profile, phase)
    result.update(threshold=phase["threshold"])
    if result["outside_context"]:
        result.update(state="unknown", reason="unseen_operating_context")
    else:
        result.update(state="change" if result["score"] > phase["threshold"] else "within_baseline",
                      reason="multivariate_change" if result["score"] > phase["threshold"] else "within_baseline")
    return result
