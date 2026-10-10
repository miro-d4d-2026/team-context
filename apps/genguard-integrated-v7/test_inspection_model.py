import copy
import unittest

from inspection_fixtures import seed_current, seed_records
from inspection_model import InspectionComparator


class InspectionModelTests(unittest.TestCase):
    def setUp(self):
        self.records = seed_records()
        self.current = seed_current()
        self.model = InspectionComparator(self.records)

    def channel(self, result, field):
        return next(c for c in result["channels"] if c["field"] == field)

    def test_normal_is_normal_with_learned_parameters(self):
        result = self.model.compare(self.current)
        self.assertEqual(result["state"], "normal")
        self.assertEqual(result["model"]["training_record_count"], 8)
        self.assertEqual(result["model"]["training_sample_count"], 240)
        self.assertFalse(result["model"]["current_used_for_training"])
        exported = self.model.export_model(self.current)
        self.assertEqual(len(exported["models"]["coolant_c"]["weights"]), 5)

    def test_target_changes_are_independent_and_compound_reports_all(self):
        cases = {"coolant_c": 87, "oil_kgf_cm2": 2.55, "vibration_rms_g": .075}
        for field, value in cases.items():
            with self.subTest(field=field):
                current = copy.deepcopy(self.current)
                current["values"][field] = value
                result = self.model.compare(current)
                self.assertEqual(result["state"], "inspect")
                self.assertEqual([c["field"] for c in result["channels"] if c["state"] == "inspect"], [field])
        self.current["values"].update(cases)
        self.assertEqual(len(self.model.compare(self.current)["recommendations"]), 3)

    def test_valid_context_change_is_corrected(self):
        self.current["values"].update(load_pct=66.5, ambient_c=32.5, engine_rpm=1810, elapsed_s=690,
                                      coolant_c=82.01, oil_kgf_cm2=3.1458, vibration_rms_g=.0465)
        result = self.model.compare(self.current)
        self.assertEqual(result["state"], "normal")
        coolant = self.channel(result, "coolant_c")
        self.assertLess(abs(coolant["adjusted_delta"]), abs(coolant["delta"]) / 2)

    def test_current_never_changes_learned_weights(self):
        before = self.model.export_model(self.current)
        self.current["confirmed_normal"] = True
        self.current["values"]["coolant_c"] = 120
        self.assertEqual(before, self.model.export_model(self.current))

    def test_future_and_current_record_excluded(self):
        contaminated = copy.deepcopy(self.records[-1])
        contaminated.update(id="future", inspected_at="2027-01-01T10:00:00+09:00")
        for sample in contaminated["samples"]:
            sample["coolant_c"] += 20
        current_copy = copy.deepcopy(contaminated)
        current_copy.update(id=self.current["id"], inspected_at=self.current["inspected_at"])
        model = InspectionComparator(self.records + [contaminated, current_copy])
        self.assertEqual(self.model.export_model(self.current), model.export_model(self.current))

    def test_non_normal_history_cannot_teach_or_become_reference(self):
        bad = copy.deepcopy(self.records[-1])
        bad.update(id="past-anomaly", inspected_at="2026-10-10T11:00:00+09:00", confirmed_normal=False)
        bad["values"]["coolant_c"] = 90
        for sample in bad["samples"]:
            sample["coolant_c"] = 90
        result = InspectionComparator(self.records + [bad]).compare(self.current)
        self.assertEqual(result["state"], "normal")
        self.assertNotEqual(result["reference_id"], "past-anomaly")
        self.assertNotIn("past-anomaly", result["model"]["training_record_ids"])

    def test_missing_channel_is_not_normal_and_other_channels_still_work(self):
        self.current["values"].pop("vibration_rms_g")
        result = self.model.compare(self.current)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(self.channel(result, "coolant_c")["state"], "normal")
        self.assertEqual(self.channel(result, "vibration_rms_g")["state"], "unavailable")
        self.current["values"]["coolant_c"] = 87
        self.assertEqual(self.model.compare(self.current)["state"], "inspect")

    def test_stale_and_invalid_values_do_not_get_imputed(self):
        for value in (None, float("nan"), float("inf"), True, -100):
            with self.subTest(value=value):
                self.current["values"]["coolant_c"] = value
                self.assertEqual(self.channel(self.model.compare(self.current), "coolant_c")["state"], "unavailable")
        self.current = seed_current()
        self.current["quality"]["coolant_c"] = {"quality": "ok", "age_s": 6}
        self.assertEqual(self.channel(self.model.compare(self.current), "coolant_c")["state"], "unavailable")

    def test_mount_and_acquisition_mismatch(self):
        mutations = [("installation_id", "other"), ("sample_rate_hz", 416), ("accel_range_g", 16),
                     ("dc_removed", False), ("feature_version", "different")]
        for field, value in mutations:
            with self.subTest(field=field):
                current = copy.deepcopy(self.current)
                if field == "installation_id":
                    current[field] = value
                else:
                    current["acquisition"][field] = value
                result = self.model.compare(current)
                self.assertEqual(self.channel(result, "vibration_rms_g")["state"], "unavailable")
                self.assertEqual(self.channel(result, "coolant_c")["state"], "normal")

    def test_missing_acquisition_is_unavailable(self):
        self.current.pop("acquisition")
        self.assertEqual(self.channel(self.model.compare(self.current), "vibration_rms_g")["reason"], "acquisition_mismatch")

    def test_physical_imu_never_learns_from_fake_generator_context(self):
        for record in [*self.records, self.current]:
            record["sources"]["vibration_rms_g"] = "live_imu"
        result = InspectionComparator(self.records).compare(self.current)
        self.assertEqual(self.channel(result, "vibration_rms_g")["reason"], "unverified_sensor_operating_context")
        self.assertEqual(self.channel(result, "coolant_c")["state"], "normal")

    def test_actual_context_allows_separate_physical_imu_fit(self):
        for record in [*self.records, self.current]:
            record["sources"]["vibration_rms_g"] = "live_imu"
            for field in ("load_pct", "ambient_c", "engine_rpm"):
                record["sources"][field] = "controller"
            record["sources"]["elapsed_s"] = "device_clock"
        result = InspectionComparator(self.records).compare(self.current)
        self.assertEqual(self.channel(result, "vibration_rms_g")["state"], "normal")

    def test_source_mismatch_is_per_field(self):
        self.current["source"] = "hybrid"
        self.current["sources"]["vibration_rms_g"] = "recorded_imu"
        result = self.model.compare(self.current)
        self.assertEqual(self.channel(result, "vibration_rms_g")["state"], "unavailable")
        self.assertEqual(self.channel(result, "coolant_c")["state"], "normal")

    def test_context_mismatch_and_warmup_not_compared(self):
        for field, value in (("load_pct", 90), ("engine_rpm", 1900), ("elapsed_s", 60), ("ambient_c", 50)):
            with self.subTest(field=field):
                current = copy.deepcopy(self.current)
                current["values"][field] = value
                self.assertEqual(self.model.compare(current)["state"], "unavailable")

    def test_context_mismatch_reports_reason_without_comparing(self):
        self.current["values"]["load_pct"] = 90
        result = self.model.compare(self.current)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["headline"], "운전 조건이 달라 비교를 보류합니다")
        self.assertIsNone(result["reference_id"])
        self.assertIsNotNone(result["conditions"]["previous"])
        load = next(m for m in result["matches"] if m["field"] == "load_pct")
        self.assertFalse(load["matched"])
        self.assertEqual(load["delta"], 90 - load["previous"])
        for channel in result["channels"]:
            self.assertEqual(channel["reason"], "operating_conditions_mismatch")
            self.assertIsNone(channel["adjusted_delta"])
        self.assertFalse(result["model"]["trained"])

    def test_explicit_unconfirmed_reference_is_not_silently_replaced(self):
        abnormal = copy.deepcopy(self.records[-1])
        abnormal.update(id="abnormal", confirmed_normal=False)
        result = InspectionComparator(self.records + [abnormal]).compare(self.current, "abnormal")
        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(result["reference_id"])
        self.assertEqual(result["comparison_blocker"], "unconfirmed_reference")
        self.assertTrue(all(c["reason"] == "unconfirmed_reference" for c in result["channels"]))

    def test_equipment_and_phase_must_match(self):
        for field, value in (("equipment_id", "OTHER"), ("phase", "starting")):
            current = copy.deepcopy(self.current)
            current[field] = value
            self.assertEqual(self.model.compare(current)["state"], "unavailable")

    def test_model_needs_independent_runs_and_unique_windows(self):
        for records in (self.records[:2], self.records[:1] * 8):
            self.assertEqual(InspectionComparator(records).compare(self.current)["state"], "unavailable")
        for record in self.records:
            record["samples"] = [record["samples"][0]] * 100
        self.assertEqual(InspectionComparator(self.records).compare(self.current)["state"], "unavailable")

    def test_snapshot_does_not_inflate_training_count(self):
        for record in self.records:
            record.pop("samples")
        result = InspectionComparator(self.records).compare(self.current)
        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["model"]["trained"])

    def test_explicit_reference_respects_same_condition_and_normal(self):
        result = self.model.compare(self.current, self.records[0]["id"])
        self.assertEqual(result["reference_id"], self.records[0]["id"])
        self.assertEqual(self.model.compare(self.current, "missing")["state"], "unavailable")

    def test_json_model_is_finite_and_reproducible(self):
        import json
        exported = self.model.export_model(self.current)
        json.dumps(exported, allow_nan=False)
        self.assertEqual(exported, InspectionComparator(seed_records()).export_model(seed_current()))


if __name__ == "__main__":
    unittest.main()
