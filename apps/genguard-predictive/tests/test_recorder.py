import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recorder import Recorder


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "recordings"
        self.recorder = Recorder(self.directory)
        self.sample = {"run_id": "live-run", "timestamp_s": 12.0,
                       "coolant_c": 82.4, "data_source": "external_telemetry"}
        self.analysis = {"state": "normal", "forecast": [{"ahead_s": 30, "values": {"coolant_c": 83}}]}

    def tearDown(self):
        self.recorder.close()
        self.temp.cleanup()

    def test_lazy_creation_flush_readback_and_separate_labels(self):
        self.assertFalse(self.directory.exists())
        sample, analysis = copy.deepcopy(self.sample), copy.deepcopy(self.analysis)
        result = self.recorder.record(sample, analysis, "model-123")
        self.assertTrue(result["ok"])
        # Read before close: each record was flushed.
        telemetry = json.loads(Path(result["telemetry_path"]).read_text())
        output = json.loads(Path(result["analyses_path"]).read_text())
        self.assertEqual(telemetry["record_id"], output["record_id"])
        self.assertEqual(telemetry["model_id"], "model-123")
        self.assertTrue(telemetry["received_at_utc"].endswith("Z"))
        self.assertEqual(output["run_id"], sample["run_id"])
        self.assertEqual(output["timestamp_s"], sample["timestamp_s"])
        self.assertNotIn("forecast", telemetry)
        self.assertNotIn("state", telemetry)
        self.assertEqual(output["label_status"], "model_output_not_verified_ground_truth")
        self.assertEqual(sample, self.sample)
        self.assertEqual(analysis, self.analysis)
        self.assertFalse(result["online_training"])

    def test_append_keeps_prior_bytes_and_ids_unique(self):
        first = self.recorder.record(self.sample, self.analysis, "model-1")
        path = Path(first["telemetry_path"])
        original = path.read_bytes()
        second = self.recorder.record(dict(self.sample, timestamp_s=14), self.analysis, "model-2")
        self.assertTrue(path.read_bytes().startswith(original))
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["record_id"], rows[1]["record_id"])
        self.assertEqual(second["records_written"], 2)

    def test_new_instances_never_overwrite_previous_session(self):
        first = self.recorder.record(self.sample, self.analysis, "model-1")
        original = Path(first["telemetry_path"]).read_bytes()
        with Recorder(self.directory) as other:
            second = other.record(self.sample, self.analysis, "model-1")
            self.assertNotEqual(first["session_id"], second["session_id"])
            self.assertNotEqual(first["telemetry_path"], second["telemetry_path"])
        self.assertEqual(Path(first["telemetry_path"]).read_bytes(), original)

    def test_nonfinite_values_become_explicit_null_without_input_mutation(self):
        sample = dict(self.sample, coolant_c=float("nan"))
        analysis = dict(self.analysis, metric=[float("inf"), -float("inf"), 2])
        result = self.recorder.record(sample, analysis, "model-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["nonfinite_values_replaced"], 3)
        telemetry = json.loads(Path(result["telemetry_path"]).read_text())
        output = json.loads(Path(result["analyses_path"]).read_text())
        self.assertIsNone(telemetry["coolant_c"])
        self.assertEqual(output["metric"], [None, None, 2])
        self.assertEqual(output["recorder_nonfinite_values_replaced"], 2)

    def test_rotation_bounds_each_file_and_preserves_every_pair(self):
        with Recorder(self.directory, max_file_bytes=1000) as recorder:
            for i in range(8):
                result = recorder.record(dict(self.sample, timestamp_s=i), self.analysis, "model-1")
                self.assertTrue(result["ok"], result)
            self.assertGreater(result["part"], 1)
            telemetry_ids, analysis_ids = [], []
            for path in self.directory.glob("*.jsonl"):
                self.assertLessEqual(path.stat().st_size, 1000)
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                destination = telemetry_ids if path.name.endswith("-telemetry.jsonl") else analysis_ids
                destination.extend(row["record_id"] for row in rows)
            self.assertEqual(len(telemetry_ids), 8)
            self.assertEqual(set(telemetry_ids), set(analysis_ids))

    def test_oversized_record_is_surfaced_without_writing(self):
        with Recorder(self.directory, max_file_bytes=50) as recorder:
            result = recorder.record(self.sample, self.analysis, "model-1")
            self.assertFalse(result["ok"])
            self.assertIn("exceeds max_file_bytes", result["error"])
            self.assertFalse(self.directory.exists())

    def test_directory_io_failure_is_reported_not_raised(self):
        self.directory.write_text("occupied by a file")
        result = self.recorder.record(self.sample, self.analysis, "model-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_records"], 1)
        self.assertFalse(result["telemetry_written"])
        self.assertIn("Error", result["error"])

    def test_partial_pair_failure_is_visible_and_next_write_rotates(self):
        real_write = self.recorder._write_line
        calls = [0]
        def fail_second(handle, blob):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("injected disk full")
            return real_write(handle, blob)
        with patch.object(self.recorder, "_write_line", side_effect=fail_second):
            failed = self.recorder.record(self.sample, self.analysis, "model-1")
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["partial"])
        self.assertTrue(failed["write_outcome_uncertain"])
        self.assertTrue(failed["telemetry_written"])
        self.assertFalse(failed["analysis_written"])
        original = Path(failed["telemetry_path"]).read_bytes()
        result = self.recorder.record(self.sample, self.analysis, "model-1")
        self.assertTrue(result["ok"])
        self.assertNotEqual(failed["telemetry_path"], result["telemetry_path"])
        self.assertEqual(Path(failed["telemetry_path"]).read_bytes(), original)
        self.assertEqual(result["telemetry_records_written"], 2)
        self.assertEqual(result["analysis_records_written"], 1)
        self.assertEqual(result["records_written"], 1)

    def test_exclusive_creation_prevents_truncating_existing_file(self):
        first = self.recorder.record(self.sample, self.analysis, "model-1")
        path = Path(first["telemetry_path"])
        original = path.read_bytes()
        with Recorder(self.directory) as collision:
            collision.session_id = first["session_id"]
            result = collision.record(self.sample, self.analysis, "model-1")
            self.assertFalse(result["ok"])
            self.assertIn("FileExistsError", result["error"])
        self.assertEqual(path.read_bytes(), original)

    def test_closed_recorder_reports_error_instead_of_reopening(self):
        self.recorder.close()
        result = self.recorder.record(self.sample, self.analysis, "model-1")
        self.assertFalse(result["ok"])
        self.assertIn("closed", result["error"])
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()
