#!/usr/bin/env python3
"""Run the GenGuard edge model (context-ridge-residual-covariance/1) on ClikaRT.

The trained model JSON from genguard-edge-v1 is rebuilt as an ONNX graph with
ClikaRT's own builder, so scoring needs no extra converter:

    xs = (X - context_mean) / context_scale
    expected = xs @ W[1:] + (W[0] + residual_mean)
    z = (Y - expected) / residual_scale
    score = sqrt(max(z P z, 0) / n_targets)

X is the operating context (e.g. load %, ambient C), Y the targets after each
field's transform (log for vibration RMS). The state rules (untrained phase,
unseen context, score > threshold) stay in Python exactly as in model.predict.
The score is a scaled Mahalanobis distance, not a fault probability.

With --frames it also replays stored windows (JSONL of {"payload", "result"})
and compares the ClikaRT score and state with the result the original numpy
model saved.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ALGORITHM = "context-ridge-residual-covariance/1"


def feature_value(value: float, spec: dict) -> float:
    if spec.get("transform") == "log":
        return math.log(max(float(value), 1e-9))
    return float(value)


def build_onnx(crt, phase: dict, n_context: int, n_targets: int, path: str) -> None:
    f32 = lambda a: crt.tensor(np.asarray(a, dtype=np.float32))
    weights = np.asarray(phase["weights"], dtype=np.float64)
    model = crt.io.OnnxModel.create("genguard_edge_score", opset_version=21)
    model.add_input("X", crt.float32, [-1, n_context])
    model.add_input("Y", crt.float32, [-1, n_targets])
    mean = model.add_initializer(f32(phase["context_mean"]))
    inv_scale = model.add_initializer(f32(1.0 / np.asarray(phase["context_scale"])))
    w = model.add_initializer(f32(weights[1:]))
    bias = model.add_initializer(f32(weights[0] + np.asarray(phase["residual_mean"])))
    inv_rscale = model.add_initializer(f32(1.0 / np.asarray(phase["residual_scale"])))
    precision = model.add_initializer(f32(phase["precision"]))
    ones = model.add_initializer(f32(np.ones((n_targets, 1))))
    inv_n = model.add_initializer(f32([1.0 / n_targets]))

    (centered,) = model.add_node("Sub", ["X", mean])
    (xs,) = model.add_node("Mul", [centered, inv_scale])
    (lin,) = model.add_node("MatMul", [xs, w])
    (expected,) = model.add_node("Add", [lin, bias])
    (resid,) = model.add_node("Sub", ["Y", expected])
    (z,) = model.add_node("Mul", [resid, inv_rscale])
    (zp,) = model.add_node("MatMul", [z, precision])
    (zpz,) = model.add_node("Mul", [zp, z])
    (quad,) = model.add_node("MatMul", [zpz, ones])
    (nonneg,) = model.add_node("Relu", [quad])
    (mean_sq,) = model.add_node("Mul", [nonneg, inv_n])
    (score,) = model.add_node("Sqrt", [mean_sq])
    model.add_output(score, crt.float32, [-1, 1])
    model.add_output(z, crt.float32, [-1, n_targets])
    model.add_output(expected, crt.float32, [-1, n_targets])
    model.save(path)


def frame_arrays(frames: list[dict], profile: dict) -> tuple[np.ndarray, np.ndarray]:
    x = [[f["elapsed_s"] if k == "elapsed_s" else f["readings"][k]["value"] for k in profile["context"]]
         for f in frames]
    y = [[feature_value(f["readings"][k]["value"], profile["fields"][k]) for k in profile["targets"]]
         for f in frames]
    return np.asarray(x, dtype=np.float32), np.asarray(y, dtype=np.float32)


def decide(frame: dict, x: np.ndarray, score: float, profile: dict, phase: dict) -> dict:
    """Same state rules as genguard.model.predict, applied to a ClikaRT score."""
    low, high = phase["context_low"], phase["context_high"]
    outside = [profile["context"][i] for i in range(len(x)) if not low[i] <= float(x[i]) <= high[i]]
    if outside:
        return {"state": "unknown", "reason": "unseen_operating_context", "score": score, "outside_context": outside}
    changed = score > phase["threshold"]
    return {"state": "change" if changed else "within_baseline",
            "reason": "multivariate_change" if changed else "within_baseline", "score": score}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="trained model JSON from genguard-edge-v1")
    parser.add_argument("--profile", required=True, help="the profile the model was trained with")
    parser.add_argument("--phase", default="running")
    parser.add_argument("--onnx", default="genguard_edge_score.onnx")
    parser.add_argument("--frames", help="JSONL of stored {payload, result} windows to compare")
    parser.add_argument("--output", default="genguard-clikart-compare.json")
    args = parser.parse_args()

    import clika_runtime as crt

    bundle = json.loads(Path(args.model).read_text())
    profile = json.loads(Path(args.profile).read_text())
    if bundle.get("algorithm") != ALGORITHM:
        raise SystemExit(f"unsupported algorithm: {bundle.get('algorithm')}")
    phase = bundle["phases"][args.phase]
    n_ctx, n_tgt = len(profile["context"]), len(profile["targets"])
    build_onnx(crt, phase, n_ctx, n_tgt, args.onnx)
    graph = crt.onnx.load(args.onnx, dynamic_axes={"X": {0: "batch"}, "Y": {0: "batch"}}, device="cpu")
    report: dict = {"clikart_version": crt.version(), "model_id": bundle.get("model_id"),
                    "data_domain": bundle.get("data_domain"), "phase": args.phase,
                    "onnx": args.onnx, "onnx_bytes": Path(args.onnx).stat().st_size}

    if args.frames:
        rows = [json.loads(line) for line in Path(args.frames).read_text().splitlines() if line.strip()]
        # The model's own per-window output is result["current_comparison"];
        # the top-level state adds the stream's persistence rules on top of it.
        rows = [r for r in rows if r["payload"].get("phase") == args.phase
                and isinstance((r["result"].get("current_comparison") or {}).get("score"), (int, float))]
        frames = [r["payload"] for r in rows]
        x, y = frame_arrays(frames, profile)
        start = time.perf_counter()
        out = graph(X=crt.tensor(x), Y=crt.tensor(y))
        batch_ms = (time.perf_counter() - start) * 1000
        scores = next(iter(out.values())).numpy().reshape(-1)
        single = []
        for i in range(min(50, len(frames))):
            t0 = time.perf_counter()
            graph(X=crt.tensor(x[i:i + 1]), Y=crt.tensor(y[i:i + 1]))
            single.append((time.perf_counter() - t0) * 1000)
        original = np.asarray([r["result"]["current_comparison"]["score"] for r in rows])
        states = [decide(f, x[i], float(scores[i]), profile, phase)["state"] for i, f in enumerate(frames)]
        agree = sum(s == r["result"]["current_comparison"]["state"] for s, r in zip(states, rows))
        diff = np.abs(scores.astype(np.float64) - original)
        report["comparison"] = {
            "windows": len(rows),
            "max_abs_score_diff": float(diff.max()),
            "max_rel_score_diff": float((diff / np.maximum(np.abs(original), 1e-9)).max()),
            "state_agreement": f"{agree}/{len(rows)}",
            "original_states": {s: sum(r["result"]["current_comparison"]["state"] == s for r in rows)
                                for s in sorted({r["result"]["current_comparison"]["state"] for r in rows})},
            "batch_ms": round(batch_ms, 3),
            "single_window_ms_median": round(float(np.median(single)), 3),
        }
        report["passed"] = agree == len(rows) and float(diff.max()) < 1e-3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed", True) else 1


if __name__ == "__main__":
    sys.exit(main())
