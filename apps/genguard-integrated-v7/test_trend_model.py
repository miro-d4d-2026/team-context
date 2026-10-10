import copy
import json
from pathlib import Path
import unittest

import numpy as np

from trend_model import CHANNELS, MODEL_PATH, TrendMonitor, feature_vector, forecast
from trend_scenarios import generate_scenario, scenario_catalog


def values(coolant=80., oil=3.2, load=62.):
    return {"coolant_c": coolant, "oil_kgf_cm2": oil,
            "load_pct": load, "ambient_c": 28.5, "engine_rpm": 1800.}


class TrendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = json.loads(MODEL_PATH.read_text())

    def linear_monitor(self):
        model = copy.deepcopy(self.model)
        model["selected_method"] = {k: "linear" for k in CHANNELS}
        model["absolute_error_band"] = {k: 0.1 for k in CHANNELS}
        model["advisory_policy"] = "point_forecast"
        return TrendMonitor(model)

    def warning_monitor(self):
        monitor = self.linear_monitor()
        for i in range(15):
            result = monitor.update(i * 10., values(coolant=86.5 + i / 3))
        self.assertTrue(result["advisory"])
        return monitor, result

    def test_model_is_learned_finite_and_complete_run_split(self):
        m = self.model
        self.assertEqual(np.asarray(m["weights"]).shape, (21, 2))
        self.assertTrue(np.isfinite(m["weights"]).all())
        self.assertGreater(m["training_examples"], 1000)
        groups = [set(m["split"][key]) for key in ("fit_seeds", "calibration_seeds", "test_seeds")]
        self.assertFalse(groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
        self.assertFalse(groups[2] & set(m["exploratory_seeds_excluded"]))

    def test_schema_target_change_rejected(self):
        model = copy.deepcopy(self.model)
        model["target_fields"] = ["vibration_rms_g"]
        with self.assertRaises(ValueError):
            TrendMonitor(model)

    def test_corrupt_weights_rejected(self):
        model = copy.deepcopy(self.model)
        model["weights"][0][0] = float("nan")
        with self.assertRaises(ValueError):
            TrendMonitor(model)

    def test_missing_context_guard_rejected(self):
        model = copy.deepcopy(self.model)
        del model["context_bounds"]
        with self.assertRaises(ValueError):
            TrendMonitor(model)

    def test_catalog_and_repeatable_fixture(self):
        self.assertEqual(len(scenario_catalog()), 8)
        self.assertEqual(generate_scenario("normal", 42), generate_scenario("normal", 42))
        self.assertNotEqual(generate_scenario("normal", 42), generate_scenario("normal", 43))
        with self.assertRaises(ValueError):
            generate_scenario("fake_fault")

    def test_normal_does_not_create_advisory(self):
        monitor = TrendMonitor()
        results = [monitor.update(**row) for row in generate_scenario("normal", 901)]
        self.assertFalse(any(r["advisory"] for r in results))
        self.assertEqual(results[-1]["state"], "stable")
        self.assertEqual(results[-1]["source"], "scenario")

    def test_twelve_samples_cannot_forecast(self):
        monitor = TrendMonitor()
        for i in range(12):
            result = monitor.update(i * 10., values())
        self.assertEqual(result["state"], "collecting")
        result = monitor.update(120., values())
        self.assertEqual(result["observed_span_s"], 120.)
        self.assertEqual(result["state"], "stable")

    def test_duplicate_and_dense_calls_never_accumulate_history(self):
        monitor = TrendMonitor()
        monitor.update(0., values())
        for t in [0.] * 20 + list(np.arange(.1, 8.9, .1)):
            result = monitor.update(float(t), values())
            self.assertFalse(result["accepted"])
        self.assertEqual(len(monitor.rows), 1)

    def test_three_distinct_forecasts_required(self):
        monitor = self.linear_monitor()
        for i in range(13):
            result = monitor.update(i * 10., values(coolant=86.5 + i / 3))
        self.assertFalse(result["advisory"])
        self.assertEqual(result["channels"]["coolant_c"]["advisory_streak"], 1)
        for _ in range(30):
            result = monitor.update(120., values(coolant=90.5))
        self.assertFalse(result["advisory"])
        monitor.update(130., values(coolant=86.5 + 13 / 3))
        result = monitor.update(140., values(coolant=86.5 + 14 / 3))
        self.assertTrue(result["advisory"])

    def test_dense_recovery_clears_old_advisory(self):
        monitor, _ = self.warning_monitor()
        result = monitor.update(142., values(coolant=90.5))
        self.assertFalse(result["advisory"])
        self.assertEqual(result["reason"], "new_values_invalidate_previous_trend")

    def test_dense_limit_crossing_is_current_alarm_not_forecast(self):
        monitor, _ = self.warning_monitor()
        result = monitor.update(142., values(coolant=92.))
        self.assertEqual(result["state"], "limit_reached")
        self.assertFalse(result["advisory"])

    def test_dense_context_change_invalidates_trend(self):
        monitor, _ = self.warning_monitor()
        result = monitor.update(142., values(coolant=91.1666667, load=68.))
        self.assertFalse(result["advisory"])
        self.assertEqual(result["history_count"], 1)

    def test_oil_boundary_is_strict_less_than_two(self):
        monitor = TrendMonitor()
        self.assertEqual(monitor.update(0., values(oil=2.))["state"], "collecting")
        self.assertEqual(monitor.update(2., values(oil=1.9999))["state"], "limit_reached")

    def test_gap_resets_history(self):
        monitor = TrendMonitor()
        monitor.update(0., values())
        result = monitor.update(12., values())
        self.assertEqual(result["reason"], "observation_gap")
        self.assertEqual(result["history_count"], 1)

    def test_regular_but_wrong_total_span_is_not_predicted(self):
        monitor = TrendMonitor()
        for i in range(13):
            result = monitor.update(i * 11., values())
        self.assertEqual(result["reason"], "irregular_sample_timing")
        self.assertEqual(result["observed_span_s"], 132.)
        self.assertFalse(result["channels"])

    def test_stale_input_clears_model_history(self):
        monitor, _ = self.warning_monitor()
        result = monitor.update(150., values(), {"coolant_c": {"age_s": 5.1, "quality": "ok"}})
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["history_count"], 0)
        self.assertFalse(result["advisory"])

    def test_missing_and_nonfinite_values_are_not_imputed(self):
        for bad in (None, float("nan"), float("inf"), True, "80"):
            result = TrendMonitor().update(0., values(coolant=bad))
            self.assertEqual(result["reason"], "invalid_or_stale_input")

    def test_unseen_context_cannot_extrapolate(self):
        for key, bad in (("load_pct", 0.), ("ambient_c", 70.), ("engine_rpm", 2500.)):
            v = values(); v[key] = bad
            result = TrendMonitor().update(0., v)
            self.assertEqual(result["reason"], "unseen_operating_context")

    def test_phase_change_resets_and_backwards_time_rejected(self):
        monitor = TrendMonitor()
        monitor.update(0., values())
        result = monitor.update(10., values(), phase="stopped")
        self.assertEqual(result["history_count"], 0)
        monitor.update(20., values())
        self.assertEqual(monitor.update(19., values())["reason"], "clock_regression")

    def test_step_starts_new_window_instead_of_extrapolating(self):
        monitor = TrendMonitor()
        for i in range(13):
            monitor.update(i * 10., values())
        result = monitor.update(130., values(coolant=87.))
        self.assertEqual(result["reason"], "abrupt_step")
        self.assertEqual(result["history_count"], 1)
        self.assertFalse(result["advisory"])

    def test_slowly_changing_load_blocks_advisory(self):
        monitor = TrendMonitor()
        for i in range(13):
            result = monitor.update(i * 10., values(coolant=86.5 + i / 3, load=55 + i * .4))
        self.assertEqual(result["reason"], "unstable_operating_context")
        self.assertFalse(result["advisory"])

    def test_features_have_no_scenario_name_or_future(self):
        run = generate_scenario("normal", 901)
        history = run[:13]
        before = feature_vector(history)
        run[13]["values"]["coolant_c"] = 999.
        np.testing.assert_array_equal(feature_vector(history), before)
        self.assertEqual(before.shape, (20,))
        self.assertEqual(forecast(history, self.model).shape, (2,))

    def test_prediction_is_json_safe_and_not_fault_probability(self):
        monitor = TrendMonitor()
        for row in generate_scenario("normal", 901)[:13]:
            result = monitor.update(**row)
        json.dumps(result, allow_nan=False)
        self.assertIn("실제 신뢰구간 아님", result["interval_label"])
        self.assertNotIn("rul", result)
        self.assertNotIn("fault_probability", result)

    def test_evaluation_contains_channel_matched_truth_and_baselines(self):
        report = json.loads((MODEL_PATH.parent / "evaluation.json").read_text())
        self.assertFalse(report["model_selection_used_test"])
        self.assertEqual(set(report["mae"]), {"ridge", "linear", "persistence", "selected"})
        self.assertEqual(report["test_runs"], 160)
        for run in report["run_results"]:
            self.assertEqual(set(run["same_channel_lead_s"]), set(CHANNELS))
            self.assertEqual(sum(run["false_advisory_by_channel"].values()),
                             run["false_advisory_updates_with_observed_future"])


if __name__ == "__main__":
    unittest.main()
