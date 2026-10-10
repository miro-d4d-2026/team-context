import copy
import hashlib
import json
import math
from pathlib import Path
import statistics
import unittest

from vibration_classifier import ARTIFACT_DIR, MODEL_FILE, MODEL_ID, VibrationClassifier, classify, load_recording


class VibrationClassifierTests(unittest.TestCase):
    def setUp(self):
        self.classifier = VibrationClassifier()
        self.training = self.classifier.load_recording("training")
        self.random = self.classifier.load_recording("random")

    def predict_frames(self, frames):
        return [self.classifier.classify(f["rms_g"], f["envelope_cv"])["label"] for f in frames]

    def test_artifacts_match_verified_manifest(self):
        manifest = json.loads((ARTIFACT_DIR / "manifest.json").read_text())
        self.assertTrue(manifest["source_zip_bytes_verified"])
        self.assertTrue(manifest["raw_recomputed"])
        for name, expected in manifest["artifacts"].items():
            self.assertEqual(hashlib.sha256((ARTIFACT_DIR / name).read_bytes()).hexdigest(), expected, name)
        self.assertEqual(hashlib.sha256((ARTIFACT_DIR / "frozen-original.json").read_bytes()).hexdigest(), "43590d31f824e1bea4b795d007a88d3a27cefd8dae76f9d96d305da27b524e19")

    def test_training_parameters_reproduced_from_repeat_only(self):
        frames = self.training["frames"][:32]
        self.assertEqual(self.classifier.artifact["training"]["cycles"], [1, 2])
        self.assertEqual(self.classifier.artifact["training"]["calibration_cycles"], [3])
        features = [[math.log(max(f["rms_g"], 1e-9)), f["envelope_cv"]] for f in frames]
        means = [statistics.fmean(x[d] for x in features) for d in range(2)]
        scales = [statistics.pstdev(x[d] for x in features) for d in range(2)]
        model = self.classifier.model
        for d in range(2):
            self.assertAlmostEqual(means[d], model["mean"][d], places=12)
            self.assertAlmostEqual(scales[d], model["std"][d], places=12)
        for label, center in zip(model["classes"], model["centroids"]):
            rows = [x for x, f in zip(features, frames) if f["expected_label"] == label]
            for d in range(2):
                actual = statistics.fmean((x[d] - means[d]) / scales[d] for x in rows)
                self.assertAlmostEqual(actual, center[d], places=12)
        self.assertFalse(model["rejection_calibration"]["random_test_used"])
        self.assertEqual(self.classifier.artifact["training"]["windows"], 32)
        self.assertEqual(self.classifier.artifact["training"]["calibration_windows"], 16)
        distances = []
        for frame in self.training["frames"]:
            point = [(x - m) / st for x, m, st in zip([math.log(frame["rms_g"]), frame["envelope_cv"]], means, scales)]
            center = model["centroids"][model["classes"].index(frame["expected_label"])]
            distances.append(math.dist(point, center))
        self.assertAlmostEqual(model["acceptance_radius"], 1.25 * max(distances), places=12)
        self.assertNotEqual(self.training["run_id"], self.random["run_id"])

    def test_held_out_recording_48_signal_predictions(self):
        predictions = self.predict_frames(self.random["frames"])
        truth = [f["expected_label"] for f in self.random["frames"]]
        self.assertEqual(sum(a == b for a, b in zip(predictions, truth)), 48)
        self.assertEqual(self.random["evaluation"]["rms_only"]["correct"], 36)
        self.assertEqual(self.random["evaluation"]["rms_and_pattern"]["segment_majority_correct"], 12)
        self.assertEqual(self.random["evaluation"]["role"], "held_out_recording")

    def test_labels_times_order_and_segments_do_not_affect_prediction(self):
        frames = copy.deepcopy(self.random["frames"])
        expected = self.predict_frames(frames)
        for frame in frames:
            frame.update(expected_label="deliberately-wrong", start_s=-1234, end_s=9999, segment_index=0, cycle=99)
        self.assertEqual(expected, self.predict_frames(frames))
        self.assertEqual(list(reversed(expected)), self.predict_frames(list(reversed(frames))))
        with self.assertRaises(TypeError):
            self.classifier.classify(frames[0]["rms_g"], frames[0]["envelope_cv"], expected_label="baseline")

    def test_inference_does_not_train_or_mutate_parameters(self):
        before = copy.deepcopy(self.classifier.artifact)
        self.predict_frames(self.random["frames"])
        self.assertEqual(before, self.classifier.artifact)

    def test_48_guarded_windows_not_163_seconds_continuous(self):
        for record in (self.training, self.random):
            self.assertEqual(record["frame_count"], 48)
            self.assertEqual(record["evaluated_duration_s"], 96)
            self.assertEqual(len({f["segment_index"] for f in record["frames"]}), 12)
            for index, frame in enumerate(record["frames"]):
                self.assertEqual(frame["index"], index)
                self.assertEqual(frame["end_s"] - frame["start_s"], 2)
                self.assertGreaterEqual(frame["samples"], 400)
                if index:
                    self.assertGreaterEqual(frame["start_s"], record["frames"][index - 1]["end_s"])
            self.assertTrue(any(b["start_s"] > a["end_s"] for a, b in zip(record["frames"], record["frames"][1:])))
            self.assertEqual(record["source"], "recorded_imu")

    def test_all_four_classes_map_to_honest_demo_states(self):
        expected_states = {"idle": "idle", "baseline": "normal", "increased": "inspect", "intermittent": "inspect"}
        for label, state in expected_states.items():
            f = next(f for f in self.random["frames"] if f["expected_label"] == label)
            result = classify(f["rms_g"], f["envelope_cv"])
            self.assertEqual(result["label"], label)
            self.assertEqual(result["state"], state)
            self.assertIsNone(result["confidence"])
            self.assertTrue(result["unknown_rejection"])
        idle = next(f for f in self.random["frames"] if f["expected_label"] == "idle")
        self.assertIn("고장이라고 판단하지", self.classifier.classify(idle["rms_g"], idle["envelope_cv"])["detail"])

    def test_unknown_patterns_are_held_without_forcing_a_fault_or_normal(self):
        out = self.classifier.classify(.1, 100)
        self.assertEqual(out["label"], "unknown")
        self.assertEqual(out["state"], "unavailable")
        self.assertEqual(out["unknown_reason"], "outside_recorded_pattern_support")
        self.assertGreater(out["model_distance"], out["acceptance_radius"])
        self.assertFalse(out["forced_choice"])
        self.assertFalse(out["generator_fault_diagnosis"])
        self.assertIsNone(out["confidence"])

    def test_new_model_keeps_import_provenance_and_old_artifacts_intact(self):
        source = json.loads((ARTIFACT_DIR / "phone-pattern-calibrated-provenance.json").read_text())
        self.assertEqual(hashlib.sha256((ARTIFACT_DIR / MODEL_FILE).read_bytes()).hexdigest(), source["file_sha256"])
        self.assertTrue(source["file_bytes_unchanged"])
        self.assertTrue(source["repository_and_zip_all_40_files_identical"])
        self.assertEqual(self.classifier.catalog()["model_id"], MODEL_ID)
        self.assertEqual(self.classifier.catalog()["model"]["training_windows"], 32)
        self.assertEqual(self.classifier.catalog()["model"]["calibration_windows"], 16)
        self.assertEqual(self.classifier.catalog()["evaluation"]["model_id"], MODEL_ID)
        self.assertEqual(self.random["evaluation"]["rms_and_pattern"]["unknown"], 0)

    def test_invalid_values_rejected_without_fallback_to_normal(self):
        for invalid in (None, "0.04", True, -1, float("nan"), float("inf")):
            for kwargs in ({"rms_g": invalid, "envelope_cv": .1}, {"rms_g": .04, "envelope_cv": invalid}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.classifier.classify(**kwargs)

    def test_recording_path_is_allowlisted_and_returns_fresh_values(self):
        for bad in ("../model", "random.json", "unknown", None):
            with self.assertRaises(ValueError):
                load_recording(bad)
        data = load_recording("random")
        data["frames"][0]["rms_g"] = 999
        self.assertNotEqual(load_recording("random")["frames"][0]["rms_g"], 999)
        catalog = self.classifier.catalog()
        catalog["model"]["training_recordings"] = 999
        self.assertEqual(self.classifier.catalog()["model"]["training_recordings"], 1)


if __name__ == "__main__":
    unittest.main()
