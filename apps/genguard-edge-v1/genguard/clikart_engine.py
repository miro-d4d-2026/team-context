"""ClikaRT backend for per-window scoring (inference only; training stays NumPy).

The trained phase model is rebuilt as an ONNX graph with ClikaRT's own builder:

    xs = (X - context_mean) / context_scale
    expected = xs @ W[1:] + (W[0] + residual_mean)
    z = (Y - expected) / residual_scale
    score = sqrt(max(z P z, 0) / n_targets)

which is model.raw_score in float32. Enable with GENGUARD_ENGINE=clikart. The
process needs a ClikaRT license (CLIKA_RT_LICENSE or clikart-license-init).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import numpy as np

PHASE_KEYS = ("context_mean", "context_scale", "weights", "residual_mean", "residual_scale", "precision")


def build_onnx(crt, phase, n_context, n_targets, path):
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


class ClikaScorer:
    """Drop-in for model.raw_score: (expected, z, score) computed by ClikaRT."""

    def __init__(self):
        import clika_runtime as crt

        self.crt = crt
        self.label = f"clikart-{crt.version()}"
        self._dir = tempfile.mkdtemp(prefix="genguard-clikart-")
        self._graphs = {}

    def graph(self, phase_model, n_context, n_targets):
        key = hashlib.sha256(json.dumps([phase_model[k] for k in PHASE_KEYS]).encode()).hexdigest()[:16]
        if key not in self._graphs:
            path = os.path.join(self._dir, f"{key}.onnx")
            build_onnx(self.crt, phase_model, n_context, n_targets, path)
            self._graphs[key] = self.crt.onnx.load(
                path, dynamic_axes={"X": {0: "batch"}, "Y": {0: "batch"}}, device="cpu")
        return self._graphs[key]

    def __call__(self, x, y, phase_model):
        graph = self.graph(phase_model, len(x), len(y))
        out = graph(X=self.crt.tensor(np.asarray([x], dtype=np.float32)),
                    Y=self.crt.tensor(np.asarray([y], dtype=np.float32)))
        score, z, expected = (out[name].numpy()[0] for name in graph.output_names)
        return expected.astype(np.float64), z.astype(np.float64), float(score[0])
