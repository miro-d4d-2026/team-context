"""Synthetic design study, NOT measured generator data or deployment code.

All model/threshold choices are fixed before evaluating the test runs. Entire
runs are separated into fit, calibration and test groups. No fault labels are
used by fit/calibration. Illustrative relationships favor conditional models;
these results cannot rank algorithms on actual generator faults.
"""
from pathlib import Path
import json
import platform
import resource
import time

import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

ROOT = Path(__file__).resolve().parent
N = 120
DT = 2
INJECT = 60
CONSECUTIVE = 3
Q = 0.99
FIELDS = ["load_pct", "ambient_c", "rpm", "coolant_c", "oil_bar", "voltage_v", "vibration_rms_g"]


def make_run(run_id, scenario="normal"):
    rng = np.random.default_rng(102026 + run_id)
    load = rng.uniform(30, 70) + 0.8 * np.sin(np.arange(N) / 12)
    ambient = np.full(N, rng.uniform(18, 30))
    shared = rng.normal(0, 0.2, N)
    coolant = 72 + 0.16 * load + 0.18 * (ambient - 24) + shared + rng.normal(0, .12, N)
    oil = 3.9 - 0.01 * load - 0.018 * (coolant - 80) + rng.normal(0, .025, N)
    rpm = 1800 + rng.normal(0, 1.4, N)
    voltage = 240 - .018 * load + rng.normal(0, .12, N)
    vib = .035 + .001 * load + rng.normal(0, .0015, N)
    if scenario == "joint_inside_ranges":
        coolant[INJECT:] += 3
        oil[INJECT:] -= .25
    elif scenario == "rpm_outside_demo_limit":
        rpm[INJECT:] += 130
    elif scenario == "temperature_outside_reference_range":
        coolant[INJECT:] = 95 + rng.normal(0, .2, N-INJECT)
    elif scenario == "vibration_increase":
        vib[INJECT:] *= 2.3
    elif scenario != "normal":
        raise ValueError(scenario)
    values = np.column_stack([load, ambient, rpm, coolant, oil, voltage, vib])
    return {"id": run_id, "scenario": scenario, "values": values}


def design(x):
    return np.column_stack([np.ones(len(x)), (x[:, 0]-50)/20, (x[:, 1]-24)/6])


def first_persistent(flags, start=0):
    count = 0
    for i in range(start, len(flags)):
        count = count + 1 if flags[i] else 0
        if count == CONSECUTIVE:
            return int(i * DT)
    return None


def fixed_rules(x):
    # Illustrative bounds, NOT a uniform OEM alarm policy.
    # Coolant/oil ranges mirror the chosen reference product's operating gauge
    # ranges. RPM +/- 5%, voltage +/- 5%, vibration 0.22 g are DEMO assumptions.
    return ((x[:, 2] < 1710) | (x[:, 2] > 1890)
            | (x[:, 3] < 71) | (x[:, 3] > 91)
            | (x[:, 4] < 1.94) | (x[:, 4] > 4.42)
            | (x[:, 5] < 228) | (x[:, 5] > 252)
            | (x[:, 6] > .22))


