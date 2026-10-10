"""Read the existing UNO Q gateway on the board; never infer on the PC by default.

This adapter is independent of the Arduino Bridge package: the existing gateway
owns the MCU connection. Training must run as a separate, after-run operation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import time
from typing import Any

import numpy as np

UINT32 = 2**32
WINDOW_US = 2_000_000
AXES = ("ax_g", "ay_g", "az_g")
PHASES = {"starting", "warming_up", "running", "stopped"}
SOURCES = {"live_imu", "live_controller", "recorded_imu", "public_replay", "scenario"}


class StreamSessionError(ValueError):
    """The current stream cannot safely continue under the same session ID."""


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _uint32(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value < UINT32:
        raise StreamSessionError(f"invalid {name}: expected uint32")
    return value


def feature_values(times_s: list[float], axes_g: list[list[float]],
                   window_start_s: float) -> tuple[float, float]:
    """Exact v1 features, including per-bin rather than global demeaning for CV."""
    samples = np.asarray(axes_g, dtype=float)
    times = np.asarray(times_s, dtype=float)
    if samples.ndim != 2 or samples.shape != (len(times), 3) or len(times) < 2:
        raise ValueError("need matching time and three-axis samples")
    if not np.isfinite(samples).all() or not np.isfinite(times).all():
        raise ValueError("non-finite samples")
    centered = samples - samples.mean(axis=0)
    rms = float(np.sqrt(np.mean(np.sum(centered * centered, axis=1))))
    # All windows are aligned to the first MCU sample, not packet arrival times.
    bins = np.floor((times - window_start_s) / 0.1 + 1e-10).astype(int)
    envelope = []
    for index in range(20):
        chunk = samples[bins == index]
        if len(chunk) < 5:
            raise ValueError("fewer than five samples in a 100 ms bin")
        envelope.append(float(np.sqrt(np.var(chunk, axis=0, ddof=0).sum())))
    values = np.asarray(envelope)
    return rms, float(np.std(values, ddof=0) / max(float(values.mean()), 1e-9))


class WindowBuilder:
    """Bounded two-second windows with no automatic restart or reconnection."""

    def __init__(self, asset_id: str, session_id: str, installation_id: str):
        self.asset_id = asset_id
        self.session_id = session_id
        self.installation_id = installation_id
        self.meta: tuple[float, float] | None = None
        self.locked_meta: tuple[float, float] | None = None
        self.gateway_counts: dict[str, int] = {}
        self.last_seq: int | None = None
        self.last_us: int | None = None
        self.elapsed_us = 0
        self.window_start_us = 0
        self.window_seq = 0
        self.samples: list[list[float]] = []
        self.times: list[float] = []
        self.reasons: set[str] = set()
        self.last_quality_reasons: list[str] = []

    def set_meta(self, packet: dict) -> None:
        odr, full_scale = packet.get("odr_hz"), packet.get("accel_range_g")
        valid = (_number(odr) and 1 <= odr <= 10000
                 and _number(full_scale) and full_scale in (2, 4, 8, 16))
        if not valid:
            self.reasons.add("missing_or_invalid_imu_metadata")
            self.meta = None
            return
        candidate = (float(odr), float(full_scale))
        if (self.locked_meta is not None and self.locked_meta != candidate
                and self.last_seq is not None):
            raise StreamSessionError("IMU configuration changed; start a new session")
        self.meta = candidate
        self.locked_meta = candidate

    def gateway_status(self, packet: dict) -> None:
        if packet.get("router_connected") is False or packet.get("sensor_ready") is False:
            raise StreamSessionError("gateway/IMU not ready; start a new session")
        for field in ("ring_overflow_count", "read_error_count"):
            if field not in packet:
                continue
            count = _uint32(packet[field], field)
            previous = self.gateway_counts.get(field)
            if previous is not None and count < previous:
                raise StreamSessionError("MCU error counter reset; start a new session")
            if previous is not None and count > previous:
                self.reasons.add(field + "_increased")
            self.gateway_counts[field] = count

    def add_sample(self, packet: dict) -> list[dict]:
        seq = _uint32(packet.get("seq"), "seq")
        stamp = _uint32(packet.get("t_us"), "t_us")
        values = [packet.get(axis) for axis in AXES]
        if any(type(value) not in (int, float) for value in values):
            raise StreamSessionError("sample has missing or non-numeric acceleration")
        if self.last_seq is not None:
            delta_seq = (seq - self.last_seq) % UINT32
            delta_us = (stamp - self.last_us) % UINT32
            if delta_seq == 0 or delta_seq > UINT32 // 2:
                raise StreamSessionError("duplicate or reset sequence; start a new session")
            if delta_us == 0 or delta_us > 10_000_000:
                raise StreamSessionError("MCU clock reset or long gap; start a new session")
            if delta_seq != 1:
                self.reasons.add("missing_sample_sequence")
            if self.meta:
                expected_us = 1_000_000 / self.meta[0]
                if not 0.5 * expected_us <= delta_us <= 1.5 * expected_us:
                    self.reasons.add("irregular_mcu_timing")
            self.elapsed_us += delta_us
        self.last_seq, self.last_us = seq, stamp
        ready = []
        if self.elapsed_us >= self.window_start_us + WINDOW_US:
            ready.append(self._finish_window())
            self.window_start_us += WINDOW_US
            if self.elapsed_us >= self.window_start_us + WINDOW_US:
                raise StreamSessionError("multiple missing windows; start a new session")
        if self.meta is None:
            self.reasons.add("missing_or_invalid_imu_metadata")
        elif any(not math.isfinite(value) or abs(value) >= self.meta[1] * 0.98
                 for value in values):
            self.reasons.add("non_finite_or_saturated_acceleration")
        self.times.append(self.elapsed_us / 1_000_000)
        self.samples.append(values)
        # Prevent an untrusted stream from exhausting RAM by freezing its clock.
        if len(self.samples) > 25_000:
            raise StreamSessionError("oversized IMU window")
        return ready

    def _finish_window(self) -> dict:
        if self.meta is None:
            self.reasons.add("missing_or_invalid_imu_metadata")
        else:
            expected = self.meta[0] * 2
            if not 0.8 * expected <= len(self.samples) <= 1.2 * expected:
                self.reasons.add("unexpected_sample_count")
        if len(self.times) < 2 or self.times[-1] - self.times[0] < 1.8:
            self.reasons.add("incomplete_time_coverage")
        rms = cv = None
        if not self.reasons:
            try:
                rms, cv = feature_values(self.times, self.samples,
                                         self.window_start_us / 1_000_000)
            except ValueError:
                self.reasons.add("invalid_feature_window")
        self.last_quality_reasons = sorted(self.reasons)
        quality = "invalid" if self.reasons else "ok"
        self.window_seq += 1
        frame = {
            "schema_version": "genguard.edge.window/1", "asset_id": self.asset_id,
            "session_id": self.session_id, "installation_id": self.installation_id,
            "seq": self.window_seq,
            "elapsed_s": (self.window_start_us + WINDOW_US) / 1_000_000,
            "duration_s": 2.0, "phase": "running", "measured_at": None,
            "readings": {
                "vibration_rms_g": {"value": rms, "unit": "g", "source": "live_imu",
                                    "age_s": 0.0, "quality": quality},
                "envelope_cv": {"value": cv, "unit": "1", "source": "live_imu",
                                "age_s": 0.0, "quality": quality},
            },
        }
        self.samples, self.times, self.reasons = [], [], set()
        return frame


def read_controller_snapshot(path: str | Path, now: float,
                             max_age_s: float | None = None) -> tuple[str, dict]:
    """Preserve units/source; add file-clock age, never relabel replay as live."""
    raw = Path(path).read_bytes()
    if len(raw) > 1_048_576:
        raise ValueError("controller snapshot is too large")
    snapshot = json.loads(raw)
    if not isinstance(snapshot, dict) or snapshot.get("phase") not in PHASES:
        raise ValueError("snapshot requires a valid phase")
    stamp = snapshot.get("monotonic_s")
    if not _number(stamp) or not isinstance(snapshot.get("readings"), dict):
        raise ValueError("snapshot requires board monotonic_s and readings")
    elapsed = now - stamp
    clock_invalid = elapsed < -0.05
    readings = {}
    for name, reading in snapshot["readings"].items():
        if name in ("vibration_rms_g", "envelope_cv"):
            continue  # IMU features always come from the real gateway.
        if not isinstance(reading, dict):
            raise ValueError(f"invalid controller reading: {name}")
        value = reading.get("value")
        unit, source = reading.get("unit"), reading.get("source")
        if not isinstance(unit, str) or source not in SOURCES:
            raise ValueError(f"reading {name} requires explicit unit and source")
        quality = reading.get("quality")
        if quality not in ("ok", "missing", "invalid"):
            raise ValueError(f"reading {name} requires explicit quality")
        base_age = reading.get("age_s", 0.0)
        age_invalid = not _number(base_age) or base_age < 0
        age = max(0.0, elapsed) + (0.0 if age_invalid else base_age)
        if clock_invalid or age_invalid or (max_age_s is not None and age > max_age_s):
            quality = "invalid"
        if value is not None and not _number(value):
            value, quality = None, "invalid"
        readings[name] = {"value": value, "unit": unit, "source": source,
                          "quality": quality, "age_s": age}
    return snapshot["phase"], readings


def _log(state: str, message: str, **extra: Any) -> None:
    print(json.dumps({"type": "stream_status", "state": state, "message": message,
                      **extra}, ensure_ascii=False, allow_nan=False), file=sys.stderr,
          flush=True)


def run_stream(args: argparse.Namespace, runtime: Any, asset_id: str) -> None:
    """Runtime dependency is injectable for CPU-only transport tests."""
    builder = WindowBuilder(asset_id, args.session, args.installation)
    # Refuse to guess an initial engine phase. The snapshot producer runs on UNO Q.
    read_controller_snapshot(args.controller_snapshot, time.monotonic(),
                             args.max_controller_age_s)
    with socket.create_connection((args.host, args.port), timeout=3) as connection:
        connection.settimeout(0.5)
        pending = b""
        last_sample_at = time.monotonic()
        while True:
            try:
                chunk = connection.recv(8192)
            except socket.timeout:
                chunk = None
            if chunk == b"":
                raise StreamSessionError("gateway disconnected; start a new session")
            if time.monotonic() - last_sample_at > args.sensor_timeout_s:
                raise StreamSessionError("gateway samples stale; start a new session")
            if chunk is None:
                continue
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if len(line) > 4096:
                    raise StreamSessionError("oversized gateway packet")
                packet = json.loads(line)
                if not isinstance(packet, dict):
                    raise StreamSessionError("gateway packet must be an object")
                kind = packet.get("type")
                if kind == "meta":
                    builder.set_meta(packet)
                elif kind == "error":
                    raise StreamSessionError("gateway reported sensor/MCU error")
                elif kind == "gateway":
                    builder.gateway_status(packet)
                elif kind == "sample":
                    last_sample_at = time.monotonic()
                    for frame in builder.add_sample(packet):
                        phase, readings = read_controller_snapshot(
                            args.controller_snapshot, time.monotonic(), args.max_controller_age_s)
                        frame["phase"] = phase
                        frame["readings"] = {**readings, **frame["readings"]}
                        if builder.last_quality_reasons:
                            _log("unknown", "invalid IMU window", seq=frame["seq"],
                                 reasons=builder.last_quality_reasons)
                        result = runtime.ingest(frame)
                        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
                else:
                    raise StreamSessionError("unknown gateway packet type")
            if len(pending) > 4096:
                raise StreamSessionError("incomplete oversized gateway packet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UNO Q local IMU + controller stream")
    parser.add_argument("--db", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--installation", required=True)
    parser.add_argument("--controller-snapshot", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--max-controller-age-s", type=float,
                        help="optional stricter age cap; profile per-field limits always apply")
    parser.add_argument("--sensor-timeout-s", type=float, default=3.0)
    args = parser.parse_args(argv)
    if ((args.max_controller_age_s is not None and
         (not math.isfinite(args.max_controller_age_s) or args.max_controller_age_s <= 0))
            or not math.isfinite(args.sensor_timeout_s) or args.sensor_timeout_s <= 0):
        parser.error("age and timeout must be finite positive seconds")
    from .core import EdgeRuntime
    runtime = None
    started = False
    previous_handler = signal.getsignal(signal.SIGTERM)

    def terminate(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
        runtime = EdgeRuntime(args.db, args.profile)
        runtime.start_session(args.session, args.installation)
        started = True
        _log("starting", "run this process on UNO Q Linux; device execution not auto-verified")
        run_stream(args, runtime, profile["asset_id"])
        return 0
    except KeyboardInterrupt:
        _log("stopped", "session closed by operator; partial window discarded")
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        _log("unknown", str(exc), action="fix source and start with a new session ID")
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if runtime is not None:
            try:
                if started:
                    runtime.close_session(args.session)
            finally:
                runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
