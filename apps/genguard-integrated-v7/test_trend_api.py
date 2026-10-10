"""Trend extension integration tests; local HTTP, never physical board proof."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from test_api import FakeClock, running_server, request
from trend_service import TrendBridgeService


class TrendAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="genguard-trend-api-")
        self.clock = FakeClock()
        self.patch = patch("test_api.HybridBridgeService", TrendBridgeService)
        self.patch.start()
        self.server = running_server(self.clock, self.tmp.name)
        self.base = self.server.__enter__()
        status, config, _ = request(self.base, "/api/config")
        self.assertEqual(status, 200)
        self.values = config["default_values"]
        status, result, _ = request(self.base, "/api/sessions", {"request_id": "trend-test"})
        self.assertEqual(status, 200)
        self.sid, self.seq = result["session_id"], 0

    def tearDown(self):
        self.server.__exit__(None, None, None)
        self.patch.stop()
        self.tmp.cleanup()

    def send(self, values=None, phase="running", quality=None):
        self.seq += 1
        body = {"seq": self.seq, "source": "hybrid_demo", "phase": phase,
                "values": values or copy.deepcopy(self.values), "quality": quality or {}}
        status, result, _ = request(self.base, f"/api/sessions/{self.sid}/samples", body)
        self.assertEqual(status, 200, result)
        return body, result

    def test_catalog_is_authenticated_and_explicitly_mock(self):
        status, result, _ = request(self.base, "/api/trend/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(result["policy"]["source"], "scenario")
        self.assertEqual(result["policy"]["horizon_s"], 120)
        self.assertTrue(result["scenarios"])
        self.assertEqual(request(self.base, "/api/trend/catalog", token=None)[0], 401)

    def test_experiment_cannot_advance_live_time_or_fake_imu(self):
        _, before = self.send()
        _, catalog, _ = request(self.base, "/api/trend/catalog")
        scenario = catalog["scenarios"][0]["id"]
        status, experiment, _ = request(self.base, "/api/trend/experiment", {"scenario_id": scenario})
        self.assertEqual(status, 200, experiment)
        self.assertEqual(experiment["source"], "scenario")
        self.assertEqual(experiment["time_mode"], "simulated")
        self.assertTrue(experiment["rows"])
        self.assertEqual(experiment["rows"][0]["trend"]["source"], "scenario")
        _, after = self.send()
        self.assertEqual(after["elapsed_s"], before["elapsed_s"])
        self.assertEqual(after["imu"]["state"], "waiting")
        self.assertEqual(after["trend"]["history_count"], before["trend"]["history_count"])

    def test_experiment_rejects_arbitrary_payload_and_requires_auth(self):
        for body in ({}, {"scenario_id": "../other"}, {"scenario_id": []},
                     {"scenario_id": "normal", "seed": 1}):
            self.assertEqual(request(self.base, "/api/trend/experiment", body)[0], 422)
        self.assertEqual(request(self.base, "/api/trend/experiment", {}, token=None)[0], 401)

    def test_duplicate_is_historical_and_does_not_advance_trend(self):
        body, first = self.send()
        self.clock.advance(20)
        status, duplicate, _ = request(self.base, f"/api/sessions/{self.sid}/samples", body)
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["trend"], first["trend"])
        self.assertGreaterEqual(duplicate["analysis_age_s"], 20)

    def test_rapid_clicks_cannot_manufacture_history(self):
        _, first = self.send()
        for _ in range(8):
            self.clock.advance(1)
            _, result = self.send()
        self.assertEqual(result["trend"]["history_count"], first["trend"]["history_count"])
        self.assertFalse(result["trend"]["advisory"])

    def test_missing_input_clears_trend_without_replacing_existing_urgent(self):
        values = dict(self.values, coolant_c=100)
        values.pop("oil_kgf_cm2")
        _, result = self.send(values)
        self.assertEqual(result["status"], "urgent")
        self.assertFalse(result["trend"]["advisory"])
        self.assertEqual(result["trend"]["state"], "unavailable")

    def test_closed_session_starts_new_trend_history(self):
        for _ in range(4):
            self.send()
            self.clock.advance(10)
        self.assertEqual(request(self.base, f"/api/sessions/{self.sid}/close", {})[0], 200)
        status, result, _ = request(self.base, "/api/sessions", {"request_id": "next-trend-test"})
        self.assertEqual(status, 200)
        self.sid, self.seq = result["session_id"], 0
        _, result = self.send()
        self.assertLessEqual(result["trend"]["history_count"], 1)
        self.assertFalse(result["trend"]["advisory"])

    def test_static_module_is_served(self):
        status, code, headers = request(self.base, "/trend.mjs", token=None)
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("export", code)

    def test_optional_module_failure_keeps_base_and_retry_consistent(self):
        with patch("trend_service.TrendMonitor", side_effect=ValueError("test model error")):
            body, result = self.send()
        self.assertEqual(result["trend"]["state"], "unavailable")
        self.assertIn("ai", result)
        status, retry, _ = request(self.base, f"/api/sessions/{self.sid}/samples", body)
        self.assertEqual(status, 200)
        self.assertEqual(result["trend"], retry["trend"])
        self.assertTrue(retry["duplicate"])

    def test_experimental_advisory_does_not_override_operational_status(self):
        fake = {"state": "deteriorating", "advisory": True, "summary": "실험 권고",
                "source": "scenario", "checks": [], "channels": {}, "history_count": 13}
        with patch("trend_service.TrendMonitor") as factory:
            factory.return_value.update.return_value = fake
            body, result = self.send()
        self.assertEqual(result["status"], "unknown")  # IMU remains unavailable.
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["notification_events"], [])
        self.assertEqual(len(result["trend_notification_events"]), 1)
        _, retry, _ = request(self.base, f"/api/sessions/{self.sid}/samples", body)
        self.assertEqual(retry["trend_notification_events"], [])


if __name__ == "__main__":
    unittest.main()
