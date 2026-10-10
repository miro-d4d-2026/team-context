"""Local lifecycle tests. Never access a board, sensor, production DB or service."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from deployment.supervise_on_unoq import Supervisor, StartupError


ROOT = Path(__file__).parent


class AutostartTests(unittest.TestCase):
    def make_supervisor(self):
        return Supervisor(Path("/tmp/genguard-test"), Path("/tmp/existing-gateway"), "test-mount")

    def test_sensor_exit_requires_whole_bundle_restart_even_with_zero_status(self):
        supervisor = self.make_supervisor()
        supervisor.children = [("gateway", Mock(poll=lambda: None)),
                               ("web and ML", Mock(poll=lambda: None)),
                               ("IMU adapter", Mock(poll=lambda: 0))]
        with self.assertRaisesRegex(StartupError, "IMU adapter exited"):
            supervisor.check_children()

    def test_gateway_exit_is_not_silently_reused(self):
        supervisor = self.make_supervisor()
        supervisor.children = [("gateway", Mock(poll=lambda: 1))]
        with self.assertRaisesRegex(StartupError, "gateway exited"):
            supervisor.check_children()

    def test_stop_is_not_reported_as_new_sensor_failure(self):
        supervisor = self.make_supervisor()
        supervisor.stop.set()
        with self.assertRaises(InterruptedError):
            supervisor.check_children()

    def test_port_collision_does_not_touch_existing_process(self):
        with patch("deployment.supervise_on_unoq.socket.socket") as socket_type:
            socket_type.return_value.__enter__.return_value.bind.side_effect = OSError("in use")
            with self.assertRaisesRegex(StartupError, "nothing was stopped"):
                Supervisor.ensure_free_ports()

    def test_launch_order_reloads_baseline_and_limits_api_to_loopback(self):
        supervisor = self.make_supervisor()
        launches = []
        supervisor.validate = Mock()
        supervisor.ensure_free_ports = Mock()
        supervisor.wait_router = Mock()
        supervisor.configure_api = Mock()
        supervisor.wait_gateway = Mock()

        def capture(name, command, cwd=None):
            launches.append((name, command))
            if name == "IMU adapter":
                supervisor.stop.set()
        supervisor.spawn = capture
        supervisor.run()
        self.assertEqual([row[0] for row in launches], ["gateway", "web and ML", "IMU adapter"])
        server = launches[1][1]
        self.assertEqual(server[server.index("--host") + 1], "127.0.0.1")
        self.assertEqual(server[server.index("--imu-baseline-file") + 1],
                         "/tmp/genguard-test/config/imu-baseline-transfer.json")
        self.assertNotIn("--csv", launches[2][1])

    def test_child_processes_are_separate_owned_groups(self):
        supervisor = self.make_supervisor()
        with patch("deployment.supervise_on_unoq.subprocess.Popen") as popen:
            supervisor.spawn("one", ["python3", "unused.py"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_gateway_probe_parent_receives_existing_numerical_runtime_path(self):
        supervisor = self.make_supervisor()
        module = types.ModuleType("adapter.usb_connect")
        module.USBConnectionError = RuntimeError

        def probe(port, timeout):
            self.assertIn("/home/arduino/clikart/site", sys.path)
            self.assertIn(str(supervisor.app), sys.path)
            self.assertEqual(port, 9877)
        module.probe_gateway = Mock(side_effect=probe)
        with patch.object(sys, "path", []), patch.dict(sys.modules, {"adapter.usb_connect": module}):
            supervisor.wait_gateway()
        module.probe_gateway.assert_called_once()

    def test_cleanup_only_terminates_handles_owned_by_invocation(self):
        supervisor = self.make_supervisor()
        owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        try:
            supervisor.children = [("owned", owned)]
            supervisor.cleanup()
            self.assertIsNotNone(owned.poll())
            self.assertIsNone(unrelated.poll())
        finally:
            if owned.poll() is None:
                owned.kill(); owned.wait()
            unrelated.terminate(); unrelated.wait(timeout=2)

    def test_missing_installation_never_creates_replacement_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            supervisor = Supervisor(base, base / "gateway", "test-mount")
            with self.assertRaises(StartupError):
                supervisor.validate()
            self.assertFalse((base / "state").exists())

    def test_unit_restarts_as_user_and_keeps_logs_in_journal(self):
        unit = (ROOT / "genguard.service.in").read_text()
        for expected in ("WantedBy=default.target", "Restart=on-failure", "RestartSec=5",
                         "KillMode=control-group", "StandardOutput=journal", "LogRateLimitBurst=200"):
            self.assertIn(expected, unit)
        self.assertNotIn("User=root", unit)
        self.assertNotIn("0.0.0.0", unit)
        self.assertIn("Environment=PYTHONPATH=/home/arduino/clikart/site", unit)

    def test_installer_requires_linger_and_does_not_replace_foreign_units(self):
        script = (ROOT / "install-autostart.sh").read_text()
        self.assertIn("loginctl show-user arduino -p Linger --value", script)
        self.assertIn("An unrelated genguard.service already exists", script)
        self.assertIn("The managed unit belongs to another installation", script)
        self.assertIn("systemd-analyze --user verify", script)
        self.assertNotIn("pkill", script)
        self.assertNotIn("rm -rf", script)


if __name__ == "__main__":
    unittest.main()
