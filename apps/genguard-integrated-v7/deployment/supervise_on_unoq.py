#!/usr/bin/env python3
"""One owned process group per component; systemd restarts a coherent bundle.

The API must restart with the adapter: a new IMU stream invalidates the frozen
baseline in a running API. Restarting reloads the existing baseline file and
still requires the first real packet to match its installation/configuration.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time


class StartupError(RuntimeError):
    pass


class Supervisor:
    def __init__(self, base: Path, gateway_root: Path, installation: str):
        self.base, self.app, self.gateway_root = base, base / "app", gateway_root
        self.installation = installation
        self.children: list[tuple[str, subprocess.Popen]] = []
        self.stop = threading.Event()
        self.env = dict(os.environ, PYTHONPATH="/home/arduino/clikart/site",
                        OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
                        PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        self.env["PATH"] = str(base / "runtime/node-v22.23.3-linux-arm64/bin") + ":" + os.environ.get("PATH", "")

    def validate(self):
        required = [self.app / "server.py", self.app / "deployment/configure_linked.py",
                    self.base / "config/imu-baseline-transfer.json",
                    self.base / "runtime/node-v22.23.3-linux-arm64/bin/node",
                    self.gateway_root / ".venv/bin/python",
                    self.gateway_root / "gateway/unoq_qwiic_gateway.py"]
        for path in required:
            if not path.is_file():
                raise StartupError(f"Required existing installation file is missing: {path}")
        state = self.base / "state"
        if not state.is_dir() or not os.access(state, os.W_OK):
            raise StartupError("The existing state directory must be writable; it will not be replaced.")

    @staticmethod
    def ensure_free_ports():
        for port in (9877, 9883):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise StartupError(f"Port {port} is already owned. Inspect its process before migrating; nothing was stopped.") from exc

    def spawn(self, name, command, cwd=None):
        process = subprocess.Popen(command, cwd=cwd or self.app, env=self.env,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        self.children.append((name, process))
        print(f"GenGuard: started {name} (PID {process.pid})", flush=True)
        return process

    def check_children(self):
        if self.stop.is_set():
            raise InterruptedError("service stop requested")
        for name, process in self.children:
            code = process.poll()
            if code is not None:
                raise StartupError(f"{name} exited ({code}); restart the complete owned bundle.")

    def wait_router(self, timeout=30):
        deadline = time.monotonic() + timeout
        while not Path("/var/run/arduino-router.sock").exists():
            if self.stop.wait(.5):
                raise InterruptedError("service stop requested")
            if time.monotonic() >= deadline:
                raise StartupError("Arduino Router is not ready; systemd will retry.")

    def wait_gateway(self, timeout=30):
        # Reuse the existing read-only protocol check, not a port-only success.
        # The probe imports the adapter's numerical dependencies in this parent
        # process too. Child Popen env alone does not change Python's sys.path.
        runtime_site = self.env["PYTHONPATH"]
        if runtime_site not in sys.path:
            sys.path.insert(0, runtime_site)
        sys.path.insert(0, str(self.app))
        from adapter.usb_connect import probe_gateway, USBConnectionError
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check_children()
            try:
                probe_gateway(9877, timeout=3)
                return
            except (USBConnectionError, OSError, ValueError):
                if self.stop.wait(.5):
                    raise InterruptedError("service stop requested")
        raise StartupError("No current IMU samples; systemd will retry without publishing invented values.")

    def configure_api(self):
        process = self.spawn("live configuration", [sys.executable, "deployment/configure_linked.py"])
        deadline = time.monotonic() + 40
        while process.poll() is None:
            if self.stop.wait(.2):
                raise InterruptedError("service stop requested")
            for name, child in self.children[:-1]:
                if child.poll() is not None:
                    raise StartupError(f"{name} exited during API startup.")
            if time.monotonic() >= deadline:
                raise StartupError("Live API configuration timed out.")
        if process.returncode:
            raise StartupError("Live API configuration failed.")
        self.children.remove(("live configuration", process))

    def run(self):
        self.validate()
        self.ensure_free_ports()
        self.wait_router()
        self.spawn("gateway", [str(self.gateway_root / ".venv/bin/python"),
                               str(self.gateway_root / "gateway/unoq_qwiic_gateway.py"), "--port", "9877"],
                   cwd=self.gateway_root)
        self.spawn("web and ML", [sys.executable, "server.py", "--host", "127.0.0.1", "--port", "9883",
                                  "--state-dir", str(self.base / "state"),
                                  "--imu-baseline-file", str(self.base / "config/imu-baseline-transfer.json")])
        self.configure_api()
        self.wait_gateway()
        self.spawn("IMU adapter", [sys.executable, "-m", "adapter.imu_to_api", "--installation", self.installation,
                                   "--api-url", "http://127.0.0.1:9883", "--gateway-host", "127.0.0.1",
                                   "--gateway-port", "9877"])
        print("GenGuard: local web, reviewed ML and actual IMU receiver running.", flush=True)
        while not self.stop.wait(.25):
            self.check_children()

    def cleanup(self):
        # Only Popen handles owned by this invocation; no PID files or pkill.
        for _, process in reversed(self.children):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for _, process in reversed(self.children):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path("/home/arduino/genguard-v8-20261011"))
    parser.add_argument("--gateway-root", type=Path, default=Path("/home/arduino/t4-6-vibration-lab"))
    parser.add_argument("--installation", default="phone-fixed-20261011")
    parser.add_argument("--check", action="store_true", help="Check existing paths only; do not start or stop anything.")
    args = parser.parse_args(argv)
    supervisor = Supervisor(args.base.resolve(), args.gateway_root.resolve(), args.installation)
    if args.check:
        supervisor.validate()
        print("Existing files and writable state directory verified; no process was changed.")
        return 0
    supervisor.validate()
    # A service/manual duplicate fails before it can own any child process.
    with (supervisor.base / "state/supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another GenGuard supervisor owns this installation.", file=sys.stderr)
            return 1
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: supervisor.stop.set())
        try:
            supervisor.run()
            return 0
        except InterruptedError:
            return 0
        except (StartupError, OSError) as exc:
            print(f"GenGuard: {exc}", file=sys.stderr, flush=True)
            return 1
        finally:
            supervisor.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
