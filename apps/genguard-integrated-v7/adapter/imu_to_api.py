#!/usr/bin/env python3
"""Subscribe to the existing UNO Q JSONL gateway; publish real IMU windows.

Run on UNO Q Linux: python -m adapter.imu_to_api --installation phone-mount-001
CSV replay is a separate, explicitly marked --csv mode. It is never labeled live.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

from .features import AXES, StreamSessionError, WindowBuilder


def utc_text(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log(state: str, **fields) -> None:
    print(json.dumps({"state": state, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a board API token to a redirected host.


class Publisher:
    def __init__(self, base_url: str, token: str = "", timeout_s: float = 3.0):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("--api-url must be an http(s) origin, without a token or path")
        if token and (len(token) > 512 or any(not 33 <= ord(character) <= 126 for character in token)):
            # Avoid urllib's invalid-header exception, which can include the secret value.
            raise ValueError("GENGUARD_API_TOKEN must contain printable ASCII without whitespace")
        self.url = base_url.rstrip("/") + "/api/imu/windows"
        self.token, self.timeout_s = token, timeout_s
        self.opener = build_opener(NoRedirects())

    def publish(self, packet: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(self.url, data=json.dumps(packet, allow_nan=False).encode(),
                          headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise StreamSessionError("API response exceeds 64 KiB")
                result = json.loads(raw)
        except HTTPError as exc:
            # Do not echo response bodies containing user data or authentication input.
            raise StreamSessionError(f"API rejected IMU window (HTTP {exc.code}); inspect server log and restart adapter") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise StreamSessionError("API did not confirm IMU window; adapter stopped without replaying a backlog") from exc
        if not isinstance(result, dict) or result.get("accepted") is not True:
            raise StreamSessionError("API response did not confirm acceptance")
        return result


class FeatureWindows(WindowBuilder):
    """Keep the original features and quality policy; add transport diagnostics."""

    def __init__(self, installation: str, stream_id: str):
        super().__init__("GEN-DEMO", stream_id, installation)
        self.completed_diagnostics = {}

    def _finish_window(self) -> dict:
        count = len(self.samples)
        span = self.times[-1] - self.times[0] if len(self.times) > 1 else 0.0
        self.completed_diagnostics = {
            "sample_count": count,
            "sample_rate_hz": (count - 1) / span if span > 0 else 0.0,
            "configured_sample_rate_hz": self.meta[0] if self.meta else 0.0,
            "accel_range_g": self.meta[1] if self.meta else 4.0,
        }
        return super()._finish_window()

    def as_packet(self, frame: dict, *, source: str, measured_at: float) -> dict:
        if source not in ("live_imu", "recorded_imu"):
            raise ValueError("source must identify a live gateway or a recording")
        readings = frame["readings"]
        return {
            "schema_version": "genguard.imu.window/1",
            "source": source,
            "stream_id": self.session_id,
            "installation_id": self.installation_id,
            "window_seq": frame["seq"],
            "duration_s": 2.0,
            **self.completed_diagnostics,
            "measured_at": utc_text(measured_at),
            "features": {key: readings[key]["value"]
                         for key in ("vibration_rms_g", "envelope_cv")},
            "quality": {"valid": not self.last_quality_reasons,
                        "flags": list(self.last_quality_reasons)},
        }


def publish_frame(publisher, builder, frame, source, measured_at):
    packet = builder.as_packet(frame, source=source, measured_at=measured_at)
    result = publisher.publish(packet)
    log("accepted", source=source, stream_id=packet["stream_id"],
        window_seq=packet["window_seq"], quality=packet["quality"],
        duplicate=bool(result.get("duplicate", False)))


def run_live(args, publisher) -> None:
    """Stop on disconnect/reset. A new invocation uses a new stream and baseline."""
    stream_id = "imu-" + uuid.uuid4().hex
    builder = FeatureWindows(args.installation, stream_id)
    pending = b""
    first_sample_wall = None
    with socket.create_connection((args.gateway_host, args.gateway_port), timeout=3) as connection:
        connection.settimeout(0.5)
        last_sample_at = time.monotonic()
        log("connected", source="live_imu", stream_id=stream_id,
            gateway=f"{args.gateway_host}:{args.gateway_port}")
        while True:
            try:
                chunk = connection.recv(8192)
            except socket.timeout:
                chunk = None
            if chunk == b"":
                raise StreamSessionError("gateway disconnected; restart after checking sensor and gateway")
            if time.monotonic() - last_sample_at > args.sensor_timeout_s:
                raise StreamSessionError(f"no fresh IMU samples for {args.sensor_timeout_s:g} seconds; incomplete window discarded")
            if chunk is None:
                continue
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if len(line) > 4096:
                    raise StreamSessionError("gateway packet exceeds 4096 bytes")
                try:
                    packet = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise StreamSessionError("gateway sent invalid JSON") from exc
                if not isinstance(packet, dict):
                    raise StreamSessionError("gateway packet must be an object")
                kind = packet.get("type")
                if kind == "meta":
                    builder.set_meta(packet)
                    if builder.meta is None:
                        raise StreamSessionError("gateway IMU rate/range metadata missing or invalid")
                elif kind == "gateway":
                    builder.gateway_status(packet)
                elif kind == "error":
                    raise StreamSessionError("gateway reported an IMU/MCU error")
                elif kind == "sample":
                    if builder.meta is None:
                        raise StreamSessionError("sample arrived before valid IMU rate/range metadata")
                    last_sample_at = time.monotonic()
                    if first_sample_wall is None:
                        first_sample_wall = time.time()
                    for frame in builder.add_sample(packet):
                        # MCU-relative time keeps slow HTTP/TCP backlogs from becoming fresh again.
                        ended_at = first_sample_wall + frame["elapsed_s"]
                        if time.time() - ended_at > 3.0:
                            raise StreamSessionError("IMU stream is delayed; discard buffered history and reconnect")
                        publish_frame(publisher, builder, frame, "live_imu", ended_at)
                else:
                    raise StreamSessionError("unknown gateway packet type")
            if len(pending) > 4096:
                raise StreamSessionError("incomplete oversized gateway packet")


def csv_samples(path: Path):
    """Read the user's verified CSV columns; reject silently guessed field units."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"seq", "t_us", *AXES}.issubset(reader.fieldnames or []):
            raise ValueError("CSV requires seq,t_us,ax_g,ay_g,az_g; accelerations must already be in g")
        for row_number, row in enumerate(reader, start=2):
            try:
                yield {"type": "sample", "seq": int(row["seq"]), "t_us": int(row["t_us"]),
                       **{axis: float(row[axis]) for axis in AXES}}
            except (ValueError, TypeError) as exc:
                raise ValueError(f"CSV row {row_number} contains invalid samples") from exc


