"""Short-horizon scenario forecast and conservative sustained-trend advisory.

No real-generator life, fault probability, or physical IMU relationship is
estimated. Model parameters are learned from explicitly synthetic complete runs.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

STEP_S = 10
LOOKBACK_S = 120
HORIZON_S = 120
CHANNELS = ("coolant_c", "oil_kgf_cm2")
REQUIRED = CHANNELS + ("load_pct", "ambient_c", "engine_rpm")
LIMITS = {"coolant_c": 92.0, "oil_kgf_cm2": 2.0}
UNITS = {"coolant_c": "°C", "oil_kgf_cm2": "kgf/cm²"}
MIN_SLOPE = {"coolant_c": 0.25, "oil_kgf_cm2": 0.025}
MODEL_PATH = Path(__file__).parent / "trend_artifacts" / "model.json"


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def slope(times, values):
    t = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    tc = t - t.mean()
    return float(tc @ (y - y.mean()) / (tc @ tc)) if tc @ tc > 0 else 0.0


def feature_vector(rows):
    """Only past and present values: levels, lag differences and two slopes."""
    if len(rows) != 13:
        raise ValueError("13 ten-second observations are required")
    times = [r["t_s"] for r in rows]
    features = []
    for key in CHANNELS:
        y = [r["values"][key] for r in rows]
        features.extend([y[-1], y[-1] - y[-2], y[-1] - y[-4],
                         y[-1] - y[-7], y[-1] - y[-13],
                         slope(times[:7], y[:7]) * 60,
                         slope(times[-7:], y[-7:]) * 60])
    for key in ("load_pct", "ambient_c", "engine_rpm"):
        features.extend([rows[-1]["values"][key],
                         rows[-1]["values"][key] - rows[0]["values"][key]])
    return np.asarray(features, dtype=float)


def forecast(rows, model, method="selected"):
    x = feature_vector(rows)
    current = np.array([rows[-1]["values"][k] for k in CHANNELS])
    design = np.r_[1., (x - np.asarray(model["feature_mean"])) /
                   np.asarray(model["feature_scale"])]
    ridge = current + design @ np.asarray(model["weights"])
    linear = np.array([current[i] + slope([r["t_s"] for r in rows],
                                        [r["values"][k] for r in rows]) * HORIZON_S
                       for i, k in enumerate(CHANNELS)])
    candidates = {"ridge": ridge, "linear": linear, "persistence": current}
    if method != "selected":
        return candidates[method]
    return np.array([candidates[model["selected_method"][k]][i]
                     for i, k in enumerate(CHANNELS)])


class TrendMonitor:
    """Session-local state. Call with real elapsed or explicit scenario time.

    Repeated HTTP submissions cannot fabricate history: observations closer than
    10 s are ignored. Interruptions and changed operating conditions invalidate
    the prior window. The service must keep scenario time separate from live time.
    """
    def __init__(self, model=None):
        self.model = copy.deepcopy(model) if model is not None else json.loads(MODEL_PATH.read_text())
        self._validate_model()
        self.reset()

    def _validate_model(self):
        m = self.model
        if (m.get("schema_version") != "genguard.trend.model/1" or m.get("lookback_s") != LOOKBACK_S
                or m.get("horizon_s") != HORIZON_S or m.get("target_fields") != list(CHANNELS)
                or m.get("limits") != LIMITS):
            raise ValueError("Unsupported trend model schema, timing, targets or limits")
        for key, shape in (("feature_mean", (20,)), ("feature_scale", (20,)), ("weights", (21, 2))):
            a = np.asarray(m.get(key), dtype=float)
            if a.shape != shape or not np.isfinite(a).all() or (key == "feature_scale" and np.any(a <= 0)):
                raise ValueError(f"Invalid trend model array: {key}")
        for key in CHANNELS:
            if (m.get("selected_method", {}).get(key) not in {"ridge", "linear", "persistence"}
                    or not number(m.get("absolute_error_band", {}).get(key))
                    or m["absolute_error_band"][key] < 0):
                raise ValueError("Invalid selected method or empirical error band")
        for key in ("load_pct", "ambient_c", "engine_rpm"):
            bounds = m.get("context_bounds", {}).get(key)
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or not all(number(x) for x in bounds) or bounds[0] >= bounds[1]):
                raise ValueError("Missing learned operating-context bounds")

    def reset(self):
        self.rows = []
        self.last = None
        self.streak = {k: 0 for k in CHANNELS}

    def _result(self, state, reason, summary, *, channels=None, advisory=False, checks=None):
        result = {"state": state, "reason": reason, "summary": summary,
                  "advisory": advisory, "source": "scenario", "lookback_s": LOOKBACK_S,
                  "horizon_s": HORIZON_S, "history_count": len(self.rows),
                  "observed_span_s": self.rows[-1]["t_s"] - self.rows[0]["t_s"] if self.rows else 0.,
                  "channels": channels or {}, "checks": checks or [],
                  "algorithm": self.model["algorithm"],
                  "interval_label": "합성 보정 데이터의 90% 절대오차 범위. 실제 신뢰구간 아님.",
                  "limit_provenance": "수온 92°C·유압 2.0 kgf/cm²: 시연용 점검 경계 가정",
                  "scope": "모의 발전기 계측값의 2분 후 추세. 실제 고장·잔여수명·IMU 예측 아님.",
                  "accepted": True}
        self.last = copy.deepcopy(result)
        return result

    def update(self, t_s, values, quality=None, phase="running"):
        if phase != "running":
            self.reset()
            return self._result("unavailable", "not_running", "운전 중 구간에서만 추세를 비교합니다.")
        quality = quality or {}
        valid = number(t_s) and t_s >= 0 and isinstance(values, dict) and isinstance(quality, dict)
        for key in REQUIRED:
            v = values.get(key) if isinstance(values, dict) else None
            q = quality.get(key, "ok") if isinstance(quality, dict) else "invalid"
            good_q = q == "ok" or (isinstance(q, dict) and q.get("valid", True) is True
                      and q.get("quality", "ok") == "ok" and number(q.get("age_s", 0))
                      and 0 <= q.get("age_s", 0) <= 5)
            valid = valid and number(v) and good_q
        if valid:
            valid = (0 <= values["coolant_c"] <= 150 and 0 <= values["oil_kgf_cm2"] <= 15
                     and 0 <= values["load_pct"] <= 110 and -40 <= values["ambient_c"] <= 80
                     and 500 <= values["engine_rpm"] <= 3000)
        if not valid:
            self.reset()
            return self._result("unavailable", "invalid_or_stale_input", "누락·오래된 계측값이 있어 추세 판단을 중단합니다.")
        outside = [key for key, (low, high) in self.model["context_bounds"].items()
                   if not low <= values[key] <= high]
        if outside:
            self.reset()
            return self._result("unavailable", "unseen_operating_context", "학습한 부하·외기온·회전수 범위를 벗어나 미래 추정을 보류합니다.")
        # Check fresh current limits even between admitted ten-second samples;
        # never keep displaying an obsolete early forecast after an actual alarm.
        reached = [k for k in CHANNELS if (values[k] >= LIMITS[k] if k == "coolant_c"
                                          else values[k] < LIMITS[k])]
        if reached:
            self.streak = {k: 0 for k in CHANNELS}
            return self._result("limit_reached", "current_limit_reached", "이미 시연 점검 경계에 도달했습니다. 현재 경보를 확인하세요.",
                                channels={k: {"current": float(values[k]), "limit": LIMITS[k],
                                               "unit": UNITS[k], "predicted": None,
                                               "lower": None, "upper": None, "slope_per_min": None}
                                          for k in CHANNELS})
        reset_reason = None
        if self.rows:
            elapsed = t_s - self.rows[-1]["t_s"]
            if elapsed < 0:
                self.reset()
                return self._result("unavailable", "clock_regression", "시간이 역행했습니다. 새 운전 구간을 시작하세요.")
            if elapsed < STEP_S - 1:
                previous = self.rows[-1]["values"]
                changed = (abs(values["load_pct"] - previous["load_pct"]) >= 5
                           or abs(values["engine_rpm"] - previous["engine_rpm"]) >= 60
                           or abs(values["ambient_c"] - previous["ambient_c"]) >= 2
                           or abs(values["coolant_c"] - previous["coolant_c"]) > 1.2
                           or abs(values["oil_kgf_cm2"] - previous["oil_kgf_cm2"]) > .20)
                reversal = (self.streak["coolant_c"] >= 3 and values["coolant_c"] < previous["coolant_c"] - .05
                            or self.streak["oil_kgf_cm2"] >= 3 and values["oil_kgf_cm2"] > previous["oil_kgf_cm2"] + .005)
                if changed or reversal:
                    self.reset()
                    self.rows.append({"t_s": float(t_s), "values": {k: float(values[k]) for k in REQUIRED}})
                    return self._result("collecting", "new_values_invalidate_previous_trend", "값이 급변하거나 회복하여 이전 추세 알림을 해제했습니다. 새 구간을 수집합니다.")
                result = copy.deepcopy(self.last)
                result["accepted"] = False
                result["ignored_reason"] = "sample_interval_not_elapsed"
                return result
            previous = self.rows[-1]["values"]
            if elapsed > STEP_S + 1:
                reset_reason = "observation_gap"
            elif (abs(values["load_pct"] - previous["load_pct"]) >= 5
                  or abs(values["engine_rpm"] - previous["engine_rpm"]) >= 60
                  or abs(values["ambient_c"] - previous["ambient_c"]) >= 2):
                reset_reason = "operating_context_changed"
            elif (abs(values["coolant_c"] - previous["coolant_c"]) > 1.2 * elapsed / 10
                  or abs(values["oil_kgf_cm2"] - previous["oil_kgf_cm2"]) > 0.20 * elapsed / 10):
                reset_reason = "abrupt_step"
            if reset_reason:
                self.reset()
        self.rows.append({"t_s": float(t_s), "values": {k: float(values[k]) for k in REQUIRED}})
        self.rows = self.rows[-13:]
        if len(self.rows) < 13:
            return self._result("collecting", reset_reason or "history_required",
                                f"동일 운전 조건의 2분 기록 수집 중 ({len(self.rows)}/13).")
        if abs(self.rows[-1]["t_s"] - self.rows[0]["t_s"] - LOOKBACK_S) > 3:
            self.streak = {k: 0 for k in CHANNELS}
            return self._result("unavailable", "irregular_sample_timing", "학습과 다른 시간 간격입니다. 10초 간격의 2분 구간을 다시 모읍니다.")
        if any(np.ptp([r["values"][k] for r in self.rows]) > threshold
               for k, threshold in (("load_pct", 3), ("engine_rpm", 40), ("ambient_c", 1.5))):
            self.streak = {k: 0 for k in CHANNELS}
            return self._result("unavailable", "unstable_operating_context", "부하·회전수·외기온이 변해 추세 경고를 보류합니다.")
        predictions = forecast(self.rows, self.model)
        output, warned, worsening, recovering = {}, [], [], []
        times = [r["t_s"] for r in self.rows]
        for i, key in enumerate(CHANNELS):
            y = np.array([r["values"][key] for r in self.rows])
            direction = 1 if key == "coolant_c" else -1
            full_slope = slope(times, y) * 60
            recent_slope = slope(times[-7:], y[-7:]) * 60
            old_slope = slope(times[:7], y[:7]) * 60
            is_worse = (direction * full_slope >= MIN_SLOPE[key]
                        and direction * recent_slope >= MIN_SLOPE[key]
                        and direction * old_slope >= MIN_SLOPE[key]
                        and np.mean(direction * np.diff(y) > 0) >= 0.75)
            is_recovering = (direction * old_slope > MIN_SLOPE[key]
                             and direction * recent_slope < -MIN_SLOPE[key])
            error = self.model["absolute_error_band"][key]
            predicted = float(predictions[i])
            lower, upper = predicted - error, predicted + error
            if self.model.get("advisory_policy", "empirical_band") == "point_forecast":
                crossing = predicted >= LIMITS[key] if direction == 1 else predicted < LIMITS[key]
            else:
                crossing = lower >= LIMITS[key] if direction == 1 else upper < LIMITS[key]
            self.streak[key] = self.streak[key] + 1 if is_worse and crossing else 0
            if is_worse:
                worsening.append(key)
            if is_recovering:
                recovering.append(key)
            if self.streak[key] >= 3:
                warned.append(key)
            output[key] = {"current": float(y[-1]), "slope_per_min": full_slope,
                           "recent_slope_per_min": recent_slope, "predicted": predicted,
                           "lower": lower, "upper": upper, "limit": LIMITS[key],
                           "unit": UNITS[key], "method": self.model["selected_method"][key],
                           "sustained_worsening": bool(is_worse), "advisory_streak": self.streak[key],
                           "boundary_crossing": bool(crossing),
                           "advisory_policy": self.model.get("advisory_policy", "empirical_band"),
                           "error_band_straddles_limit": bool(lower <= LIMITS[key] <= upper)}
        if warned:
            checks = []
            if "coolant_c" in warned:
                checks.append("수온 상승 추세 확인 후 냉각수량·누수·라디에이터 점검 우선순위를 검토하세요.")
            if "oil_kgf_cm2" in warned:
                checks.append("유압 하락 추세 확인 후 오일량·누유·센서 상태 점검 우선순위를 검토하세요.")
            return self._result("deteriorating", "sustained_approach_to_demo_limit",
                                "악화가 지속되며 2분 후 시연 점검 경계에 접근할 가능성이 있습니다. 예측 오차를 함께 확인하고 점검을 준비하세요.",
                                channels=output, advisory=True, checks=checks)
        if recovering:
            return self._result("recovering", "recent_recovery", "최근 변화가 회복 방향입니다. 사전 점검 알림을 보류합니다.", channels=output)
        if worsening:
            return self._result("deteriorating", "worsening_without_persistent_limit_forecast",
                                "악화 방향의 변화는 있지만 사전 점검 알림 조건은 아직 충족하지 않았습니다.", channels=output)
        return self._result("stable", "no_sustained_worsening", "지속 악화와 경계 접근이 함께 확인되지 않았습니다.", channels=output)
