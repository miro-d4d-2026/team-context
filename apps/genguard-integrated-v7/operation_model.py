"""Metric-specific, elapsed-phase-aligned comparison of operation histories.

Temperature learns five-minute rise rates. Pressure learns its normal warm-up
fall and the level reached at each phase. Vibration learns the normal RMS level
and checks a sudden amplitude ratio. These are distinct statistical questions.
Only approved, earlier, compatible records train the small ridge regressions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime

import numpy as np

ALGORITHM = "operation-phase-context-ridge/1"
CONTEXT = ("load_pct", "ambient_c", "engine_rpm")
TOLERANCE = {"load_pct": 5, "ambient_c": 5, "engine_rpm": 50, "initial_coolant_c": 5}
RANGES = {"load_pct": (0, 100), "ambient_c": (-40, 60), "engine_rpm": (500, 4000),
          "coolant_c": (-40, 150), "oil_kgf_cm2": (0, 15), "vibration_rms_g": (0, 30)}
SPECS = {
    "coolant_c": {"label": "냉각수 온도", "unit": "°C", "method": "phase_rise_rate",
                  "method_label": "운전 구간별 상승 속도", "rate_unit": "°C/분", "floor": .5, "scale_floor": .06,
                  "checks": ["냉각수량·누수 여부 확인", "라디에이터·통풍 상태 확인", "같은 부하와 초기 수온에서 다시 비교"]},
    "oil_kgf_cm2": {"label": "윤활유 압력", "unit": "kgf/cm²", "method": "warmup_rate_and_level",
                      "method_label": "예열 구간별 하락 속도·압력", "rate_unit": "kgf/cm²/분", "floor": .035, "scale_floor": .004,
                      "checks": ["오일량·누유 여부 확인", "윤활유 필터·압력 센서 확인", "제조사 절차에 따라 윤활계통 점검"]},
    "vibration_rms_g": {"label": "진동 RMS", "unit": "g", "method": "amplitude_ratio_and_step",
                         "method_label": "진동 크기·급변", "rate_unit": None, "floor": math.log(1.35), "scale_floor": .025,
                         "checks": ["센서 부착 위치·고정 상태 확인", "장비 체결·받침 상태 확인", "같은 운전 조건에서 진동을 다시 측정"]},
}
REASONS = {
    "invalid_operating_context": "부하·외기온·회전수 정보가 없어 비교할 수 없습니다.",
    "invalid_time_sequence": "운전 시각이 누락되었거나 순서가 올바르지 않습니다.",
    "interval_alignment_required": "같은 5분 구간의 기록이 필요합니다. 누락 구간을 보간하지 않았습니다.",
    "insufficient_observation": "첫 운전 구간이 끝나면 변화 속도를 비교합니다.",
    "comparable_history_required": "같은 장비의 이전 정상 점검 기록이 필요합니다.",
    "operating_conditions_mismatch": "부하·외기온·회전수 또는 초기 수온이 달라 비교를 보류합니다.",
    "unconfirmed_reference": "정상으로 확인된 이전 기록을 선택해 주세요.",
    "source_mismatch": "실측과 모의 기록을 서로 학습하거나 비교하지 않습니다.",
    "installation_mismatch": "진동 센서 부착 위치가 달라 비교를 보류합니다.",
    "acquisition_mismatch": "진동 수집 설정이 달라 비교를 보류합니다.",
    "invalid_current_value": "현재 구간에 유효한 계측값이 부족합니다.",
    "invalid_reference_value": "이전 기록의 해당 구간 값이 누락되었습니다.",
    "insufficient_training_history": "최소 3회의 서로 다른 정상 운전 기록이 필요합니다.",
    "invalid_starting_temperature": "초기 수온 정보가 없어 예열 조건을 비교할 수 없습니다.",
}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid(value, field):
    lo, hi = RANGES[field]
    return number(value) and lo <= value <= hi


def timestamp(record):
    try:
        dt = datetime.fromisoformat(record["inspected_at"].replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def time_reason(run):
    samples = run.get("samples")
    if not isinstance(samples, list) or not samples:
        return "invalid_time_sequence"
    if any(not isinstance(s, dict) or not number(s.get("elapsed_s")) or s["elapsed_s"] < 0 for s in samples):
        return "invalid_time_sequence"
    times = [s["elapsed_s"] for s in samples]
    if any(b <= a for a, b in zip(times, times[1:])):
        return "invalid_time_sequence"
    if times[0] != 0 or any(abs(t - i * 300) > .001 for i, t in enumerate(times)):
        return "interval_alignment_required"
    if len(samples) < 2:
        return "insufficient_observation"
    return None


def initial_coolant(run):
    samples = run.get("samples") or []
    return samples[0].get("coolant_c") if samples and isinstance(samples[0], dict) else None


def context_valid(run):
    c = run.get("context", {})
    return isinstance(c, dict) and all(valid(c.get(f), f) for f in CONTEXT)


def conditions_match(current, previous):
    return (context_valid(current) and context_valid(previous)
            and all(abs(current["context"][f] - previous["context"][f]) <= TOLERANCE[f] for f in CONTEXT)
            and valid(initial_coolant(current), "coolant_c") and valid(initial_coolant(previous), "coolant_c")
            and abs(initial_coolant(current) - initial_coolant(previous)) <= TOLERANCE["initial_coolant_c"])


def source_match(current, previous):
    return current.get("source") in {"scenario", "controller", "recorded_controller"} and current.get("source") == previous.get("source")


def vibration_match(current, previous):
    if not current.get("installation_id") or current.get("installation_id") != previous.get("installation_id"):
        return "installation_mismatch"
    a, b = current.get("acquisition", {}), previous.get("acquisition", {})
    required = ("sample_rate_hz", "window_s", "dc_removed", "feature_version")
    if any(k not in a or k not in b or a[k] != b[k] for k in required):
        return "acquisition_mismatch"
    if not number(a["sample_rate_hz"]) or a["sample_rate_hz"] <= 0 or not number(a["window_s"]) or a["window_s"] <= 0 or a["dc_removed"] is not True:
        return "acquisition_mismatch"
    for optional in ("accel_range_g", "configured_sample_rate_hz"):
        if optional in a or optional in b:
            if not number(a.get(optional)) or a[optional] <= 0 or a[optional] != b.get(optional):
                return "acquisition_mismatch"
    return None


def features(run):
    return [run["context"][f] for f in CONTEXT] + [initial_coolant(run)]


def _fit(rows, floor, scale_floor):
    """Fit normal response without using the current run's target or labels."""
    if len({row[0]["id"] for row in rows}) < 3:
        return None
    x = np.array([features(run) for run, _ in rows], dtype=float)
    y = np.array([target for _, target in rows], dtype=float)
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), [1, 1, 5, .5])
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.eye(design.shape[1]) * 1.0
    penalty[0, 0] = 0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    residual_scale = max(float(np.std(y - design @ weights)), scale_floor)
    return {"features": [*CONTEXT, "initial_coolant_c"], "mean": mean.tolist(), "scale": scale.tolist(),
            "weights": weights.tolist(), "residual_scale": residual_scale,
            "threshold": max(floor, 3 * residual_scale), "floor": floor,
            "training_record_ids": [r["id"] for r, _ in rows], "sample_count": len(rows)}


