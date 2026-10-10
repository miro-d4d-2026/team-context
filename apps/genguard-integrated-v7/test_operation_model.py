import copy
import json
import unittest

from operation_fixtures import make_record, scenarios, seed_current, seed_records
from operation_model import OperationAnalyzer


class OperationModelTests(unittest.TestCase):
    def setUp(self):
        self.records = seed_records()
        self.current = seed_current()
        self.model = OperationAnalyzer(self.records)

    def channel(self, result, field):
        return next(c for c in result["channels"] if c["field"] == field)

    def test_normal_warmup_is_normal_despite_28_degree_temperature_rise(self):
        result = self.model.analyze(self.current)
        self.assertEqual(result["state"], "normal")
        self.assertEqual(result["model"]["training_count"], 8)
        coolant = self.channel(result, "coolant_c")
        self.assertEqual([row["observed_delta"] for row in coolant["intervals"]], [10, 8, 5, 5])
        self.assertEqual([row["observed_rate"] for row in coolant["intervals"]], [2, 1.6, 1, 1])
        self.assertTrue(all(row["state"] == "normal" for row in coolant["intervals"]))

    def test_spike_is_identified_at_5_to_10_minutes_before_high_absolute_value(self):
        current = scenarios()["coolant_rise"]
        current["samples"] = current["samples"][:3]
        result = self.model.analyze(current)
        c = self.channel(result, "coolant_c")
        self.assertEqual(c["current"], 80)
        self.assertEqual(c["state"], "inspect")
        self.assertEqual(c["interval"], {"start_s": 300, "end_s": 600})
        self.assertEqual(c["observed_rate"], 4)
        self.assertAlmostEqual(c["expected_rate"], 1.6, delta=.1)
        self.assertEqual(c["intervals"][-1]["observed_delta"], 20)
        self.assertAlmostEqual(c["intervals"][-1]["expected_delta"], 8, delta=.5)
        self.assertEqual(self.channel(result, "oil_kgf_cm2")["state"], "normal")

    def test_same_final_temperature_does_not_hide_abnormal_middle_interval(self):
        self.current["samples"][2]["coolant_c"] = 80
        r = self.model.analyze(self.current)
        self.assertEqual(self.current["samples"][-1]["coolant_c"], 78)
        self.assertEqual(self.channel(r, "coolant_c")["state"], "inspect")
        self.assertEqual(self.channel(r, "coolant_c")["worst_interval"]["start_s"], 300)

    def test_earlier_anomaly_is_retained_but_latest_recovery_is_explicit(self):
        r = self.model.analyze(scenarios()["coolant_rise"])
        c = self.channel(r, "coolant_c")
        self.assertEqual(c["state"], "inspect")
        self.assertEqual(c["intervals"][-1]["state"], "normal")
        self.assertIn("현재 구간은 평소 변화로 돌아왔지만", c["summary"])

    def test_pressure_warmup_decline_is_normal_but_later_drop_is_not(self):
        normal = self.channel(self.model.analyze(self.current), "oil_kgf_cm2")
        self.assertEqual(normal["state"], "normal")
        self.assertAlmostEqual(normal["intervals"][0]["observed_rate"], -.08)
        abnormal = self.channel(self.model.analyze(scenarios()["oil_drop"]), "oil_kgf_cm2")
        self.assertEqual(abnormal["state"], "inspect")
        self.assertEqual(abnormal["worst_interval"]["start_s"], 600)
        self.assertAlmostEqual(abnormal["worst_interval"]["observed_rate"], -.2)

    def test_pressure_low_plateau_still_gets_checked_without_new_drop(self):
        for sample in self.current["samples"]:
            sample["oil_kgf_cm2"] -= .6
        c = self.channel(self.model.analyze(self.current), "oil_kgf_cm2")
        self.assertEqual(c["state"], "inspect")
        self.assertEqual(c["reason"], "oil_pressure_phase_level_changed")

    def test_vibration_uses_amplitude_ratio_not_temperature_style_slope(self):
        c = self.channel(self.model.analyze(scenarios()["vibration_increase"]), "vibration_rms_g")
        self.assertEqual(c["state"], "inspect")
        self.assertEqual(c["method"], "amplitude_ratio_and_step")
        self.assertIsNone(c["observed_rate"])
        self.assertIsNone(c["expected_rate"])
        self.assertAlmostEqual(c["change_ratio"], 2, delta=.1)

    def test_vibration_reducing_or_disappearing_does_not_silently_look_normal(self):
        self.current["samples"][-1]["vibration_rms_g"] = 0
        c = self.channel(self.model.analyze(self.current), "vibration_rms_g")
        self.assertEqual(c["reason"], "vibration_amplitude_decrease")
        self.assertEqual(c["state"], "inspect")

    def test_compound_changes_report_all_channels(self):
        r = self.model.analyze(scenarios()["combined"])
        self.assertEqual(len(r["recommendations"]), 3)
        self.assertEqual({c["method"] for c in r["channels"]}, {"phase_rise_rate", "warmup_rate_and_level", "amplitude_ratio_and_step"})

    def test_prefixes_compare_only_observed_time_and_not_future_spike(self):
        current = scenarios()["coolant_rise"]
        current["samples"] = current["samples"][:2]
        r = self.model.analyze(current)
        self.assertEqual(r["state"], "normal")
        self.assertEqual(r["observed_until_s"], 300)
        self.assertEqual(len(self.channel(r, "coolant_c")["intervals"]), 1)

    def test_one_point_waits_instead_of_marking_normal(self):
        self.current["samples"] = self.current["samples"][:1]
        r = self.model.analyze(self.current)
        self.assertEqual(r["state"], "unavailable")
        self.assertEqual(r["comparison_blocker"], "insufficient_observation")

    def test_missing_sensor_does_not_block_other_channels(self):
        for s in self.current["samples"]:
            s["vibration_rms_g"] = None
        r = self.model.analyze(self.current)
        self.assertEqual(r["state"], "unavailable")
        self.assertEqual(self.channel(r, "coolant_c")["state"], "normal")
        self.assertEqual(self.channel(r, "oil_kgf_cm2")["state"], "normal")
        self.assertEqual(self.channel(r, "vibration_rms_g")["state"], "unavailable")

    def test_missing_middle_sample_never_invents_a_flat_curve(self):
        self.current["samples"][2].pop("oil_kgf_cm2")
        r = self.model.analyze(self.current)
        c = self.channel(r, "oil_kgf_cm2")
        self.assertEqual(c["state"], "unavailable")
        self.assertEqual(c["intervals"][1]["reason"], "invalid_current_value")
        self.assertEqual(c["intervals"][2]["reason"], "invalid_current_value")

    def test_invalid_numbers_not_serialized_as_nan_or_treated_as_zero(self):
        for value in (None, True, float("nan"), float("inf"), -3):
            with self.subTest(value=value):
                self.current["samples"][-1]["oil_kgf_cm2"] = value
                r = self.model.analyze(self.current)
                self.assertEqual(self.channel(r, "oil_kgf_cm2")["state"], "unavailable")
                json.dumps(r, allow_nan=False)

    def test_duplicate_reversed_and_misaligned_times_are_unavailable(self):
        mutations = [lambda s: s.__setitem__(2, copy.deepcopy(s[1])),
                     lambda s: s.reverse(), lambda s: s[2].update(elapsed_s=601),
                     lambda s: s.pop(2), lambda s: s[0].update(elapsed_s=10)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                current = seed_current()
                mutate(current["samples"])
                r = self.model.analyze(current)
                self.assertEqual(r["state"], "unavailable")
                self.assertTrue(all(c["state"] == "unavailable" for c in r["channels"]))

    def test_context_mismatch_refuses_to_interpret_heat_as_fault(self):
        for field, value in (("load_pct", 95), ("ambient_c", 50), ("engine_rpm", 3000)):
            with self.subTest(field=field):
                current = seed_current()
                current["context"][field] = value
                r = self.model.analyze(current)
                self.assertEqual(r["state"], "unavailable")
                self.assertEqual(r["comparison_blocker"], "operating_conditions_mismatch")

    def test_initial_temperature_mismatch_refuses_cold_hot_start_comparison(self):
        for s in self.current["samples"]:
            s["coolant_c"] += 20
        r = self.model.analyze(self.current)
        self.assertEqual(r["comparison_blocker"], "operating_conditions_mismatch")

    def test_small_initial_offset_does_not_become_a_false_rise_alarm(self):
        for s in self.current["samples"]:
            s["coolant_c"] += 2
        r = self.model.analyze(self.current)
        self.assertEqual(self.channel(r, "coolant_c")["state"], "normal")

    def test_source_and_mount_are_guarded(self):
        self.current["source"] = "controller"
        self.assertEqual(self.model.analyze(self.current)["comparison_blocker"], "source_mismatch")
        self.current = seed_current()
        self.current["installation_id"] = "moved-sensor"
        r = self.model.analyze(self.current)
        self.assertEqual(self.channel(r, "vibration_rms_g")["reason"], "installation_mismatch")
        self.assertEqual(self.channel(r, "coolant_c")["state"], "normal")

    def test_acquisition_settings_must_match_for_vibration(self):
        for field, value in (("sample_rate_hz", 416), ("window_s", 4), ("dc_removed", False), ("accel_range_g", 16)):
            with self.subTest(field=field):
                current = seed_current()
                current["acquisition"][field] = value
                r = self.model.analyze(current)
                self.assertEqual(self.channel(r, "vibration_rms_g")["reason"], "acquisition_mismatch")

    def test_unapproved_and_future_runs_cannot_train_or_be_reference(self):
        extra = []
        for ident, at, approved in (("future", "2027-01-01T00:00:00+09:00", True),
                                   ("unapproved", "2026-10-10T10:00:00+09:00", False),
                                   (self.current["id"], self.current["inspected_at"], True)):
            r = make_record(ident, self.current["context"], 9, at, confirmed_normal=approved)
            r["id"] = ident
            for s in r["samples"]:
                s["coolant_c"] += 30
            extra.append(r)
        self.model.analyze(self.current)
        base = self.model.export_model()
        contaminated = OperationAnalyzer(self.records + extra)
        result = contaminated.analyze(self.current)
        self.assertEqual(contaminated.export_model(), base)
        self.assertFalse(set(r["id"] for r in extra) & set(result["model"]["training_record_ids"]))
        self.assertEqual(contaminated.analyze(self.current, "unapproved")["comparison_blocker"], "unconfirmed_reference")

    def test_current_targets_do_not_modify_learned_parameters(self):
        self.model.analyze(self.current)
        weights = self.model.export_model()
        for s in self.current["samples"][1:]:
            s.update(coolant_c=120, oil_kgf_cm2=.1, vibration_rms_g=3)
        self.current["confirmed_normal"] = True
        self.model.analyze(self.current)
        self.assertEqual(self.model.export_model(), weights)

    def test_duplicate_histories_cannot_manufacture_three_training_runs(self):
        duplicates = []
        for index in range(6):
            r = copy.deepcopy(self.records[-1])
            r["id"] = f"duplicate-{index}"
            duplicates.append(r)
        result = OperationAnalyzer(duplicates).analyze(self.current)
        self.assertEqual(result["state"], "unavailable")
        self.assertTrue(all(c["reason"] == "insufficient_training_history" for c in result["channels"]))

    def test_missing_history_is_not_normal(self):
        r = OperationAnalyzer([]).analyze(self.current)
        self.assertEqual(r["state"], "unavailable")
        self.assertEqual(r["comparison_blocker"], "comparable_history_required")

    def test_model_export_contains_learned_per_phase_coefficients(self):
        self.model.analyze(self.current)
        artifact = self.model.export_model()
        self.assertEqual(len(artifact["models"]["coolant_c"]), 4)
        self.assertEqual(len(artifact["models"]["coolant_c"]["2"]["weights"]), 5)
        self.assertIn("level", artifact["models"]["oil_kgf_cm2"]["2"])
        self.assertNotIn("level", artifact["models"]["vibration_rms_g"]["2"])
        self.assertTrue(artifact["metadata"]["synthetic"])
        self.assertFalse(artifact["metadata"]["current_used_for_training"])


if __name__ == "__main__":
    unittest.main()