def run_replay(args, publisher, *, clock=time.monotonic, sleeper=time.sleep,
               wall_clock=time.time) -> None:
    """Wall-paced recording replay: always recorded_imu, never a live measurement."""
    stream_id = "replay-" + uuid.uuid4().hex
    builder = FeatureWindows(args.installation, stream_id)
    builder.set_meta({"odr_hz": args.recorded_odr_hz, "accel_range_g": args.recorded_range_g})
    if builder.meta is None:
        raise ValueError("recorded ODR/range must be specified correctly")
    next_window_at = clock() + 2.0
    log("replay_started", source="recorded_imu", stream_id=stream_id,
        notice="Recorded data, paced every 2 seconds; no live sensor attached by this mode")
    for sample in csv_samples(Path(args.csv)):
        for frame in builder.add_sample(sample):
            sleeper(max(0.0, next_window_at - clock()))
            publish_frame(publisher, builder, frame, "recorded_imu", wall_clock())
            # If sending ran slowly, do not catch up by bursting old windows.
            next_window_at = clock() + 2.0
    log("replay_finished", source="recorded_imu", complete_windows=builder.window_seq,
        incomplete_window_discarded=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Existing UNO Q IMU gateway → GenGuard hybrid API")
    parser.add_argument("--api-url", default="http://127.0.0.1:9879")
    parser.add_argument("--installation", required=True,
                        help="Stable mounting ID; change it after moving/remounting the IMU")
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=9877)
    parser.add_argument("--sensor-timeout-s", type=float, default=3.0)
    parser.add_argument("--csv", help="Optional recorded CSV replay, explicitly marked recorded_imu")
    parser.add_argument("--recorded-odr-hz", type=float, default=208.0)
    parser.add_argument("--recorded-range-g", type=float, choices=(2, 4, 8, 16), default=4.0)
    args = parser.parse_args(argv)
    if not args.installation or len(args.installation) > 100:
        parser.error("--installation must be a nonempty mounting identifier up to 100 characters")
    if not math.isfinite(args.sensor_timeout_s) or not 0.5 <= args.sensor_timeout_s <= 5.0:
        parser.error("--sensor-timeout-s must be between 0.5 and 5")
    try:
        publisher = Publisher(args.api_url, os.environ.get("GENGUARD_API_TOKEN", ""))
        if args.csv:
            run_replay(args, publisher)
        else:
            run_live(args, publisher)
    except KeyboardInterrupt:
        log("stopped", message="User stopped adapter; no buffered samples retried")
        return 0
    except (StreamSessionError, ValueError, OSError) as exc:
        log("stopped_unknown", message=str(exc), incomplete_window_discarded=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
