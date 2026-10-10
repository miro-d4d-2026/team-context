"""Meaningful preprocessing, split, recorded replay and quality-gate checks."""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vibration import VibrationMonitor, extract_features, replay_windows, rms_trend, _fit


class VibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads((ROOT / "data/vibration-feature-fixtures.json").read_text())["fixtures"]
        cls.model = json.loads((ROOT / "models/vibration.json").read_text())

    def setUp(self):
        self.monitor = VibrationMonitor()
        self.raw = copy.deepcopy(self.fixtures[1]["samples"])

    def test_actual_raw_excerpt_reproduces_supplied_feature_contract(self):
        for fixture in self.fixtures:
            axes = [[s[k] for k in ("ax_g", "ay_g", "az_g")] for s in fixture["samples"]]
            actual = extract_features(fixture["feature_times_s"], axes, fixture["origin_s"])
            expected = fixture["expected_features"]
            self.assertAlmostEqual(actual["rms_vector_g"], expected["rms_vector_g"], places=12)
            self.assertAlmostEqual(actual["envelope_cv"], expected["envelope_cv"], places=12)
            self.assertAlmostEqual(actual["rms_contract_g"] * math.sqrt(3), actual["rms_vector_g"], places=12)

    def test_fit_and_radius_reproduce_without_random_data(self):
        rows = replay_windows("repeat")
        fit = [r for r in rows if r["cycle"] in (1, 2)]
        calibration = [r for r in rows if r["cycle"] == 3]
        self.assertEqual((len(fit), len(calibration)), (32, 16))
        for name, model in self.model["models"].items():
            actual = _fit(fit, calibration, model["features"])
            self.assertEqual(actual, model)
        self.assertFalse(self.model["models"]["rms_and_pattern"]["rejection_calibration"]["random_test_used"])

    def test_separate_recording_results_match_report_with_real_denominator(self):
        rows = replay_windows("random")
        outputs = [self.monitor.classify_features(r["rms_vector_g"], r["envelope_cv"]) for r in rows]
        report = json.loads((ROOT / "reports/vibration.json").read_text())
        metrics = report["models"]["rms_and_pattern"]["random_with_unknown_rejection"]
        self.assertEqual(len(rows), metrics["count"])
        self.assertEqual(sum(o["pattern_id"] == r["expected_pattern"] for o, r in zip(outputs, rows)), metrics["correct"])
        self.assertEqual(sum(o["pattern_id"] == "unknown" for o in outputs), metrics["unknown"])
        self.assertTrue(all(o["health_state"] == "unknown" and o["confidence"] is None for o in outputs))

    def test_profile_invalid_and_unknown_pattern_are_not_forced_choices(self):
        known = replay_windows()[0]
        out = self.monitor.classify_features(known["rms_vector_g"], known["envelope_cv"], False)
        self.assertEqual(out["unknown_reason"], "installation_profile_unconfirmed")
        for rms, cv in [(float("nan"), 1), (True, 0.2), (-0.1, 1), (0.1, float("inf"))]:
            self.assertEqual(self.monitor.classify_features(rms, cv)["unknown_reason"], "invalid_features")
        out = self.monitor.classify_features(0.1, 100)
        self.assertEqual(out["pattern_id"], "unknown")
        self.assertEqual(out["unknown_reason"], "outside_recorded_pattern_support")
        self.assertGreater(out["model_distance"], out["acceptance_radius"])

    def test_live_good_data_and_unknown_fullscale_are_explicit(self):
        out = self.monitor.process_samples(self.raw, {"profile_confirmed": True})
        self.assertEqual(out["pattern_id"], "baseline")
        self.assertEqual(out["source"], "live_imu")
        self.assertEqual(out["health_state"], "unknown")
        self.assertFalse(out["freshness_checked"])
        self.assertTrue(out["quality_notes"])
        out = self.monitor.process_samples(self.raw)
        self.assertEqual(out["unknown_reason"], "installation_profile_unconfirmed")

    def test_sequence_gap_sampling_gap_and_saturation_reject(self):
        missing = self.raw[:100] + self.raw[101:]
        self.assertEqual(self.monitor.process_samples(missing, {"profile_confirmed": True})["unknown_reason"], "sequence_gap")
        jump = copy.deepcopy(self.raw)
        for row in jump[100:]:
            row["elapsed_s"] += 0.025
        self.assertEqual(self.monitor.process_samples(jump, {"profile_confirmed": True})["unknown_reason"], "sample_gap_over_10ms")
        saturated = copy.deepcopy(self.raw)
        saturated[150]["ax_g"] = 2.0
        self.assertEqual(self.monitor.process_samples(saturated, {"profile_confirmed": True, "accel_range_g": 2})["unknown_reason"], "sensor_saturation_or_range_proximity")

    def test_stale_short_nonfinite_and_counter_wrap(self):
        self.assertEqual(self.monitor.process_samples(self.raw, {"fresh": False})["unknown_reason"], "stale_input")
        self.assertEqual(self.monitor.process_samples(self.raw[:20])["unknown_reason"], "collecting_2s_window")
        invalid = copy.deepcopy(self.raw)
        invalid[200]["ax_g"] = float("nan")
        self.assertEqual(self.monitor.process_samples(invalid)["unknown_reason"], "invalid_acceleration")
        wrapped = copy.deepcopy(self.raw)
        for i, row in enumerate(wrapped):
            row["seq"] = (2**32 - 200 + i) % 2**32
        self.assertEqual(self.monitor.process_samples(wrapped, {"profile_confirmed": True})["pattern_id"], "baseline")

    def test_rising_rule_is_separate_and_has_no_learned_fault_claim(self):
        # Artificial rule unit fixture only; never counted as measured training data.
        rising = [{"timestamp_s": i * 2, "rms_vector_g": 0.04 + i * 0.003} for i in range(12)]
        out = rms_trend(rising)
        self.assertEqual(out["state"], "rising")
        self.assertEqual(out["source"], "rule")
        self.assertFalse(out["trained_pattern"])
        self.assertFalse(out["generator_fault_diagnosis"])
        self.assertEqual(rms_trend(rising[:2])["state"], "insufficient_history")


if __name__ == "__main__":
    unittest.main()
