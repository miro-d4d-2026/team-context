"""IMU feature extraction reused from the existing, tested GenGuard edge adapter.

Gateway input is the pinned team protocol, not an invented sensor serial format.
The receiving transport determines source provenance; this module only calculates.
"""
from __future__ import annotations
import math
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
