"""Use the board's existing gateway over USB. Never flash or replace firmware."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time

from .imu_to_api import main as run_adapter


class USBConnectionError(RuntimeError):
    pass


def adb_path(explicit=None):
    candidates = [explicit, os.environ.get("GENGUARD_ADB"), shutil.which("adb"),
                  "/private/tmp/genguard-adb-20261011/platform-tools/adb"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise USBConnectionError("ADB를 찾지 못했습니다. USB_CONNECTION.md의 설치 안내를 확인하세요.")


def command(args, *, timeout=10):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Do not include arbitrary command output or credentials in diagnostic logs.
        raise USBConnectionError("USB 명령이 실패했습니다. USB 연결과 ADB 기기 상태를 확인하세요.")
    return result.stdout


def select_device(device_listing, requested=None):
    devices = []
    for line in device_listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            devices.append(fields[0])
    if requested:
        if requested not in devices:
            raise USBConnectionError("지정한 USB 기기가 연결되어 있지 않습니다.")
        return requested
    if len(devices) != 1:
        raise USBConnectionError("연결된 ADB 기기가 1개여야 합니다. 여러 기기이면 --serial로 UNO Q를 지정하세요.")
    return devices[0]


def forward_exists(listing, serial, local_port, remote_port):
    local, remote = f"tcp:{local_port}", f"tcp:{remote_port}"
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] == local:
            if fields == [serial, local, remote]:
                return True
            raise USBConnectionError("요청한 USB 터널 포트를 다른 연결이 사용 중입니다. --local-port를 바꾸세요.")
    return False


# Runs only the already installed receiver. Files and MCU firmware remain intact.
GATEWAY_START = r'''
import json, pathlib, socket, subprocess, time
root = pathlib.Path('/home/arduino/t4-6-vibration-lab')
if not pathlib.Path('/var/run/arduino-router.sock').exists():
    raise SystemExit('UNO Q Arduino Router is unavailable')
def listening():
    try:
        with socket.create_connection(('127.0.0.1', 9877), timeout=0.5):
            return True
    except OSError:
        return False
if listening():
    print(json.dumps({'gateway': 'already_running'}))
else:
    python = root / '.venv/bin/python'
    gateway = root / 'gateway/unoq_qwiic_gateway.py'
    if not python.is_file() or not gateway.is_file():
        raise SystemExit('Existing team gateway is not installed; firmware was not changed')
    with open('/tmp/genguard-v7-gateway.log', 'ab') as output:
        process = subprocess.Popen([str(python), str(gateway), '--port', '9877'],
            cwd=root, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(20):
        if listening():
            print(json.dumps({'gateway': 'started', 'pid': process.pid}))
            break
        time.sleep(0.2)
    else:
        raise SystemExit('Gateway did not start; inspect /tmp/genguard-v7-gateway.log')
'''


def probe_gateway(port, timeout=8):
    """Require metadata, a ready gateway and at least one actual sample."""
    meta = status = sample = None
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.settimeout(1)
        pending = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                break
            pending += chunk
            if len(pending) > 65536:
                raise USBConnectionError("센서 수집기 응답 형식을 확인하세요.")
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                row = json.loads(line)
                if row.get("type") == "meta":
                    meta = row
                elif row.get("type") == "gateway":
                    status = row
                elif row.get("type") == "sample":
                    sample = row
                if (meta and status and sample and status.get("sensor_ready")
                        and status.get("router_connected")):
                    return {"sensor": meta.get("sensor"), "sample_rate_hz": meta.get("odr_hz"),
                            "accel_range_g": meta.get("accel_range_g"), "source": "live_imu"}
    raise USBConnectionError("USB는 연결됐지만 실제 IMU 샘플이 없습니다. Qwiic 센서 연결을 확인하세요.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="UNO Q USB → existing gateway → GenGuard")
    parser.add_argument("--adb")
    parser.add_argument("--serial")
    parser.add_argument("--local-port", type=int, default=19877)
    parser.add_argument("--api-url", default="http://127.0.0.1:9883")
    parser.add_argument("--installation", required=True)
    parser.add_argument("--check-only", action="store_true", help="Connect and check; do not publish windows")
    args = parser.parse_args(argv)
    if not 1024 <= args.local_port <= 65535:
        parser.error("--local-port must be between 1024 and 65535")
    owned_forward = False
    adb = serial = None
    try:
        adb = adb_path(args.adb)
        serial = select_device(command([adb, "devices"]), args.serial)
        base = [adb, "-s", serial]
        command(base + ["shell", "python3 -c " + shlex.quote(GATEWAY_START)])
        existing = forward_exists(command([adb, "forward", "--list"]), serial, args.local_port, 9877)
        if not existing:
            command(base + ["forward", "--no-rebind", f"tcp:{args.local_port}", "tcp:9877"])
            owned_forward = True
        result = probe_gateway(args.local_port)
        print(json.dumps({"state": "sensor_connected", **result}, ensure_ascii=False), flush=True)
        if args.check_only:
            return 0
        return run_adapter(["--gateway-host", "127.0.0.1", "--gateway-port", str(args.local_port),
                            "--api-url", args.api_url, "--installation", args.installation])
    except KeyboardInterrupt:
        return 0
    except (USBConnectionError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        print(json.dumps({"state": "connection_failed", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 2
    finally:
        if owned_forward:
            # Only remove this invocation's tunnel. Never stop a shared gateway or ADB daemon.
            try:
                command([adb, "-s", serial, "forward", "--remove", f"tcp:{args.local_port}"])
            except (USBConnectionError, OSError, subprocess.TimeoutExpired):
                pass


if __name__ == "__main__":
    raise SystemExit(main())
