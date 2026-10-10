"""Optional generator trend advisory, separate from the physical IMU domain."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

from bridge_service import BridgeError, canonical
from hybrid_service import HybridBridgeService
from trend_model import TrendMonitor
from trend_scenarios import scenario_catalog, generate_scenario

ROOT = Path(__file__).resolve().parent


class TrendBridgeService(HybridBridgeService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trend_monitors = {}
        self.trend_active = {}
        self.experiment_cache = {}
        try:
            self.trend_model = json.loads((ROOT / "trend_artifacts" / "model.json").read_text())
            TrendMonitor(self.trend_model)
        except (OSError, ValueError, KeyError, TypeError):
            self.trend_model = None

    def config(self):
        result = super().config()
        result["trend_policy"] = self.trend_catalog()["policy"]
        return result

    def trend_catalog(self):
        return {"scenarios": scenario_catalog(), "policy": {
            "lookback_s": 120, "horizon_s": 120, "source": "scenario",
            "scope": "수온·유압의 조건부 단기 추세 실험. 실제 IMU 예측·고장시점·잔여수명이 아님.",
            "time_mode": "server_elapsed_for_manual_inputs; simulated_for_experiments",
            "basis": "기존 시연 점검 경계 수온 92°C·유압 2.0kgf/cm². 제조사 확정값이 아님.",
            "integration": "experimental_advisory_only",
        }}

    def sample(self, sid, body):
        result = super().sample(sid, body)
        if result["duplicate"]:
            result["trend_notification_events"] = []
            return result
        try:
            if self.trend_model is None:
                raise ValueError("trend_model_unavailable")
            if sid not in self.trend_monitors:
                self.trend_monitors[sid] = TrendMonitor(self.trend_model)
            monitor = self.trend_monitors[sid]
            trend = monitor.update(result["elapsed_s"], body["values"],
                                   quality=body.get("quality", {}), phase=body["phase"])
        except Exception as exc:
            # A failed optional extension cannot lose a committed base result
            # or turn a retry into a different transaction.
            self.trend_monitors.pop(sid, None)
            sys.stderr.write(f"[trend unavailable] {type(exc).__name__}\n")
            trend = {"state": "unavailable", "reason": "trend_module_unavailable", "advisory": False,
                     "source": "scenario", "lookback_s": 120, "horizon_s": 120, "history_count": 0,
                     "channels": {}, "checks": [],
                     "summary": "추세 모듈을 사용할 수 없습니다. 기존 현재 상태 분석을 확인하고 서버 로그를 점검하세요."}
        result["trend"] = trend
        result["trend_extension"] = "genguard.trend-advisory/1"
        result["trend_notification_events"] = []
        trend["integration"] = "experimental_advisory_only"
        prior_advisory = self.trend_active.get(sid, False)
        advisory = bool(trend.get("advisory"))
        self.trend_active[sid] = advisory
        if advisory:
            alert = {"id": "generator_trend_advisory", "kind": "trend_experiment", "severity": "watch",
                     "title": "실험 추세 권고 · 현재 경보와 별도", "source": "scenario",
                     "checks": trend.get("checks", []), "control_action": "none"}
            # Held-out transient failures did not justify promoting this optional
            # advisory into the operational status or the existing alarm stream.
            if not prior_advisory:
                result["trend_notification_events"].append({
                    **alert, "id": f"{sid}:{body['seq']}:generator_trend_advisory",
                })
        # Retries return this exact historical analysis and cannot grow its history.
        with self.runtime.db:
            self.runtime.db.execute(
                "UPDATE bridge_samples SET result=? WHERE session_id=? AND seq=?",
                (canonical(result), sid, body["seq"]))
        return result

    def close_session(self, sid, body):
        result = super().close_session(sid, body)
        self.trend_monitors.pop(sid, None)
        self.trend_active.pop(sid, None)
        return result

    def trend_experiment(self, body):
        catalog = {row["id"]: row for row in scenario_catalog()}
        if not isinstance(body, dict) or set(body) != {"scenario_id"} or not isinstance(body["scenario_id"], str) or body["scenario_id"] not in catalog:
            raise BridgeError(422, "invalid_trend_scenario", "목록에 있는 추세 실험을 선택하세요.")
        scenario_id = body["scenario_id"]
        if scenario_id not in self.experiment_cache:
            self.experiment_cache[scenario_id] = self._experiment(scenario_id, catalog[scenario_id])
        return copy.deepcopy(self.experiment_cache[scenario_id])

    def _experiment(self, scenario_id, metadata, *, seed=901, include_evaluation=True):
        # Each replay gets its own monitor: it cannot train/change the live IMU or
        # a manually operated session. Only prior rows enter either model.
        if self.trend_model is None:
            raise BridgeError(503, "trend_unavailable", "추세 모델 파일을 확인하고 서버를 다시 시작하세요.")
        monitor = TrendMonitor(self.trend_model)
        rows = []
        anomaly_streak = 0
        onset = {"coolant": None, "oil": None}
        first = {"first_current_anomaly_s": None, "first_trend_advisory_s": None,
                 "first_fixed_warning_s": None, "first_limit_crossing_s": None}
        for frame in generate_scenario(scenario_id, seed=seed):
            t, values = frame["t_s"], frame["values"]
            phase, quality = frame.get("phase", "running"), frame.get("quality", {})
            trend = monitor.update(t, values, quality=quality, phase=phase)
            current = self.analyzer.score_generator(values, quality) if phase == "running" else {"state": "unknown", "score": None, "threshold": 2}
            anomaly_streak = anomaly_streak + 1 if current["state"] == "change" else 0
            current["persistent_change"] = anomaly_streak >= 3
            current["change_streak"] = anomaly_streak
            fixed = False
            crossing = False
            for key, field, compare in (("coolant", "coolant_c", lambda v: v >= 92),
                                         ("oil", "oil_kgf_cm2", lambda v: v < 2.0)):
                q = quality.get(field, {})
                valid = (phase == "running" and isinstance(values.get(field), (int, float))
                         and not isinstance(values.get(field), bool)
                         and (not q or isinstance(q, dict) and q.get("quality", "ok") == "ok" and q.get("age_s", 0) <= 5))
                exceeded = valid and compare(values[field])
                crossing = crossing or exceeded
                onset[key] = (t if onset[key] is None else onset[key]) if exceeded else None
                fixed = fixed or (exceeded and t - onset[key] >= 5)
            signals = {"first_current_anomaly_s": current["persistent_change"],
                       "first_trend_advisory_s": bool(trend.get("advisory")),
                       "first_fixed_warning_s": fixed, "first_limit_crossing_s": crossing}
            for key, active in signals.items():
                if active and first[key] is None:
                    first[key] = t
            rows.append({"t_s": t, "values": values, "quality": quality, "phase": phase,
                         "trend": trend, "current_anomaly": current, "fixed_warning": bool(fixed)})
        def difference(later, earlier):
            return None if first[later] is None or first[earlier] is None else first[later] - first[earlier]
        comparison = dict(first,
            trend_lead_s=difference("first_fixed_warning_s", "first_trend_advisory_s"),
            added_lead_vs_current_s=difference("first_current_anomaly_s", "first_trend_advisory_s"),
            note="10초 공통 표본의 동일 모의 운전 비교. 음수이면 추세 권고가 더 늦었습니다. 현재 이상은 3창, 고정 경보는 5초 지속 확인. 실사용 2초 표본의 현재 감지는 최대 16초 더 빠를 수 있습니다.")
        report = ROOT / "trend_artifacts" / "evaluation.json"
        evaluation = json.loads(report.read_text()) if include_evaluation and report.is_file() else {}
        comparison_report = ROOT / "trend_artifacts" / "value_evaluation.json"
        if include_evaluation and comparison_report.is_file():
            value_report = json.loads(comparison_report.read_text())
            groups = [value_report["summary"][key] for key in ("coolant_drift", "oil_drift", "joint_drift")]
            evaluation["current_anomaly_comparison"] = {
                "worsening_runs": sum(group["runs"] for group in groups),
                "current_earlier_runs": sum(group["trend_later_than_current_runs"] for group in groups),
                "trend_earlier_runs": sum(group["trend_earlier_than_current_runs"] for group in groups),
                "source": "same_held_out_synthetic_runs_10_second_sampling",
            }
        return {"scenario": metadata, "source": "scenario", "time_mode": "simulated", "rows": rows,
                "comparison": comparison, "evaluation": evaluation,
                "scope": "합성 시계열 실험이며 실제 발전기 성능이 아닙니다. 재생 속도는 모의 운전 시간만 바꾸며 실측 IMU와 분리됩니다."}