def main():
    fit_ids, calibration_ids = list(range(1, 16)), list(range(101, 106))
    fit = np.concatenate([make_run(i)["values"] for i in fit_ids])
    cal = np.concatenate([make_run(i)["values"] for i in calibration_ids])
    # All hyperparameters and transformations specified before test scoring.
    t = time.perf_counter()
    d = design(fit)
    weights = np.linalg.solve(d.T @ d + np.diag([0, .1, .1]), d.T @ fit[:, 2:])
    center = (fit[:, 2:] - d @ weights).mean(axis=0)
    scale = (fit[:, 2:] - d @ weights).std(axis=0)

    def residual(x):
        return (x[:, 2:] - design(x) @ weights - center) / scale

    r = residual(fit)
    cov = np.cov(r.T, bias=True)
    precision = np.linalg.inv(.9 * cov + .1 * np.eye(cov.shape[0]))

    def md(x):
        r = residual(x)
        return np.sqrt(np.einsum("ij,jk,ik->i", r, precision, r) / r.shape[1])

    residual_fit_s = time.perf_counter() - t
    models, times = {}, {"conditional_mahalanobis": residual_fit_s}
    for name, data in [("raw_isolation_forest", fit), ("residual_isolation_forest", r)]:
        t = time.perf_counter()
        models[name] = IsolationForest(n_estimators=128, max_samples=256,
            contamination="auto", random_state=20261010, n_jobs=1).fit(data)
        times[name] = time.perf_counter() - t + (residual_fit_s if name.startswith("residual") else 0)

    scoring = {"raw_isolation_forest": lambda x: -models["raw_isolation_forest"].score_samples(x),
               "residual_isolation_forest": lambda x: -models["residual_isolation_forest"].score_samples(residual(x)),
               "conditional_mahalanobis": md}
    thresholds = {name: float(np.quantile(fn(cal), Q)) for name, fn in scoring.items()}
    cases = ["normal", "joint_inside_ranges", "rpm_outside_demo_limit",
             "temperature_outside_reference_range", "vibration_increase"]
    runs = [make_run(1001 + c*10 + j, scenario) for c, scenario in enumerate(cases) for j in range(5)]
    records, plot_rows = [], None
    for run in runs:
        x = run["values"]
        scores = {name: fn(x) for name, fn in scoring.items()}
        flags = {name: score > thresholds[name] for name, score in scores.items()}
        flags["fixed_rules"] = fixed_rules(x)
        if run["scenario"] == "joint_inside_ranges":
            assert not flags["fixed_rules"].any(), "Scenario must stay within the illustrative fixed bounds"
        outcome = {}
        for name, flag in flags.items():
            is_normal = run["scenario"] == "normal"
            first = first_persistent(flag, 0 if is_normal else INJECT)
            outcome[name] = {"first_persistent_s": first,
                "delay_s": None if first is None or is_normal else first-INJECT*DT,
                "flagged_frames": int(flag.sum()),
                "normal_prefix_false_alert": first_persistent(flag[:INJECT]) is not None}
        records.append({"run_id": run["id"], "scenario": run["scenario"], "outcome": outcome})
        if run["scenario"] == "joint_inside_ranges" and plot_rows is None:
            plot_rows = {"values": x.tolist(), "scores": {k:v.tolist() for k,v in scores.items()}}
    summary = {}
    for case in cases:
        subset = [r for r in records if r["scenario"] == case]
        summary[case] = {}
        for name in [*scoring, "fixed_rules"]:
            events = [r["outcome"][name] for r in subset]
            delays = [e["delay_s"] for e in events if e["delay_s"] is not None]
            summary[case][name] = {"persistent_alert_runs": sum(e["first_persistent_s"] is not None for e in events),
                "total_runs": len(events), "median_delay_s": float(np.median(delays)) if delays else None,
                "normal_prefix_false_alert_runs": sum(e["normal_prefix_false_alert"] for e in events)}
    latency = {}
    for name, fn in scoring.items():
        fn(cal[:1])
        duration = []
        for _ in range(30):
            t = time.perf_counter(); fn(cal[:1]); duration.append((time.perf_counter()-t)*1000)
        latency[name] = float(np.percentile(duration, 95))
    report = {"kind": "synthetic_design_study_not_generator_accuracy",
      "not_an_oem_alarm_configuration": True,
      "split": {"fit_normal_runs": fit_ids, "calibration_normal_runs": calibration_ids,
                "test_run_ids": [r["id"] for r in runs]},
      "fixed_before_evaluation": {"threshold_quantile": Q, "consecutive_windows": CONSECUTIVE,
         "sample_interval_s": DT, "injection_s": INJECT*DT,
         "trees": 128, "max_samples": 256, "seed": 20261010},
      "fields": FIELDS, "thresholds": thresholds, "summary": summary, "records": records,
      "host": {"platform": platform.platform(), "python": platform.python_version(),
         "sklearn": sklearn.__version__, "not_uno_q_benchmark": True},
      "fit_seconds": times, "single_frame_inference_p95_ms": latency,
      "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
      "model_tree_storage_bytes": {name: sum(e.tree_.__getstate__()["nodes"].nbytes +
          e.tree_.__getstate__()["values"].nbytes for e in m.estimators_) for name,m in models.items()},
      "limitations": ["Linear synthetic mechanism favors conditional linear models",
          "25 synthetic test runs do not represent actual generator fault modes",
          "No parameter tuning used test outcomes; only one experiment design and one seed",
          "Normal calibration 99th percentile is not guaranteed future false alarm rate",
          "Within illustrative fixed bounds does not certify equipment safety"]}
    (ROOT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    (ROOT / "joint-case-series.json").write_text(json.dumps(plot_rows, indent=2)+"\n")
    print(json.dumps({"summary": summary, "fit_seconds": times, "single_frame_p95_ms": latency}, indent=2))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    x = np.array(plot_rows["values"]); t = np.arange(N)*DT
    axes[0].plot(t, x[:, 3], label="Coolant (synthetic)")
    axes[0].axhspan(71, 91, color="#20b878", alpha=.10, label="Reference gauge range 71-91 C")
    axes[0].set_ylabel("Coolant C"); axes[0].legend(loc="upper left", fontsize=8)
    axes[1].plot(t, x[:, 4], color="#9467bd", label="Oil pressure (synthetic)")
    axes[1].axhspan(1.94, 4.42, color="#20b878", alpha=.10, label="Reference gauge range 1.94-4.42 bar")
    axes[1].set_ylabel("Oil pressure bar"); axes[1].legend(loc="lower left", fontsize=8)
    for name, scores in plot_rows["scores"].items():
        axes[2].plot(t, np.array(scores)/thresholds[name], label=name.replace("_", " "))
    axes[2].axhline(1, color="black", linestyle=":", label="Calibrated threshold")
    axes[2].set_ylabel("Score / threshold"); axes[2].set_xlabel("Steady operation seconds (synthetic)")
    axes[2].legend(loc="upper left", fontsize=8)
    for ax in axes:
        ax.axvline(INJECT*DT, color="#c33", linestyle="--"); ax.grid(alpha=.18)
    fig.suptitle("Synthetic case: within individual bounds, changed relation to load\nIllustration only; not generator fault accuracy or UNO Q timing", fontsize=12)
    fig.tight_layout(); fig.savefig(ROOT/"joint-comparison.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()
