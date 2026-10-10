#!/usr/bin/env python3
"""Reproducible synthetic-run training, calibration and untouched test report."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

from trend_model import CHANNELS, HORIZON_S, LIMITS, TrendMonitor, feature_vector, forecast
from trend_scenarios import SCENARIOS, generate_scenario

FIT_SEEDS = list(range(1000, 1040))
CALIBRATION_SEEDS = list(range(2000, 2012))
# 3000..3019 were inspected during the exploratory strict-band design. Keep
# that report, and never reuse those runs as the final untouched test split.
TEST_SEEDS = list(range(4000, 4020))


def examples(seeds):
    for name, _, _ in SCENARIOS:
        for seed in seeds:
            run = generate_scenario(name, seed)
            for end in range(12, len(run) - 12):
                history = run[end - 12:end + 1]
                future = np.array([run[end + 12]["values"][k] for k in CHANNELS])
                yield name, seed, history, future


def fit():
    xs, ys = [], []
    contexts = defaultdict(list)
    for _, _, history, future in examples(FIT_SEEDS):
        xs.append(feature_vector(history))
        ys.append(future - np.array([history[-1]["values"][k] for k in CHANNELS]))
        for key in ("load_pct", "ambient_c", "engine_rpm"):
            contexts[key].append(history[-1]["values"][key])
    x, y = np.array(xs), np.array(ys)
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1e-8)
    design = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.eye(design.shape[1]) * 10.0
    penalty[0, 0] = 0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    model = {"schema_version": "genguard.trend.model/1", "algorithm": "calibration-selected-short-horizon/1",
             "learned_candidate": "ridge-future-delta/1", "feature_mean": mean.tolist(),
             "feature_scale": scale.tolist(), "weights": weights.tolist(), "ridge_alpha": 10.0,
             "source": "synthetic_scenarios_only", "lookback_s": 120, "horizon_s": 120,
             "context_bounds": {key: [float(min(v) - margin), float(max(v) + margin)]
                                for (key, v), margin in zip(contexts.items(), (2., 1., 5.))},
             "context_bound_method": "Fit-set observed min/max plus demo margins load2 percentage points, ambient1C, rpm5.",
             "normal_background_source": "research/t4-6/genguard-trained-208v-v1/generator.py; normal coolant/oil context formulas reused. New drift and interventions are synthetic assumptions.",
             "target_fields": list(CHANNELS), "training_examples": len(x),
             "training_runs": len(FIT_SEEDS) * len(SCENARIOS),
             "selected_method": {}, "absolute_error_band": {},
             "split": {"fit_seeds": FIT_SEEDS, "calibration_seeds": CALIBRATION_SEEDS,
                       "test_seeds": TEST_SEEDS,
                       "unit": "complete simulated run identified by scenario and seed"},
             "selection_rule": "Per channel minimum calibration MAE among ridge, linear, persistence. Never select from test.",
             "exploratory_seeds_excluded": list(range(3000, 3020)),
             "interval_method": "90th percentile absolute error on independent calibration runs, selected method; not physical confidence.",
             "limits": LIMITS,
             "assumptions": "All scenario equations, thresholds, persistence and timing are demo assumptions; no real generator failure validation."}
    calibration_errors = {m: [] for m in ("ridge", "linear", "persistence")}
    for _, _, history, future in examples(CALIBRATION_SEEDS):
        for method in calibration_errors:
            calibration_errors[method].append(np.abs(forecast(history, model, method) - future))
    calibration_errors = {k: np.array(v) for k, v in calibration_errors.items()}
    calibration_mae = {m: {k: float(e[:, i].mean()) for i, k in enumerate(CHANNELS)}
                       for m, e in calibration_errors.items()}
    for i, key in enumerate(CHANNELS):
        selected = min(calibration_mae, key=lambda m: calibration_mae[m][key])
        model["selected_method"][key] = selected
        model["absolute_error_band"][key] = float(np.quantile(calibration_errors[selected][:, i], .9))
    model["calibration_mae"] = calibration_mae
    return model


def evaluate(model, seeds=None, dataset_name="test"):
    seeds = TEST_SEEDS if seeds is None else seeds
    errors = {m: [] for m in ("ridge", "linear", "persistence", "selected")}
    by_scenario = {name: {m: [] for m in errors} for name, _, _ in SCENARIOS}
    for name, _, history, future in examples(seeds):
        for method in errors:
            error = np.abs(forecast(history, model, method) - future)
            errors[method].append(error)
            by_scenario[name][method].append(error)
    def mae(data):
        return {method: {key: float(np.asarray(es)[:, i].mean()) for i, key in enumerate(CHANNELS)}
                for method, es in data.items()}
    run_results = []
    for name, _, _ in SCENARIOS:
        for seed in seeds:
            run = generate_scenario(name, seed)
            monitor = TrendMonitor(model)
            first_advisory = first_threshold = None
            warned_times = []
            first_advisory_by_channel = {k: None for k in CHANNELS}
            first_threshold_by_channel = {k: None for k in CHANNELS}
            recovering_seen = False
            for row in run:
                v = row["values"]
                for key in CHANNELS:
                    at_boundary = v[key] >= LIMITS[key] if key == "coolant_c" else v[key] < LIMITS[key]
                    if first_threshold_by_channel[key] is None and at_boundary:
                        first_threshold_by_channel[key] = row["t_s"]
                if first_threshold is None and (v["coolant_c"] >= LIMITS["coolant_c"]
                                                 or v["oil_kgf_cm2"] < LIMITS["oil_kgf_cm2"]):
                    first_threshold = row["t_s"]
                result = monitor.update(**row)
                if result["advisory"]:
                    for key, channel in result["channels"].items():
                        if channel.get("advisory_streak", 0) >= 3:
                            warned_times.append((row["t_s"], key))
                            if first_advisory_by_channel[key] is None:
                                first_advisory_by_channel[key] = row["t_s"]
                    if first_advisory is None:
                        first_advisory = row["t_s"]
                recovering_seen = recovering_seen or result["state"] == "recovering"
            channel_lead = {key: first_threshold_by_channel[key] - first_advisory_by_channel[key]
                            if first_threshold_by_channel[key] is not None and first_advisory_by_channel[key] is not None
                            else None for key in CHANNELS}
            valid_leads = [value for value in channel_lead.values() if value is not None and 0 < value <= HORIZON_S]
            lead = min(valid_leads) if valid_leads else None
            false_windows = 0
            false_by_channel = {k: 0 for k in CHANNELS}
            for warned_t, key in warned_times:
                if warned_t + HORIZON_S > run[-1]["t_s"]:
                    continue  # Future is unobserved, not a proved false alert.
                future_rows = [r for r in run if warned_t < r["t_s"] <= warned_t + HORIZON_S]
                if not any((r["values"][key] >= LIMITS[key] if key == "coolant_c"
                            else r["values"][key] < LIMITS[key]) for r in future_rows):
                    false_windows += 1
                    false_by_channel[key] += 1
            run_results.append({"scenario": name, "seed": seed,
                                "first_advisory_s": first_advisory, "first_fixed_threshold_s": first_threshold,
                                "lead_s": lead, "advisory_updates": len(warned_times),
                                "false_advisory_updates_with_observed_future": false_windows,
                                "first_advisory_by_channel_s": first_advisory_by_channel,
                                "first_threshold_by_channel_s": first_threshold_by_channel,
                                "same_channel_lead_s": channel_lead,
                                "false_advisory_by_channel": false_by_channel,
                                "recovery_state_seen": recovering_seen,
                                "threshold_reached": first_threshold is not None,
                                "early_warning": lead is not None and 0 < lead <= HORIZON_S,
                                "unresolved_late_advisory": first_advisory is not None and first_threshold is None
                                    and first_advisory + HORIZON_S > run[-1]["t_s"]})
    summary = {}
    for name, _, _ in SCENARIOS:
        runs = [r for r in run_results if r["scenario"] == name]
        leads = [r["lead_s"] for r in runs if r["early_warning"]]
        summary[name] = {"runs": len(runs), "threshold_reached_runs": sum(r["threshold_reached"] for r in runs),
                         "runs_with_advisory": sum(r["first_advisory_s"] is not None for r in runs),
                         "early_warning_runs": sum(r["early_warning"] for r in runs),
                         "missed_early_warning_runs": sum(r["threshold_reached"] and not r["early_warning"] for r in runs),
                         "runs_with_false_advisory": sum(r["false_advisory_updates_with_observed_future"] > 0 for r in runs),
                         "unresolved_late_advisory_runs": sum(r["unresolved_late_advisory"] for r in runs),
                         "median_lead_s": float(np.median(leads)) if leads else None}
    overall = mae(errors)
    return {"schema_version": "genguard.trend.evaluation/1", "source": "synthetic_held_out_runs", "dataset_name": dataset_name,
            "model_selection_used_test": False, "split": model["split"],
            "test_runs": len(seeds) * len(SCENARIOS), "test_windows": len(errors["selected"]),
            "lookback_s": 120, "horizon_s": 120,
            "mae": overall, "mae_by_scenario": {name: mae(data) for name, data in by_scenario.items()},
            "selected_method": model["selected_method"], "absolute_error_band": model["absolute_error_band"],
            "ridge_beats_linear_on_test": {key: overall["ridge"][key] < overall["linear"][key] for key in CHANNELS},
            "advisory_summary": summary, "run_results": run_results,
            "interpretation": [
                "MAE includes stable, drift, recovery and unexpected transitions. Selection used only calibration runs.",
                "Repeated overlapping windows within each run are not independent samples; split is by whole run.",
                "An early warning means before the demo fixed threshold, not before actual generator failure.",
                "A false advisory has no threshold crossing within the stated 120-second horizon when that future is observed.",
                "Warnings are evaluated against the SAME channel's boundary. lead_s is the minimum successful same-channel lead, not an unrelated first-system threshold.",
                "Unannounced steps/interventions cannot be predicted reliably from history; recovery can make a reasonable trend extrapolation false.",
                "This evaluates internally generated synthetic families, not transfer to real equipment. Do not report as field accuracy.",
                "A learned forecast is not automatically more useful than current-state anomaly detection. Compare alert time and actionability separately."
            ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(Path(__file__).parent / "trend_artifacts"))
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model = fit()
    policy_evaluations = {}
    for policy in ("empirical_band", "point_forecast"):
        model["advisory_policy"] = policy
        report = evaluate(model, CALIBRATION_SEEDS, "calibration")
        summary = report["advisory_summary"]
        early = sum(x["early_warning_runs"] for x in summary.values())
        false = sum(x["runs_with_false_advisory"] for x in summary.values())
        policy_evaluations[policy] = {"summary": summary, "early_warning_runs": early,
                                      "runs_with_false_advisory": false, "selection_score": early - 2 * false}
    model["advisory_policy"] = max(policy_evaluations, key=lambda p: policy_evaluations[p]["selection_score"])
    model["advisory_policy_selection"] = {"dataset": "calibration runs only", "candidates": policy_evaluations,
        "criterion": "early-warning runs minus twice false-advisory runs; demo utility assumption, not operational cost estimate"}
    evaluation = evaluate(model)
    (out / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (out / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"training_runs": model["training_runs"], "test_runs": evaluation["test_runs"],
                      "selected_method": model["selected_method"], "mae": evaluation["mae"],
                      "advisory_summary": evaluation["advisory_summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
