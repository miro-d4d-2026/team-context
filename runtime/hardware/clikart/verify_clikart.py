#!/usr/bin/env python3
"""Verify ClikaRT on this machine (meant for the UNO Q Linux side).

Builds the docs' tiny MLP (y = relu(X W + b), W = 0.5, b = 0.25), saves it as
an .onnx file, loads it back with crt.onnx.load and checks the known answer.
Records whether the network looked reachable, so an offline run can be told
apart from an online one. The license credential itself is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

EXPECTED = [[5.25, 5.25, 5.25], [0.0, 0.0, 0.0]]
X_ROWS = [[1, 2, 3, 4], [-1, 0.5, 2, -2]]


def has_default_route() -> bool | None:
    try:
        lines = Path("/proc/net/route").read_text().splitlines()[1:]
    except OSError:
        return None
    return any(line.split()[1] == "00000000" for line in lines if len(line.split()) > 1)


def dns_resolves(host: str, timeout_s: float = 3.0) -> bool:
    result = {"ok": False}

    def lookup() -> None:
        try:
            socket.getaddrinfo(host, 443)
            result["ok"] = True
        except OSError:
            pass

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return result["ok"]


def license_source() -> str:
    for name in ("CLIKA_RT_LICENSE", "CLIKA_LICENSE"):
        if os.environ.get(name):
            return f"env:{name}"
    if (Path.home() / ".clika" / "runtime" / "license").exists():
        return "file:~/.clika/runtime/license"
    return "none"


def run_tiny_mlp(crt, np) -> dict:
    model = crt.io.OnnxModel.create("tiny_mlp", opset_version=21)
    model.add_input("X", crt.float32, [-1, 4])
    w = model.add_initializer(crt.tensor(np.full((4, 3), 0.5, dtype=np.float32)))
    b = model.add_initializer(crt.tensor(np.full((3,), 0.25, dtype=np.float32)))
    (mm,) = model.add_node("MatMul", ["X", w])
    (summed,) = model.add_node("Add", [mm, b])
    (out,) = model.add_node("Relu", [summed])
    model.add_output(out, crt.float32, [-1, 3])

    x = crt.tensor(np.array(X_ROWS, dtype=np.float32))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "tiny_mlp.onnx")
        model.save(path)
        loaded = crt.onnx.load(path, dynamic_axes={"X": {0: "batch"}}, device="cpu")
        start = time.perf_counter()
        outputs = loaded(X=x)
        elapsed_ms = (time.perf_counter() - start) * 1000
    y = next(iter(outputs.values())).numpy()
    return {
        "output": y.tolist(),
        "matches_expected": bool(np.allclose(y, np.array(EXPECTED, dtype=np.float32))),
        "first_call_ms": round(elapsed_ms, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="clikart-verify.json")
    parser.add_argument("--require-offline", action="store_true",
                        help="fail unless the network looks unreachable")
    args = parser.parse_args()

    report: dict = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": {
            "machine": platform.machine(),
            "system": platform.system(),
            "python": platform.python_version(),
            "libc": "-".join(platform.libc_ver()),
        },
        "network": {
            "default_route": has_default_route(),
            "dns_resolves_docs_clika_io": dns_resolves("docs.clika.io"),
        },
        "license_source": license_source(),
    }
    offline = not report["network"]["dns_resolves_docs_clika_io"]
    report["network"]["looked_offline"] = offline

    ok = False
    try:
        import numpy as np
        import clika_runtime as crt

        report["clikart_version"] = crt.version()
        report["tiny_mlp"] = run_tiny_mlp(crt, np)
        ok = report["tiny_mlp"]["matches_expected"]
    except Exception as err:  # report every failure with ClikaRT's stable code name
        report["error"] = {
            "type": type(err).__name__,
            "code_name": getattr(err, "code_name", ""),
            "message": str(err),
        }

    if args.require_offline and not offline:
        report["offline_requirement"] = "failed: network still reachable"
        ok = False

    report["passed"] = ok
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
