"""Integration-boundary tests for the T4-6 v1 data contract."""

from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest

from validate_contracts import load_json, validate_pair, validate_record


EXAMPLES = Path(__file__).resolve().parent / "examples"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_json(EXAMPLES / "raw-window-2s.json")
        self.warning = load_json(EXAMPLES / "result-warning.json")
        self.normal = load_json(EXAMPLES / "result-normal.json")
        self.replay = load_json(EXAMPLES / "result-rul-example.json")

    def assertInvalid(self, record):
        self.assertTrue(validate_record(record), "A malformed record was accepted")

    def test_all_eight_examples_are_valid(self):
        paths = sorted(EXAMPLES.glob("*.json"))
        self.assertEqual(len(paths), 8)
        for path in paths:
            with self.subTest(example=path.name):
                self.assertEqual(validate_record(load_json(path)), [])

    def test_raw_and_warning_are_a_valid_pair(self):
        self.assertEqual(validate_pair(self.raw, self.warning), [])

    def test_independent_fixtures_do_not_reuse_record_identities(self):
        keys = []
        for path in EXAMPLES.glob("*.json"):
            record = load_json(path)
            if record["message_type"] == "raw_window":
                key = ("raw", record["node_id"], record["boot_id"], record["window_seq"])
            else:
                key = ("result", record["node_id"], record["record_seq"])
            keys.append(key)
        self.assertEqual(len(keys), len(set(keys)))

    def test_record_sequence_preserves_javascript_integer_precision(self):
        self.warning["record_seq"] = 9007199254740992
        self.assertInvalid(self.warning)

    def test_rms_is_mean_removed_and_averaged_across_axes(self):
        # Gravity/DC must not dominate; summing variances causes a sqrt(3) error.
        variances = []
        for axis in (1, 2, 3):
            values = [sample[axis] for sample in self.raw["samples"]]
            mean = math.fsum(values) / len(values)
            variances.append(math.fsum((value - mean) ** 2 for value in values) / len(values))
        expected = math.sqrt(math.fsum(variances) / 3)
        self.assertAlmostEqual(expected, 0.04472136, places=8)
        self.assertAlmostEqual(self.warning["features"]["rms_g"], expected, places=8)
        self.assertEqual(validate_pair(self.raw, self.warning), [])

    def test_missing_required_fields_are_rejected(self):
        for template, field in ((self.raw, "sample_count"), (self.warning, "rul")):
            with self.subTest(field=field):
                record = deepcopy(template)
                del record[field]
                self.assertInvalid(record)

    def test_unknown_fields_are_rejected_at_both_levels(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                record = deepcopy(self.warning)
                (record["features"] if nested else record)["unexpected"] = 123
                self.assertInvalid(record)

    def test_booleans_are_not_accepted_as_numbers(self):
        records = [deepcopy(self.raw), deepcopy(self.raw), deepcopy(self.warning)]
        records[0]["sample_count"] = True
        records[1]["samples"][1][0] = True
        records[2]["features"]["rms_g"] = False
        for index, record in enumerate(records):
            with self.subTest(index=index):
                self.assertInvalid(record)

    def test_in_memory_nonfinite_values_are_rejected(self):
        for number in (math.nan, math.inf, -math.inf):
            with self.subTest(number=number):
                record = deepcopy(self.raw)
                record["samples"][0][1] = number
                self.assertInvalid(record)

    def test_loader_rejects_nonfinite_overflow_duplicate_keys_and_nonobjects(self):
        texts = (
            '{"n": NaN}', '{"n": Infinity}', '{"n": -Infinity}',
            '{"n": 1e999}', '{"nested": {"x": 1, "x": 2}}', '[]',
        )
        with tempfile.TemporaryDirectory(prefix="d4d-contract-test-") as folder:
            path = Path(folder) / "invalid.json"
            for content in texts:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_json(path)

    def test_null_wall_clock_is_valid_when_both_records_agree(self):
        self.raw["measured_at"] = None
        self.warning["measured_at"] = None
        self.assertEqual(validate_record(self.raw), [])
        self.assertEqual(validate_record(self.warning), [])
        self.assertEqual(validate_pair(self.raw, self.warning), [])

    def test_wall_clock_requires_real_date_and_explicit_utc_z(self):
        for timestamp in (
            "2026-02-30T06:00:00Z", "2026-10-10T06:00:00",
            "2026-10-10T15:00:00+09:00", "2026-10-10T06:00:00+00:00",
            "2026-10-10Z", "2026-10-10T06:00:00.1234567Z",
        ):
            with self.subTest(timestamp=timestamp):
                record = deepcopy(self.raw)
                record["measured_at"] = timestamp
                self.assertInvalid(record)

    def test_declared_sample_count_must_match_samples(self):
        self.raw["sample_count"] -= 1
        self.assertInvalid(self.raw)

    def test_offsets_start_at_zero_and_increase_strictly(self):
        for kind in ("nonzero_first", "duplicate", "backward"):
            with self.subTest(kind=kind):
                record = deepcopy(self.raw)
                if kind == "nonzero_first":
                    record["samples"][0][0] = 1
                elif kind == "duplicate":
                    record["samples"][2][0] = record["samples"][1][0]
                else:
                    record["samples"][2][0] = record["samples"][1][0] - 1
                self.assertInvalid(record)

    def test_sample_rate_must_match_recorded_offsets(self):
        for rate in (0, -1, 100, self.raw["sample_rate_hz"] * 1.01):
            with self.subTest(rate=rate):
                record = deepcopy(self.raw)
                record["sample_rate_hz"] = rate
                self.assertInvalid(record)

    def test_clipping_count_and_full_scale_bounds(self):
        record = deepcopy(self.raw)
        record["quality"]["clipped_samples"] = record["sample_count"] + 1
        self.assertInvalid(record)
        record = deepcopy(self.raw)
        record["samples"][0][1] = record["full_scale_g"] * 1.02
        self.assertInvalid(record)

    def test_baseline_must_be_positive_and_null_as_a_pair(self):
        for baseline in (
            {"id": "test", "rms_g": 0},
            {"id": "test", "rms_g": None},
            {"id": None, "rms_g": 0.02},
        ):
            with self.subTest(baseline=baseline):
                record = deepcopy(self.warning)
                record["baseline"] = baseline
                self.assertInvalid(record)

    def test_reported_ratio_must_match_baseline(self):
        self.warning["features"]["rms_ratio"] = 2.1
        self.assertInvalid(self.warning)

    def test_bad_signal_cannot_claim_normal(self):
        self.normal["quality_state"] = "invalid"
        self.normal["quality_flags"] = ["sample_loss"]
        self.normal["reason_codes"].append("poor_signal")
        self.assertInvalid(self.normal)

    def test_warning_event_id_is_required_only_in_warning(self):
        self.warning["warning_event_id"] = None
        self.assertInvalid(self.warning)
        self.normal["warning_event_id"] = "c1f6f120-6da7-4194-b40c-6e0e1c5d7965"
        self.assertInvalid(self.normal)

    def test_normal_state_cannot_include_elevated_or_unknown_reasons(self):
        for reason in ("rms_elevated", "persistent_change", "poor_signal", "model_not_ready"):
            with self.subTest(reason=reason):
                result = deepcopy(self.normal)
                result["reason_codes"].append(reason)
                self.assertInvalid(result)

    def test_xjtu_rul_cannot_be_attached_to_live_imu(self):
        self.replay["source_kind"] = "live_imu"
        self.replay["source_ref"] = "physical-imu-01"
        self.replay["features"]["axes"] = ["x", "y", "z"]
        self.assertInvalid(self.replay)

    def test_rul_time_and_bearing_match_the_replayed_record(self):
        for field, value in (("as_of_minute", 31), ("bearing_id", "Bearing2_5")):
            with self.subTest(field=field):
                record = deepcopy(self.replay)
                record["rul"][field] = value
                self.assertInvalid(record)
        self.replay["source_ref"] = "XJTU-SY/Bearing1_5/record-32"
        self.assertInvalid(self.replay)

    def test_rul_unit_target_and_nonnegative_value_are_fixed(self):
        for field, value in (("unit", "hours"), ("target", "generator_failure"), ("value", -1)):
            with self.subTest(field=field):
                record = deepcopy(self.replay)
                record["rul"][field] = value
                self.assertInvalid(record)

    def test_frequency_must_be_strictly_below_nyquist(self):
        for factor in (0.5, 0.6):
            with self.subTest(factor=factor):
                record = deepcopy(self.warning)
                record["features"]["dominant_frequency_hz"] = record["input_window"]["sample_rate_hz"] * factor
                self.assertInvalid(record)

    def test_pair_rejects_wrong_provenance_window_metadata_and_unit(self):
        for field, value in (("asset_id", "different-asset"), ("measured_at", None), ("is_example", False)):
            with self.subTest(field=field):
                result = deepcopy(self.warning)
                result[field] = value
                self.assertEqual(validate_record(result), [])
                self.assertTrue(validate_pair(self.raw, result))
        result = deepcopy(self.warning)
        result["input_window"]["window_seq"] += 1
        self.assertTrue(validate_pair(self.raw, result))
        self.raw["acceleration_unit"] = "m/s2"
        self.assertTrue(validate_pair(self.raw, self.warning))

    def test_pair_rejects_wrong_rms_even_with_consistent_ratio(self):
        self.warning["features"]["rms_g"] *= math.sqrt(3)
        self.warning["baseline"]["rms_g"] *= math.sqrt(3)
        self.assertEqual(validate_record(self.warning), [])
        self.assertTrue(validate_pair(self.raw, self.warning))

    def test_raw_quality_problems_propagate_to_analysis(self):
        for field, value, flag in (
            ("dropped_samples", 1, "sample_loss"),
            ("clipped_samples", 1, "clipping"),
            ("timestamps", "reconstructed", "timing_unverified"),
            ("dropped_samples", None, "quality_unknown"),
        ):
            with self.subTest(field=field, value=value):
                raw = deepcopy(self.raw)
                raw["quality"][field] = value
                self.assertEqual(validate_record(raw), [])
                self.assertTrue(validate_pair(raw, self.warning))
                result = load_json(EXAMPLES / "result-invalid.json")
                result["node_id"] = raw["node_id"]
                result["asset_id"] = raw["asset_id"]
                result["quality_flags"] = [flag]
                self.assertEqual(validate_pair(raw, result), [])

    def test_sensor_missing_can_report_invalid_without_input_window(self):
        result = load_json(EXAMPLES / "result-invalid.json")
        result["input_window"] = None
        result["quality_flags"] = ["sensor_missing"]
        self.assertEqual(validate_record(result), [])
        result["features"]["rms_g"] = 0.1
        self.assertInvalid(result)
        result["features"]["rms_g"] = None
        result["quality_flags"] = ["sample_loss"]
        self.assertInvalid(result)

    def test_calibrating_and_unmeasured_sensor_fields_do_not_fake_values(self):
        result = load_json(EXAMPLES / "result-calibrating.json")
        result["baseline"] = {"id": "premature-baseline", "rms_g": 0.02}
        self.assertInvalid(result)
        for field in ("surface_temperature_c", "current_a", "oil_level_pct"):
            with self.subTest(field=field):
                result = deepcopy(self.warning)
                result["additional_measurements"][field] = 0
                self.assertInvalid(result)


if __name__ == "__main__":
    unittest.main()