def expected(model, run):
    design = np.r_[1, (np.array(features(run)) - model["mean"]) / model["scale"]]
    return float(design @ np.array(model["weights"]))


def _minute_interval(interval):
    return f"{interval['start_s'] / 60:g}–{interval['end_s'] / 60:g}분"


class OperationAnalyzer:
    def __init__(self, normal_records):
        self.records = copy.deepcopy(normal_records)
        self._last_models = {}
        self._last_metadata = {}

    def _prior(self, current):
        now = timestamp(current)
        # Repeated IDs or duplicate complete runs do not create more evidence.
        seen_ids, seen_payloads, records = set(), set(), []
        for r in sorted(self.records, key=lambda r: timestamp(r) or 0, reverse=True):
            at = timestamp(r)
            if not r.get("id") or r.get("id") == current.get("id") or now is None or at is None or at >= now:
                continue
            if r.get("equipment_id") != current.get("equipment_id") or r.get("confirmed_normal") is not True:
                continue
            if not context_valid(r) or time_reason(r) or not valid(initial_coolant(r), "coolant_c"):
                continue
            signature = json.dumps({"context": r.get("context"), "samples": r.get("samples"), "source": r.get("source"),
                                    "installation_id": r.get("installation_id"), "acquisition": r.get("acquisition")}, sort_keys=True)
            if r["id"] in seen_ids or signature in seen_payloads:
                continue
            seen_ids.add(r["id"])
            seen_payloads.add(signature)
            records.append(r)
        return records

    def _models(self, current, records):
        models = {}
        for field, spec in SPECS.items():
            eligible = [r for r in records if source_match(current, r) and conditions_match(current, r)
                        and (field != "vibration_rms_g" or vibration_match(current, r) is None)]
            field_models = {}
            max_count = max((len(r["samples"]) for r in eligible), default=0)
            for index in range(1, max_count):
                pairs = [(r, r["samples"][index - 1], r["samples"][index]) for r in eligible if index < len(r["samples"])]
                pairs = [(r, a, b) for r, a, b in pairs if valid(a.get(field), field) and valid(b.get(field), field)]
                if field == "vibration_rms_g":
                    rows = [(r, math.log(max(b[field], 1e-6))) for r, a, b in pairs]
                else:
                    rows = [(r, (b[field] - a[field]) / ((b["elapsed_s"] - a["elapsed_s"]) / 60)) for r, a, b in pairs]
                fitted = _fit(rows, spec["floor"], spec["scale_floor"])
                if fitted is None:
                    continue
                if field == "oil_kgf_cm2":
                    fitted["level"] = _fit([(r, b[field]) for r, a, b in pairs], .25, .025)
                field_models[str(index)] = fitted
            models[field] = field_models
        return models

    def export_model(self):
        """Export the exact parameters used by the latest analyze call.

        A call to analyze must precede export: there is no implicit all-history
        fit that could accidentally include future records.
        """
        artifact = {"schema_version": "genguard.operation-model/1", "algorithm": ALGORITHM,
                    "models": copy.deepcopy(self._last_models), "metadata": copy.deepcopy(self._last_metadata),
                    "criteria_provenance": "학습값과 탐지 허용차는 합성 운전 시연 기준이며 제조사 한계값이 아닙니다."}
        return artifact

    def analyze(self, current_run, reference_id=None):
        current = copy.deepcopy(current_run)
        prior = self._prior(current)
        selected = next((r for r in prior if r["id"] == reference_id), None) if reference_id else None
        blocker = time_reason(current)
        if not context_valid(current):
            blocker = "invalid_operating_context"
        elif not valid(initial_coolant(current), "coolant_c"):
            blocker = "invalid_starting_temperature"
        candidates = [r for r in prior if source_match(current, r) and conditions_match(current, r)] if not blocker else []
        reference = next((r for r in candidates if r["id"] == reference_id), None) if reference_id else next(iter(candidates), None)
        if not blocker and reference is None:
            blocker = "comparable_history_required"
            requested = next((r for r in self.records if r.get("id") == reference_id), None) if reference_id else None
            if requested and requested.get("confirmed_normal") is not True:
                blocker = "unconfirmed_reference"
            elif (selected and not source_match(current, selected)) or (prior and not any(source_match(current, r) for r in prior)):
                blocker = "source_mismatch"
            elif selected or (prior and not reference_id):
                blocker = "operating_conditions_mismatch"
        models = self._models(current, prior) if not blocker else {f: {} for f in SPECS}
        ids = sorted({rid for by_phase in models.values() for m in by_phase.values() for rid in m["training_record_ids"]})
        model_hash = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()[:12]
        metadata = {"id": f"{ALGORITHM}:{model_hash}", "algorithm": ALGORITHM,
                    "training_count": len(ids), "training_record_count": len(ids), "training_record_ids": ids,
                    "training_sample_count": sum(len(r["samples"]) - 1 for r in prior if r["id"] in ids),
                    "current_used_for_training": False, "training_source": current.get("source"),
                    "synthetic": current.get("source") == "scenario", "phase_interval_s": 300,
                    "scope": "승인된 과거 정상 운전에서 구간별 변화 패턴을 학습합니다. 고장 부위·수명 예측이 아닙니다.",
                    "threshold_provenance": "모의 탐지 허용차. 실제 발전기 검증 및 제조사 안전 기준이 아님."}
        self._last_models, self._last_metadata = models, metadata
        conditions = {"current": {**current.get("context", {}), "initial_coolant_c": initial_coolant(current)},
                      "previous": {**reference["context"], "initial_coolant_c": initial_coolant(reference)} if reference else None,
                      "tolerances": copy.deepcopy(TOLERANCE), "matched": reference is not None}
        result = {"schema_version": "genguard.operation-comparison/1", "state": "unavailable", "headline": "비교 자료를 확인해 주세요",
                  "summary": REASONS.get(blocker, "같은 운전 구간의 변화를 비교했습니다."), "comparison_blocker": blocker,
                  "channels": [], "recommendations": [], "reference_id": reference["id"] if reference else None,
                  "reference_run": copy.deepcopy(reference), "reference_at": reference["inspected_at"] if reference else None,
                  "conditions": conditions, "model": metadata, "history_count": len(prior), "source": current.get("source"),
                  "observed_until_s": (current.get("samples") or [{}])[-1].get("elapsed_s")}
        for field, spec in SPECS.items():
            samples = current.get("samples") or []
            latest = samples[-1].get(field) if samples and isinstance(samples[-1], dict) else None
            channel = {"field": field, "label": spec["label"], "unit": spec["unit"], "state": "unavailable",
                       "method": spec["method"], "method_label": spec["method_label"], "current": latest if valid(latest, field) else None,
                       "previous": None, "delta": None, "interval": None, "observed_rate": None, "expected_rate": None,
                       "rate_unit": spec["rate_unit"], "rate_delta": None, "threshold": None, "expected_range": None,
                       "observed_delta": None, "expected_delta": None,
                       "change_ratio": None, "model_expected": None, "reason": blocker,
                       "summary": REASONS.get(blocker, ""), "checks": [], "intervals": [], "source": current.get("source"),
                       "reference_source": reference.get("source") if reference else None, "worst_interval": None}
            result["channels"].append(channel)
            if blocker:
                continue
            if field == "vibration_rms_g" and vibration_match(current, reference):
                channel.update(reason=vibration_match(current, reference), summary=REASONS[vibration_match(current, reference)])
                continue
            for i in range(1, len(samples)):
                a, b = samples[i - 1], samples[i]
                interval = {"start_s": a["elapsed_s"], "end_s": b["elapsed_s"]}
                entry = {"interval": interval, **interval, "state": "unavailable", "reason": None, "current": b.get(field) if valid(b.get(field), field) else None, "previous": None,
                         "observed_rate": None, "expected_rate": None, "rate_delta": None, "threshold": None,
                         "expected_range": None, "change_ratio": None, "step_ratio": None, "model_expected": None,
                         "delta": None, "severity_score": 0, "rate_unit": spec["rate_unit"], "current_change": None, "previous_change": None,
                         "observed_delta": None, "expected_delta": None}
                channel["intervals"].append(entry)
                if not valid(a.get(field), field) or not valid(b.get(field), field):
                    entry["reason"] = "invalid_current_value"
                    continue
                if i >= len(reference["samples"]) or not all(valid(s.get(field), field) for s in reference["samples"][i - 1:i + 1]):
                    entry["reason"] = "invalid_reference_value"
                    continue
                pa, pb = reference["samples"][i - 1:i + 1]
                m = models[field].get(str(i))
                if m is None:
                    entry["reason"] = "insufficient_training_history"
                    continue
                previous, prediction = pb[field], expected(m, current)
                entry.update(previous=previous, delta=b[field] - previous, threshold=m["threshold"], state="normal", reason="normal_phase_pattern",
                             current_change=b[field] - a[field], previous_change=pb[field] - pa[field], observed_delta=b[field] - a[field])
                if field == "vibration_rms_g":
                    typical = math.exp(prediction)
                    ratio = b[field] / max(typical, 1e-6)
                    step = b[field] / max(a[field], 1e-6)
                    score = math.log(max(ratio, 1e-6)) / m["threshold"]
                    step_score = math.log(max(step, 1e-6)) / math.log(1.5)
                    entry.update(model_expected=typical, change_ratio=ratio, step_ratio=step,
                                 expected_range=[typical * math.exp(-m["threshold"]), typical * math.exp(m["threshold"])],
                                 severity_score=max(abs(score), abs(step_score)))
                    if score > 1 or step_score > 1:
                        entry.update(state="inspect", reason="vibration_amplitude_increase")
                    elif score < -1 or step_score < -1:
                        entry.update(state="inspect", reason="vibration_amplitude_decrease")
                else:
                    rate = (b[field] - a[field]) / ((b["elapsed_s"] - a["elapsed_s"]) / 60)
                    residual = rate - prediction
                    entry.update(observed_rate=rate, expected_rate=prediction, rate_delta=residual,
                                 expected_delta=prediction * ((b["elapsed_s"] - a["elapsed_s"]) / 60),
                                 expected_range=[prediction - m["threshold"], prediction + m["threshold"]],
                                 model_expected=prediction, severity_score=abs(residual) / m["threshold"])
                    if field == "coolant_c" and residual > m["threshold"]:
                        entry.update(state="inspect", reason="coolant_rise_accelerated")
                    # Faster cooling is a measurable change, but by itself is
                    # not enough to recommend mechanical repair in this demo.
                    elif field == "oil_kgf_cm2":
                        level = expected(m["level"], current)
                        entry.update(expected_level=level, level_delta=b[field] - level, level_threshold=m["level"]["threshold"])
                        entry["severity_score"] = max(entry["severity_score"], abs(entry["level_delta"]) / entry["level_threshold"])
                        if residual < -m["threshold"]:
                            entry.update(state="inspect", reason="oil_pressure_drop_accelerated")
                        elif abs(entry["level_delta"]) > entry["level_threshold"]:
                            entry.update(state="inspect", reason="oil_pressure_phase_level_changed")
            if not channel["intervals"]:
                channel.update(reason="insufficient_observation", summary=REASONS["insufficient_observation"])
                continue
            latest_interval = channel["intervals"][-1]
            inspected = [row for row in channel["intervals"] if row["state"] == "inspect"]
            unavailable = [row for row in channel["intervals"] if row["state"] == "unavailable"]
            for key in ("previous", "delta", "interval", "observed_rate", "expected_rate", "rate_delta", "threshold", "expected_range", "change_ratio", "model_expected", "current_change", "previous_change", "step_ratio", "observed_delta", "expected_delta"):
                channel[key] = latest_interval.get(key)
            if inspected:
                worst = max(inspected, key=lambda row: row["severity_score"])
                channel.update(state="inspect", reason=worst["reason"], worst_interval=copy.deepcopy(worst), checks=spec["checks"][:])
                when = _minute_interval(worst["interval"])
                if field == "coolant_c":
                    text = f"{when} 수온 상승 속도 {worst['observed_rate']:.2f}°C/분 · 평소 예상 {worst['expected_rate']:.2f}°C/분"
                elif field == "oil_kgf_cm2":
                    text = f"{when} 유압 변화 {worst['observed_rate']:+.3f}kgf/cm²/분 · 평소 예상 {worst['expected_rate']:+.3f}kgf/cm²/분"
                    if worst["reason"] == "oil_pressure_phase_level_changed":
                        text = f"{when} 유압 {worst['current']:.2f}kgf/cm² · 해당 구간 예상 {worst['expected_level']:.2f}kgf/cm²"
                else:
                    text = f"{when} 진동 {worst['change_ratio']:.2f}배 · 같은 조건의 평소 진동 대비"
                if latest_interval["state"] == "normal" and latest_interval is not worst:
                    text += ". 현재 구간은 평소 변화로 돌아왔지만 앞선 이상 구간은 확인이 필요합니다."
                elif unavailable:
                    text += ". 일부 구간은 계측값이 부족해 판단하지 않았습니다."
                channel["summary"] = text
                result["recommendations"].append({"field": field, "title": f"{spec['label']} 변화 확인", "reason": text,
                                                   "checks": spec["checks"][:], "interval": worst["interval"]})
            elif unavailable:
                reason = unavailable[-1]["reason"]
                channel.update(reason=reason, summary=REASONS.get(reason, "비교할 기록이 부족합니다."))
            else:
                channel.update(state="normal", reason="normal_phase_pattern", summary={
                    "coolant_c": "온도는 올랐지만 각 구간의 상승 속도는 이전 정상 운전과 비슷합니다.",
                    "oil_kgf_cm2": "예열에 따른 압력 하락과 이후 안정화가 평소 패턴 안에 있습니다.",
                    "vibration_rms_g": "진동 크기가 평소 범위에서 유지되고 급격한 변화가 없습니다."}[field])
        states = [c["state"] for c in result["channels"]]
        result["state"] = "inspect" if "inspect" in states else "unavailable" if "unavailable" in states else "normal"
        if result["state"] == "normal":
            result.update(headline="평소 운전 변화와 비슷합니다", summary="수온의 상승 속도, 예열 중 유압 변화, 진동 크기를 각각의 기준으로 비교했습니다.")
        elif result["state"] == "inspect":
            names = " · ".join(c["label"] for c in result["channels"] if c["state"] == "inspect")
            result.update(headline=f"{names} 점검이 필요합니다", summary="같은 운전 조건의 과거 정상 기록과 다른 변화가 관찰되었습니다. 아래 구간과 근거를 확인하세요.")
        elif not blocker:
            result.update(headline="일부 항목은 비교를 보류합니다", summary="누락값이나 수집 조건을 확인해 주세요. 확인되지 않은 항목을 정상으로 표시하지 않습니다.")
        return result
