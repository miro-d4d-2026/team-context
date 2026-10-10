"""Learn normal condition-response relationships from confirmed past runs.

This is a current-state comparison, not a future forecast or fault classifier.
Every target is independently fitted within its own source/mounting domain;
synthetic coolant never teaches a physical IMU a generator correlation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime

import numpy as np

ALGORITHM = "inspection-context-ridge-residual/1"
CONTEXT = ("load_pct", "ambient_c", "engine_rpm", "elapsed_s")
TOLERANCE = {"load_pct": 5, "ambient_c": 5, "engine_rpm": 50, "elapsed_s": 120}
RANGES = {"load_pct": (0, 100), "ambient_c": (-40, 60), "engine_rpm": (500, 4000),
          "elapsed_s": (300, 7200), "coolant_c": (-40, 150), "oil_kgf_cm2": (0, 15), "vibration_rms_g": (0, 30)}
SPECS = {
    "coolant_c": {"label": "냉각수 온도", "unit": "°C", "floor": 2.0, "scale_floor": .4,
                  "checks": ["냉각수량·누수 여부 확인", "라디에이터·통풍 상태 확인", "온도 센서와 운전 조건 확인"]},
    "oil_kgf_cm2": {"label": "윤활유 압력", "unit": "kgf/cm²", "floor": .2, "scale_floor": .035,
                      "checks": ["오일량·누유 여부 확인", "윤활유 필터·압력 센서 확인", "제조사 점검 절차에 따라 윤활계통 확인"]},
    "vibration_rms_g": {"label": "진동 RMS", "unit": "g", "floor": math.log(1.25), "scale_floor": .06,
                         "checks": ["센서 부착 위치·고정 상태 확인", "장비 체결·받침 상태 확인", "동일 조건에서 다시 측정하고 회전체 점검"]}}
VALID_SOURCES = {"scenario", "live_imu", "recorded_imu", "controller", "recorded_controller"}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quality_ok(entry):
    if entry is None or entry == "ok":
        return True
    if not isinstance(entry, dict) or entry.get("quality", "ok") != "ok" or entry.get("valid", True) is not True:
        return False
    age = entry.get("age_s", 0)
    return number(age) and 0 <= age <= 5


def valid(record, field, values=None):
    value = (values if values is not None else record.get("values", {})).get(field)
    lo, hi = RANGES[field]
    return number(value) and lo <= value <= hi and quality_ok(record.get("quality", {}).get(field))


def timestamp(record):
    try:
        dt = datetime.fromisoformat(record["inspected_at"].replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def source(record, field):
    return record.get("sources", {}).get(field)


def visible_value(value):
    return value if number(value) else None


def domain_match(a, b, field):
    if source(a, field) not in VALID_SOURCES or source(a, field) != source(b, field):
        return False, "source_mismatch"
    if field == "vibration_rms_g":
        if source(a, field) in {"live_imu", "recorded_imu"}:
            verified = {"controller", "recorded_controller", "live_sensor", "recorded_sensor", "device_clock"}
            if any(source(record, context) not in verified for record in (a, b) for context in CONTEXT):
                return False, "unverified_sensor_operating_context"
        if not a.get("installation_id") or a.get("installation_id") != b.get("installation_id"):
            return False, "installation_mismatch"
        keys = ("sample_rate_hz", "window_s", "dc_removed", "feature_version")
        acq_a, acq_b = a.get("acquisition", {}), b.get("acquisition", {})
        if any(key not in acq_a or key not in acq_b or acq_a[key] != acq_b[key] for key in keys):
            return False, "acquisition_mismatch"
        for key in ("accel_range_g", "configured_sample_rate_hz"):
            if key in acq_a or key in acq_b:
                if not number(acq_a.get(key)) or acq_a.get(key) != acq_b.get(key) or acq_a[key] <= 0:
                    return False, "acquisition_mismatch"
        if not number(acq_a["sample_rate_hz"]) or acq_a["sample_rate_hz"] <= 0 or not number(acq_a["window_s"]) or acq_a["window_s"] <= 0 or acq_a["dc_removed"] is not True:
            return False, "acquisition_invalid"
    return True, "matched"


def transform(field, value):
    return math.log(max(value, 1e-9)) if field == "vibration_rms_g" else value


def inverse(field, value):
    return float(math.exp(min(max(value, -50), 50))) if field == "vibration_rms_g" else float(value)


class InspectionComparator:
    def __init__(self, records):
        self.records = copy.deepcopy(records)
        self._last_models = {}
        self._fit_cache = {}

    @staticmethod
    def _fit(records, field):
        xs, ys, used = [], [], []
        for record in records:
            values_list = record.get("samples") or [record.get("values", {})]
            rows = []
            # Duplicate windows must not manufacture training evidence.
            seen = set()
            for values in values_list:
                if not isinstance(values, dict) or not all(valid(record, f, values) for f in (*CONTEXT, field)):
                    continue
                signature = tuple(values[f] for f in (*CONTEXT, field))
                if signature in seen:
                    continue
                seen.add(signature)
                rows.append(values)
            if rows:
                used.append(record["id"])
                xs.extend([[row[f] for f in CONTEXT] for row in rows])
                ys.extend([transform(field, row[field]) for row in rows])
        if len(set(used)) < 3 or len(xs) < 36:
            return None
        x, y = np.array(xs), np.array(ys)
        mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), [1, 1, 5, 10])
        design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
        penalty = np.eye(design.shape[1]) * 1.0
        penalty[0, 0] = 0
        weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        residual_scale = max(float(np.std(y - design @ weights)), SPECS[field]["scale_floor"])
        return {"field": field, "context": list(CONTEXT), "mean": mean.tolist(), "scale": scale.tolist(),
                "weights": weights.tolist(), "residual_scale": residual_scale,
                "context_low": x.min(axis=0).tolist(), "context_high": x.max(axis=0).tolist(),
                "training_record_ids": list(dict.fromkeys(used)), "training_sample_count": len(xs),
                "threshold": max(SPECS[field]["floor"], 3 * residual_scale),
                "pair_threshold": max(SPECS[field]["floor"], 3 * math.sqrt(2) * residual_scale)}

    @staticmethod
    def _expected(model, values):
        x = np.array([values[f] for f in CONTEXT])
        return float(np.r_[1, (x - model["mean"]) / model["scale"]] @ np.array(model["weights"]))

    def compare(self, current, reference_id=None):
        current = copy.deepcopy(current)
        now = timestamp(current)
        prior = [r for r in self.records if timestamp(r) is not None and now is not None and timestamp(r) < now
                 and r.get("id") != current.get("id") and r.get("equipment_id") == current.get("equipment_id")
                 and r.get("phase") == current.get("phase") == "running"]
        prior.sort(key=timestamp, reverse=True)
        base = {"schema_version": "genguard.inspection-comparison/1", "state": "unavailable",
                "headline": "비교 기준이 필요합니다", "summary": "같은 조건의 이전 정상 점검 기록을 선택해 주세요.",
                "reference_id": None, "reference_at": None, "conditions": {"current": {f: visible_value(current.get("values", {}).get(f)) for f in CONTEXT}, "previous": None, "tolerances": TOLERANCE},
                "matches": [], "channels": [], "recommendations": [], "history_count": len(prior), "source": current.get("source", "unknown")}
        models = {}
        current_context_valid = current.get("phase") == "running" and all(valid(current, f) for f in CONTEXT)
        eligible = [r for r in prior if r.get("confirmed_normal") is True and all(valid(r, f) for f in CONTEXT) and current_context_valid
                    and all(abs(current["values"][f] - r["values"][f]) <= TOLERANCE[f] for f in CONTEXT)]
        reference = next((r for r in eligible if r["id"] == reference_id), None) if reference_id else next(iter(eligible), None)
        unavailable_reason = "comparable_history_required"
        if reference is None and current_context_valid:
            selected = next((r for r in prior if r["id"] == reference_id), None) if reference_id else None
            normal_prior = [r for r in prior if r.get("confirmed_normal") is True and all(valid(r, f) for f in CONTEXT)]
            candidate = None
            if selected is not None and selected.get("confirmed_normal") is not True:
                unavailable_reason = "unconfirmed_reference"
                base.update(headline="승인된 정상 기록을 선택해 주세요", summary="선택한 점검은 정상 기준으로 승인되지 않아 비교하지 않았습니다.")
            elif reference_id:
                candidate = selected if selected in normal_prior else None
            elif normal_prior:
                candidate = min(normal_prior, key=lambda r: sum(abs(current["values"][f] - r["values"][f]) / TOLERANCE[f] for f in CONTEXT))
            if candidate is not None:
                unavailable_reason = "operating_conditions_mismatch"
                # Diagnostic context only: never select an ineligible record
                # for channel comparisons or model inference.
                base["conditions"].update(previous={f: candidate["values"][f] for f in CONTEXT},
                                           candidate_id=candidate["id"], candidate_at=candidate["inspected_at"])
                base["matches"] = [{"field": f, "current": current["values"][f], "previous": candidate["values"][f],
                                    "delta": current["values"][f] - candidate["values"][f], "tolerance": TOLERANCE[f],
                                    "matched": abs(current["values"][f] - candidate["values"][f]) <= TOLERANCE[f]} for f in CONTEXT]
                labels = {"load_pct": "부하율", "ambient_c": "외기온", "engine_rpm": "회전수", "elapsed_s": "운전 경과 시간"}
                differences = " · ".join(labels[m["field"]] for m in base["matches"] if not m["matched"])
                base.update(headline="운전 조건이 달라 비교를 보류합니다", summary=f"{differences} 차이가 비교 허용 범위를 벗어났습니다. 같은 조건의 점검 기록이 필요합니다.")
        base["comparison_blocker"] = unavailable_reason if reference is None else None
        if reference:
            base.update(reference_id=reference["id"], reference_at=reference["inspected_at"], reference_type="confirmed_normal")
            base["conditions"]["previous"] = {f: reference["values"][f] for f in CONTEXT}
            base["matches"] = [{"field": f, "current": current["values"][f], "previous": reference["values"][f],
                                "delta": current["values"][f] - reference["values"][f], "tolerance": TOLERANCE[f], "matched": True} for f in CONTEXT]
        for field, spec in SPECS.items():
            channel = {"field": field, "label": spec["label"], "unit": spec["unit"],
                       "current": visible_value(current.get("values", {}).get(field)), "previous": visible_value(reference.get("values", {}).get(field)) if reference else None,
                       "delta": None, "delta_pct": None, "adjusted_delta": None, "expected": None,
                       "state": "unavailable", "reason": unavailable_reason, "checks": [], "source": source(current, field),
                       "reference_source": source(reference, field) if reference else None, "threshold": None,
                       "observation": {"current_window_s": current.get("sample_duration_s"),
                                       "current_sample_count": len(current.get("samples") or [1]),
                                       "reference_sample_count": len(reference.get("samples") or [1]) if reference else 0,
                                       "reference_summary": "저장된 점검 대표값", "comparison_stage": "시동 후 5분 이상, 경과 시간 차이 2분 이내"}}
            base["channels"].append(channel)
            if not current_context_valid:
                channel["reason"] = "invalid_operating_context"
                continue
            if not valid(current, field):
                channel["reason"] = "invalid_or_stale_current_value"
                continue
            if not reference:
                continue
            if not valid(reference, field):
                channel["reason"] = "invalid_reference_value"
                continue
            compatible, reason = domain_match(current, reference, field)
            if not compatible:
                channel["reason"] = reason
                continue
            training = [r for r in prior if r.get("confirmed_normal") is True and domain_match(current, r, field)[0]]
            cache_key = (field, tuple(r["id"] for r in training))
            if cache_key not in self._fit_cache:
                self._fit_cache[cache_key] = self._fit(training, field)
            model = self._fit_cache[cache_key]
            if model is None:
                channel["reason"] = "insufficient_confirmed_normal_history"
                continue
            x = [current["values"][f] for f in CONTEXT]
            if any(not model["context_low"][i] - TOLERANCE[f] <= x[i] <= model["context_high"][i] + TOLERANCE[f] for i, f in enumerate(CONTEXT)):
                channel["reason"] = "outside_learned_conditions"
                continue
            models[field] = model
            expected_t = self._expected(model, current["values"])
            previous_expected_t = self._expected(model, reference["values"])
            cv, pv = current["values"][field], reference["values"][field]
            # Translate the reference into the CURRENT conditions before
            # comparing. For RMS this is a learned multiplicative correction.
            previous_adjusted = inverse(field, transform(field, pv) + expected_t - previous_expected_t)
            normal_deviation = transform(field, cv) - expected_t
            paired_deviation = transform(field, cv) - transform(field, previous_adjusted)
            normal_changed = abs(normal_deviation) > model["threshold"]
            previous_changed = abs(paired_deviation) > model["pair_threshold"]
            changed = normal_changed or previous_changed
            channel.update(delta=cv - pv, delta_pct=(cv / pv - 1) * 100 if pv != 0 else None,
                           adjusted_delta=cv - previous_adjusted, expected=inverse(field, expected_t),
                           previous_adjusted=previous_adjusted, standardized_deviation=normal_deviation / model["residual_scale"],
                           state="inspect" if changed else "normal", reason="condition_adjusted_change" if changed else "within_learned_history",
                           checks=spec["checks"] if changed else [],
                           evidence={"outside_normal_model": normal_changed, "changed_from_previous": previous_changed},
                           threshold={"normal_deviation": model["threshold"], "paired_deviation": model["pair_threshold"],
                                      "unit": "log-ratio" if field == "vibration_rms_g" else spec["unit"], "provenance": "시연용 점검 정책·정상 기록에서 학습한 변동폭. 제조사 보호 한계값이 아님."})
            if changed:
                if previous_changed:
                    direction = "증가" if channel["adjusted_delta"] > 0 else "감소"
                    reason = f"운전 조건을 보정한 값이 이전 정상 점검보다 {direction}했습니다."
                else:
                    reason = f"같은 조건에서 학습한 {spec['label']}의 정상 변동 범위를 벗어났습니다."
                base["recommendations"].append({"title": f"{spec['label']} 변화 확인", "reason": reason, "checks": spec["checks"]})
        valid_channels = [c for c in base["channels"] if c["state"] != "unavailable"]
        changed = [c for c in valid_channels if c["state"] == "inspect"]
        unavailable = len(base["channels"]) - len(valid_channels)
        if changed:
            base.update(state="inspect", headline="이전 점검과 다른 변화가 있습니다", summary=" · ".join(c["label"] for c in changed) + " 변화를 확인하고 점검해 주세요.")
        elif valid_channels:
            base.update(state="normal" if not unavailable else "unavailable", headline="이전 점검과 비슷합니다" if not unavailable else "일부 항목은 비교 기준이 필요합니다", summary="비교 가능한 항목은 정상 기록의 변동 범위 안에 있습니다.")
        if unavailable:
            base["summary"] += f" {unavailable}개 항목은 비교하지 못했습니다."
        digest = hashlib.sha256(json.dumps(models, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
        used = sorted({rid for m in models.values() for rid in m["training_record_ids"]})
        base["model"] = {"algorithm": ALGORITHM, "model_id": f"inspection-{digest}", "trained": bool(models),
                         "training_record_ids": used, "training_record_count": len(used),
                         "training_sample_count": max((m["training_sample_count"] for m in models.values()), default=0),
                         "channel_sample_counts": {f: m["training_sample_count"] for f, m in models.items()},
                         "source": "confirmed_historical_normal_only", "current_used_for_training": False,
                         "meaning": "조건별 정상값을 학습한 회귀 모델 + 잔차 점검 정책. 고장 원인·수명 예측 모델이 아님."}
        self._last_models = models
        return base

    def export_model(self, current):
        result = self.compare(current)
        return {**result["model"], "models": copy.deepcopy(self._last_models), "threshold_provenance": "시연용 개발 설정. 제조사 한계값이 아님."}
