#!/usr/bin/env python3
"""GenGuard console server for the UNO Q (stdlib only, besides the edge model).

Serves the web console (static/) and three JSON endpoints:

  POST /api/score   one snapshot -> pattern judgment from the trained edge model
                    (ClikaRT when GENGUARD_ENGINE=clikart), with per-asset streak
                    so 3 consecutive changes become inspection_suggested
  GET  /api/live    latest live IMU vibration RMS read from the Qwiic gateway
  GET  /api/status  engine, model, license and network facts for the header
  GET/POST /api/control  presenter scene per asset (control.html -> index.html)

Fixed alarm rules and the trend estimate run in the browser (Woozy's engine.mjs
and trend.mjs). The model never sees the live IMU: it was trained on synthetic
data only, so the live card is shown separately.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
KGF_TO_BAR = 0.980665
# model field <- (snapshot key, factor)
FIELD_MAP = {
    "load_pct": ("load_pct", 1.0), "ambient_c": ("ambient_c", 1.0),
    "engine_rpm": ("engine_rpm", 1.0), "coolant_c": ("coolant_c", 1.0),
    "oil_bar": ("oil_kgf_cm2", KGF_TO_BAR), "voltage_v": ("voltage_ll_v", 1.0),
    "vibration_rms_g": ("vibration_rms_g", 1.0), "envelope_cv": ("envelope_cv", 1.0),
}


class Judge:
    def __init__(self, model_dir: Path):
        from genguard.model import predict, raw_score
        self.predict = predict
        self.bundle = json.loads((model_dir / "model.json").read_text())
        self.profile = json.loads((model_dir / "profile.json").read_text())
        self.persist = self.profile["model"]["persistence_windows"]
        if os.environ.get("GENGUARD_ENGINE", "numpy") == "clikart":
            from genguard.clikart_engine import ClikaScorer
            self.scorer = ClikaScorer()
            self.engine = self.scorer.label
        else:
            self.scorer, self.engine = raw_score, "numpy"
        self.streaks: dict[str, int] = {}
        self.lock = threading.Lock()

    def score(self, body: dict) -> dict:
        asset = str(body.get("asset_id", "GEN-10K-001"))
        readings = body.get("readings", {})
        if body.get("phase") != "running":
            with self.lock:
                self.streaks[asset] = 0
            return {"state": "unknown", "reason": "phase_not_running", "engine": self.engine}
        frame = {"phase": "running", "elapsed_s": float(body.get("t_s", 0)), "readings": {}}
        for key, (src, factor) in FIELD_MAP.items():
            item = readings.get(src) or {}
            value = item.get("value")
            if not isinstance(value, (int, float)) or item.get("quality", "ok") != "ok":
                with self.lock:
                    self.streaks[asset] = 0
                return {"state": "unknown", "reason": f"missing_or_stale:{src}", "engine": self.engine}
            frame["readings"][key] = {"value": value * factor}
        start = time.perf_counter()
        result = self.predict(frame, self.profile, self.bundle, self.scorer)
        result["inference_ms"] = round((time.perf_counter() - start) * 1000, 3)
        with self.lock:
            streak = self.streaks.get(asset, 0) + 1 if result["state"] == "change" else 0
            self.streaks[asset] = streak
        if result["state"] == "change":
            result["display_state"] = "inspection_suggested" if streak >= self.persist else "change_observed"
        else:
            result["display_state"] = result["state"]
        result.update(streak=streak, persistence_windows=self.persist, engine=self.engine,
                      model_id=self.bundle.get("model_id"), data_domain=self.bundle.get("data_domain"))
        return result


class LiveIMU(threading.Thread):
    """1-second vibration RMS (std of |a|, gravity removed) from the gateway JSONL."""

    def __init__(self, host="127.0.0.1", port=9877):
        super().__init__(daemon=True)
        self.addr = (host, port)
        self.latest = {"rms_g": None, "at": None, "samples": 0, "connected": False, "sensor": None}

    def run(self):
        while True:
            try:
                with socket.create_connection(self.addr, timeout=5) as s:
                    f = s.makefile()
                    self.latest["connected"] = True
                    mags, start = [], time.time()
                    for line in f:
                        msg = json.loads(line)
                        if msg.get("type") == "meta":
                            self.latest["sensor"] = msg.get("sensor")
                        if msg.get("type") != "sample":
                            continue
                        mags.append(math.sqrt(msg["ax_g"] ** 2 + msg["ay_g"] ** 2 + msg["az_g"] ** 2))
                        if time.time() - start >= 1.0 and len(mags) > 20:
                            mean = sum(mags) / len(mags)
                            rms = math.sqrt(sum((m - mean) ** 2 for m in mags) / len(mags))
                            self.latest.update(rms_g=rms, at=time.time(), samples=len(mags))
                            mags, start = [], time.time()
            except (OSError, ValueError):
                self.latest["connected"] = False
                time.sleep(2)


def network_reachable() -> bool:
    result = {"ok": False}

    def lookup():
        try:
            socket.getaddrinfo("docs.clika.io", 443)
            result["ok"] = True
        except OSError:
            pass

    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(2)
    return result["ok"]


class Control:
    """Presenter settings (control.html) that the console (index.html) follows."""

    def __init__(self, assets=("GEN-10K-001", "GEN-10K-002", "GEN-10K-003")):
        self.lock = threading.Lock()
        self.state = {"fast": False, "assets": {a: {"scenario": "normal", "ramp": False, "run": 1} for a in assets}}

    def get(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def update(self, body: dict):
        with self.lock:
            if "fast" in body:
                self.state["fast"] = bool(body["fast"])
            asset = self.state["assets"].get(body.get("asset"))
            if asset is not None:
                if "scenario" in body:
                    asset.update(scenario=str(body["scenario"]), ramp=bool(body.get("ramp", False)), run=asset["run"] + 1)
                if body.get("reset"):
                    asset["run"] += 1
            return json.loads(json.dumps(self.state))


def make_handler(judge: Judge, live: LiveIMU, control: Control):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE / "static"), **kw)

        def log_message(self, *_):
            pass

        def send_json(self, obj, code=200):
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/live":
                info = dict(live.latest)
                info["age_s"] = None if info["at"] is None else round(time.time() - info["at"], 2)
                return self.send_json(info)
            if self.path == "/api/control":
                return self.send_json(control.get())
            if self.path == "/api/status":
                lic = "env" if os.environ.get("CLIKA_RT_LICENSE") else (
                    "file" if (Path.home() / ".clika/runtime/license").exists() else "none")
                return self.send_json({"engine": judge.engine, "model_id": judge.bundle.get("model_id"),
                                       "algorithm": judge.bundle.get("algorithm"),
                                       "data_domain": judge.bundle.get("data_domain"),
                                       "license": lic, "network_reachable": network_reachable(),
                                       "host": os.uname().nodename, "machine": os.uname().machine})
            return super().do_GET()

        def do_POST(self):
            if self.path not in ("/api/score", "/api/control"):
                return self.send_json({"error": "not found"}, 404)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if self.path == "/api/control":
                    return self.send_json(control.update(body))
                return self.send_json(judge.score(body))
            except Exception as err:  # report, keep serving
                return self.send_json({"state": "unknown", "reason": f"server_error:{type(err).__name__}",
                                       "code_name": getattr(err, "code_name", ""), "message": str(err)}, 500)

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model-dir", default=str(HERE / "models" / "gen10k-208v"))
    p.add_argument("--edge-dir", default=str(HERE.parent / "genguard-edge-v1"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    args = p.parse_args()
    sys.path.insert(0, args.edge_dir)
    judge = Judge(Path(args.model_dir))
    live = LiveIMU()
    live.start()
    print(f"GenGuard console http://{args.host}:{args.port}  engine={judge.engine}", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(judge, live, Control())).serve_forever()


if __name__ == "__main__":
    main()
