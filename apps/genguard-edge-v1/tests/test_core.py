"""Independent synthetic contract checks; no real generator accuracy claim.

The measurements below are deliberately invented to test data boundaries and
post-run model lifecycle. Separate run IDs prevent window-level split leakage,
but these deterministic fixtures are not independent field experiments.
"""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from genguard.core import EdgeRuntime, load_profile
from genguard.model import fit_phase, predict, score_phase


ROOT = Path(__file__).resolve().parents[1]


def profile():
    return json.loads((ROOT / "profiles/fusion-demo.json").read_text())


def frame(session, seq=1, *, installation="fixture-v1", source="scenario", seed=0):
    """Invented smooth joint readings; values are not OEM normal limits."""
    elapsed = seq * 2.0
    load = 50 + 15 * math.sin(seq / 9)
    ambient = 25 + 0.3 * math.cos(seq / 11)
    noise = math.sin(seq * 1.731 + seed * 0.379)
    values = {
        "load_pct": load,
        "ambient_c": ambient,
        "engine_rpm": 1800 + 0.03 * load + 0.05 * noise,
        "coolant_c": 60 + 0.25 * load + 0.015 * elapsed + 0.04 * noise,
        "oil_bar": 3.8 - 0.005 * load - 0.0005 * elapsed + 0.001 * noise,
        "voltage_v": 240 - 0.005 * load + 0.04 * noise,
        "current_a": 0.3 * load + 0.002 * noise,
        "vibration_rms_g": math.exp(-4 + 0.007 * load + 0.0002 * elapsed + 0.008 * noise),
        "envelope_cv": 0.2 + 0.0001 * load + 0.001 * noise,
    }
    p = profile()
    return {"schema_version": "genguard.edge.window/1", "asset_id": p["asset_id"],
            "session_id": session, "installation_id": installation,
            "seq": seq, "elapsed_s": elapsed, "duration_s": 2.0,
            "phase": "running", "measured_at": None,
            "readings": {key: {"value": val, "unit": p["fields"][key]["unit"],
                               "source": source, "age_s": 0.0, "quality": "ok"}
                         for key, val in values.items()}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.sqlite"
        self.runtime = EdgeRuntime(self.db_path, profile())
        self.addCleanup(self.runtime.close)

    def add_run(self, sid, *, seed=0, review="normal", installation="fixture-v1", transform=None, count=60):
        self.runtime.start_session(sid, installation)
        for seq in range(1, count + 1):
            item = frame(sid, seq, installation=installation, seed=seed)
            if transform:
                transform(item)
            self.runtime.ingest(item)
        self.runtime.close_session(sid)
        if review is not None:
            self.runtime.review_session(sid, review, "synthetic-test", "인공 fixture의 정상/이상 입력 표지")

    def history(self, prefix="", validation_transform=None):
        ids = [prefix + key for key in ("fit1", "fit2", "cal", "val")]
        for i, sid in enumerate(ids):
            self.add_run(sid, seed=i, transform=validation_transform if i == 3 else None)
        return ids

    def candidate(self, ids=None):
        ids = self.history() if ids is None else ids
        return self.runtime.train_candidate(ids[:2], ids[2:3], ids[3:4])

    def active(self):
        candidate = self.candidate()
        self.assertTrue(candidate["validation"]["passed"])
        self.runtime.activate(candidate["model_id"])
        return candidate

    def test_first_runs_collect_without_claiming_normality(self):
        self.runtime.start_session("first", "fixture-v1")
        result = self.runtime.ingest(frame("first"))
        self.assertEqual(result["state"], "baseline_collecting")
        self.assertIsNone(result["model_id"])
        self.assertIsNone(result["rul"])
        self.assertIsNone(result["fault_diagnosis"])

    def test_post_run_training_uses_disjoint_runs_and_next_run_model(self):
        candidate = self.active()
        self.assertEqual(candidate["fit_sessions"], ["fit1", "fit2"])
        self.assertEqual(candidate["calibration_sessions"], ["cal"])
        self.assertEqual(candidate["validation_sessions"], ["val"])
        self.runtime.start_session("next", "fixture-v1")
        result = self.runtime.ingest(frame("next", seed=9))
        self.assertEqual(result["model_id"], candidate["model_id"])
        self.assertEqual(result["state"], "within_baseline")
        self.assertTrue(result["is_demonstration"])

    def test_missing_review_abnormal_and_excluded_runs_cannot_train(self):
        ids = self.history()
        for status in ("abnormal", "exclude"):
            self.runtime.review_session("fit1", status, "tester", "explicit exclusion")
            with self.assertRaisesRegex(ValueError, "normal-reviewed"):
                self.candidate(ids)
        self.add_run("unreviewed", review=None)
        with self.assertRaisesRegex(ValueError, "normal-reviewed"):
            self.runtime.train_candidate(["unreviewed", "fit2"], ["cal"], ["val"])

    def test_review_needs_closed_run_reviewer_and_evidence(self):
        self.runtime.start_session("open", "fixture-v1")
        with self.assertRaisesRegex(ValueError, "closed"):
            self.runtime.review_session("open", "normal", "test", "note")
        self.runtime.close_session("open")
        for reviewer, note in [("", "note"), ("tester", " ")]:
            with self.assertRaisesRegex(ValueError, "review"):
                self.runtime.review_session("open", "normal", reviewer, note)

    def test_split_overlap_or_duplicate_run_is_rejected(self):
        cases = [(["a", "b"], ["b"], ["c"]),
                 (["a", "a"], ["b"], ["c"]),
                 (["a", "b"], ["c"], ["c"])]
        for fit, cal, val in cases:
            with self.subTest(fit=fit, cal=cal, val=val), self.assertRaisesRegex(ValueError, "disjoint"):
                self.runtime.train_candidate(fit, cal, val)

    def test_insufficient_split_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Need >=2"):
            self.runtime.train_candidate(["one"], ["cal"], ["val"])

    def test_bad_validation_is_saved_but_cannot_be_activated(self):
        def hot(item):
            item["readings"]["coolant_c"]["value"] += 15
        candidate = self.candidate(self.history(validation_transform=hot))
        self.assertFalse(candidate["validation"]["passed"])
        self.assertGreater(candidate["validation"]["runs"][0]["change_fraction"], 0.9)
        with self.assertRaisesRegex(ValueError, "failed"):
            self.runtime.activate(candidate["model_id"])
        self.assertIsNone(self.runtime.setting("active_model"))

    def test_review_retraction_invalidates_candidate_activation(self):
        candidate = self.candidate()
        self.runtime.review_session("fit1", "abnormal", "tester", "Inspection later discovered an issue")
        with self.assertRaises(ValueError):
            self.runtime.activate(candidate["model_id"])
        self.assertIsNone(self.runtime.setting("active_model"))

    def test_training_and_activation_rejected_during_run(self):
        candidate = self.candidate()
        self.runtime.start_session("ongoing", "fixture-v1")
        with self.assertRaisesRegex(ValueError, "Close the active run"):
            self.runtime.train_candidate(["fit1", "fit2"], ["cal"], ["val"])
        with self.assertRaisesRegex(ValueError, "between runs"):
            self.runtime.activate(candidate["model_id"])
        self.assertIsNone(self.runtime.setting("active_model"))

    def test_second_connection_cannot_start_run_during_training(self):
        ids = self.history()
        other = EdgeRuntime(self.db_path, profile())
        self.addCleanup(other.close)
        attempted = []

        def interleaved_fit(frames, p):
            attempted.append(True)
            with self.assertRaisesRegex(ValueError, "busy"):
                other.start_session("racing-start", "fixture-v1")
            return fit_phase(frames, p)

        with patch("genguard.core.fit_phase", side_effect=interleaved_fit):
            candidate = self.candidate(ids)
        self.assertTrue(attempted)
        self.assertTrue(candidate["validation"]["passed"])
        self.assertFalse(any(s["id"] == "racing-start" for s in other.sessions()))
        other.start_session("after-training", "fixture-v1")
        self.assertEqual(other.sessions()[-1]["status"], "open")

    def test_lifecycle_lock_is_released_after_failed_training(self):
        with self.assertRaises(ValueError):
            self.runtime.train_candidate(["missing-a", "missing-b"], ["missing-c"], ["missing-d"])
        self.runtime.start_session("after-failure", "fixture-v1")
        self.assertEqual(self.runtime.ingest(frame("after-failure"))["state"], "baseline_collecting")

    def test_bad_unit_missing_stale_and_quality_produce_unknown(self):
        self.active()
        self.runtime.start_session("quality", "fixture-v1")
        changes = [
            ("unit_mismatch:oil_bar", lambda f: f["readings"]["oil_bar"].update(unit="psi")),
            ("missing:oil_bar", lambda f: f["readings"].pop("oil_bar")),
            ("stale_or_unknown_age:oil_bar", lambda f: f["readings"]["oil_bar"].update(age_s=100)),
            ("quality:oil_bar", lambda f: f["readings"]["oil_bar"].update(quality="missing", value=None)),
        ]
        for seq, (flag, mutate) in enumerate(changes, 1):
            item = frame("quality", seq)
            mutate(item)
            result = self.runtime.ingest(item)
            self.assertEqual(result["state"], "unknown")
            self.assertIn(flag, result["quality_flags"])
            self.assertFalse(result["persistent_change"])

    def test_malformed_reading_has_controlled_failure_without_partial_write(self):
        self.runtime.start_session("malformed", "fixture-v1")
        item = frame("malformed")
        item["readings"]["oil_bar"] = 3
        try:
            result = self.runtime.ingest(item)
        except ValueError:
            self.assertEqual(len(self.runtime.export_results()), 0)
        else:
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(len(self.runtime.export_results()), 1)

    def test_demo_live_sources_never_silently_mix(self):
        self.runtime.start_session("mixed", "fixture-v1")
        item = frame("mixed")
        item["readings"]["vibration_rms_g"]["source"] = "live_imu"
        result = self.runtime.ingest(item)
        self.assertEqual(result["state"], "unknown")
        self.assertIn("source_mismatch:vibration_rms_g", result["quality_flags"])
        live = profile()
        live["data_domain"] = "live"
        with self.assertRaisesRegex(ValueError, "another profile"):
            EdgeRuntime(self.db_path, live)
        other = EdgeRuntime(Path(self.tmp.name) / "live.sqlite", live)
        try:
            other.start_session("scenario-into-live", "fixture-v1")
            result = other.ingest(frame("scenario-into-live"))
            self.assertEqual(result["state"], "unknown")
            self.assertIn("source_mismatch:oil_bar", result["quality_flags"])
        finally:
            other.close()

    def test_unseen_context_is_unknown_not_fault_or_normal(self):
        self.active()
        self.runtime.start_session("unseen", "fixture-v1")
        item = frame("unseen")
        item["readings"]["load_pct"]["value"] = 99
        result = self.runtime.ingest(item)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["current_comparison"]["reason"], "unseen_operating_context")
        self.assertEqual(result["checks"], [])

    def test_startup_warmup_and_stopped_are_not_judged_by_running_model(self):
        self.active()
        self.runtime.start_session("phases", "fixture-v1")
        for seq, phase in enumerate(("starting", "warming_up", "stopped"), 1):
            item = frame("phases", seq)
            item["phase"] = phase
            result = self.runtime.ingest(item)
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["current_comparison"]["reason"], "phase_not_configured")

    def test_collection_clock_is_not_mistaken_for_engine_age(self):
        self.active()
        self.runtime.start_session("collection-clock", "fixture-v1")
        early = frame("collection-clock")
        first = self.runtime.ingest(early)
        late = copy.deepcopy(early)
        late["seq"], late["elapsed_s"] = 2, 3600.0
        later = self.runtime.ingest(late)
        self.assertEqual(first["current_comparison"], later["current_comparison"])
        self.assertEqual(later["state"], "within_baseline")

    def test_wrong_asset_or_schema_is_rejected_before_storage(self):
        self.runtime.start_session("identity", "fixture-v1")
        for field, value in (("asset_id", "OTHER-ASSET"), ("schema_version", "genguard.edge.window/999")):
            item = frame("identity")
            item[field] = value
            with self.assertRaisesRegex(ValueError, "Schema/asset mismatch"):
                self.runtime.ingest(item)
        self.assertEqual(self.runtime.export_results(), [])

    def test_failed_cli_import_cannot_close_an_existing_live_run(self):
        from genguard.__main__ import main
        self.runtime.start_session("existing", "fixture-v1")
        source = Path(self.tmp.name) / "duplicate.jsonl"
        source.write_text(json.dumps(frame("existing")) + "\n")
        profile_file = Path(self.tmp.name) / "profile.json"
        profile_file.write_text(json.dumps(profile()))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["--db", str(self.db_path), "--profile", str(profile_file),
                         "import-run", "--input", str(source)])
        self.assertEqual(code, 2)
        self.assertEqual(self.runtime.sessions()[0]["status"], "open")
        self.assertEqual(self.runtime.ingest(frame("existing"))["state"], "baseline_collecting")

    def test_hybrid_demo_preserves_real_imu_and_scenario_source_boundaries(self):
        p = json.loads((ROOT / "profiles/fusion-hybrid-demo.json").read_text())
        hybrid = EdgeRuntime(Path(self.tmp.name) / "hybrid.sqlite", p)
        self.addCleanup(hybrid.close)
        hybrid.start_session("hybrid", "fixture-v1")
        good = frame("hybrid")
        good["asset_id"] = p["asset_id"]
        for key in ("vibration_rms_g", "envelope_cv"):
            good["readings"][key]["source"] = "live_imu"
        result = hybrid.ingest(good)
        self.assertEqual(result["state"], "baseline_collecting")
        self.assertTrue(result["is_demonstration"])
        self.assertEqual(result["data_domain"], "hybrid_demo")
        self.assertEqual(result["input_sources"]["vibration_rms_g"], "live_imu")
        self.assertEqual(result["input_sources"]["coolant_c"], "scenario")
        for seq, key, wrong_source in ((2, "vibration_rms_g", "scenario"), (3, "coolant_c", "live_imu")):
            bad = copy.deepcopy(good)
            bad["seq"], bad["elapsed_s"] = seq, seq * 2
            bad["readings"][key]["source"] = wrong_source
            result = hybrid.ingest(bad)
            self.assertEqual(result["state"], "unknown")
            self.assertIn("channel_source_mismatch:" + key, result["quality_flags"])

    def test_installations_cannot_mix_during_run_or_training(self):
        ids = self.history()
        self.add_run("new-mount", installation="fixture-v2")
        with self.assertRaisesRegex(ValueError, "installations"):
            self.runtime.train_candidate(["fit1", "new-mount"], ["cal"], ["val"])
        with self.assertRaisesRegex(ValueError, "Installation mismatch"):
            self.runtime.train_candidate(ids[:2], ["new-mount"], ["val"])
        self.runtime.start_session("current", "fixture-v1")
        with self.assertRaisesRegex(ValueError, "Installation changed"):
            self.runtime.ingest(frame("current", installation="fixture-v2"))

    def test_new_mount_does_not_inherit_previous_model(self):
        self.active()
        self.runtime.start_session("new-mount", "fixture-v2")
        result = self.runtime.ingest(frame("new-mount", installation="fixture-v2"))
        self.assertEqual(result["state"], "baseline_collecting")
        self.assertIsNone(result["model_id"])
        self.assertIsNone(result["reference_model_id"])

    def test_duplicate_is_idempotent_conflict_and_time_reversal_are_rejected(self):
        self.runtime.start_session("repeat", "fixture-v1")
        item = frame("repeat")
        first = self.runtime.ingest(item)
        self.assertEqual(first, self.runtime.ingest(copy.deepcopy(item)))
        self.assertEqual(len(self.runtime.export_results()), 1)
        conflict = copy.deepcopy(item)
        conflict["readings"]["oil_bar"]["value"] += 0.1
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.runtime.ingest(conflict)
        reversed_time = frame("repeat", 2)
        reversed_time["elapsed_s"] = 1
        with self.assertRaisesRegex(ValueError, "increase"):
            self.runtime.ingest(reversed_time)
        self.assertEqual(len(self.runtime.export_results()), 1)
        self.runtime.close_session("repeat")
        self.assertEqual(first, self.runtime.ingest(item))

    def test_sqlite_reopen_preserves_records_and_model(self):
        candidate = self.active()
        self.runtime.start_session("reopen", "fixture-v1")
        result = self.runtime.ingest(frame("reopen"))
        second = EdgeRuntime(self.db_path, profile())
        try:
            self.assertEqual(second.setting("active_model"), candidate["model_id"])
            self.assertEqual(second.ingest(frame("reopen")), result)
        finally:
            second.close()

    def test_initial_model_is_preserved_after_latest_model_activation(self):
        initial = self.active()
        latest = self.candidate(self.history("new-"))
        self.runtime.activate(latest["model_id"])
        self.assertNotEqual(initial["model_id"], latest["model_id"])
        self.assertEqual(self.runtime.setting("active_model"), latest["model_id"])
        self.assertEqual(self.runtime.setting("reference_model"), initial["model_id"])
        self.assertEqual(self.runtime.model(initial["model_id"]), initial)
        self.runtime.start_session("both", "fixture-v1")
        result = self.runtime.ingest(frame("both"))
        self.assertEqual(result["model_id"], latest["model_id"])
        self.assertEqual(result["reference_model_id"], initial["model_id"])
        self.assertIsNotNone(result["initial_comparison"])

    def test_active_run_stops_using_model_when_initial_review_is_retracted(self):
        self.active()
        latest = self.candidate(self.history("latest-"))
        self.runtime.activate(latest["model_id"])
        self.runtime.start_session("review-change", "fixture-v1")
        first = self.runtime.ingest(frame("review-change"))
        self.assertEqual(first["state"], "within_baseline")
        self.runtime.review_session("fit1", "abnormal", "tester", "Old baseline later found suspect")
        second = self.runtime.ingest(frame("review-change", 2))
        self.assertEqual(second["state"], "unknown")
        self.assertEqual(second["current_comparison"]["reason"], "training_review_retracted")
        self.assertEqual(second["checks"], [])

    def test_three_contiguous_changes_raise_inspection_and_alert_stays_open(self):
        self.active()
        self.runtime.start_session("change", "fixture-v1")
        results = []
        for seq in range(1, 4):
            item = frame("change", seq)
            item["readings"]["coolant_c"]["value"] += 15
            item["readings"]["vibration_rms_g"]["value"] *= 3
            results.append(self.runtime.ingest(item))
        self.assertEqual([r["state"] for r in results],
                         ["change_observed", "change_observed", "inspection_suggested"])
        self.assertTrue(results[-1]["persistent_change"])
        self.assertGreater(len(results[-1]["checks"]), 0)
        recovered = self.runtime.ingest(frame("change", 4))
        self.assertEqual(recovered["state"], "within_baseline")
        self.assertTrue(recovered["open_alert_in_session"])

    def test_missing_window_breaks_persistent_change_streak(self):
        self.active()
        self.runtime.start_session("gap", "fixture-v1")
        for seq in (1, 2, 4):
            item = frame("gap", seq)
            item["readings"]["coolant_c"]["value"] += 15
            result = self.runtime.ingest(item)
        self.assertEqual(result["change_streak"], 1)
        self.assertFalse(result["persistent_change"])

    def test_too_many_invalid_windows_and_short_history_cannot_train(self):
        def missing(item):
            if item["seq"] % 2 == 0:
                item["readings"]["oil_bar"]["age_s"] = 99
        self.add_run("bad", transform=missing)
        self.add_run("short", count=10)
        with self.assertRaisesRegex(ValueError, "invalid/missing"):
            self.runtime.training_frames(["bad"])
        with self.assertRaisesRegex(ValueError, "Not enough"):
            self.runtime.training_frames(["short"])


class MultivariateRelationshipTests(unittest.TestCase):
    def test_joint_relationship_change_can_be_detected_inside_marginal_ranges(self):
        """Synthetic correlation reversal, not a generator fault experiment."""
        p = profile()
        p["targets"] = ["coolant_c", "oil_bar"]
        p["context"] = ["load_pct"]
        p["fields"]["coolant_c"]["scale_floor"] = 0.001
        p["fields"]["oil_bar"]["scale_floor"] = 0.001
        samples = []
        for seq in range(1, 241):
            item = frame("correlated", seq)
            shared = math.sin(seq * 1.731)
            item["readings"]["load_pct"]["value"] = 50
            item["readings"]["coolant_c"]["value"] = 80 + shared
            item["readings"]["oil_bar"]["value"] = 3 + 0.1 * shared
            samples.append(item)
        model = fit_phase(samples, p)
        model["threshold"] = 2.0
        sample = frame("test")
        sample["readings"]["load_pct"]["value"] = 50
        expected = np.array(model["weights"])[0] + model["residual_mean"]
        scale = np.array(model["residual_scale"])
        for key, value in zip(p["targets"], expected + 1.1 * scale):
            sample["readings"][key]["value"] = float(value)
        normal = score_phase(sample, p, model)
        sample["readings"]["oil_bar"]["value"] = float(expected[1] - 1.1 * scale[1])
        changed = score_phase(sample, p, model)
        self.assertLess(normal["score"], model["threshold"])
        self.assertGreater(changed["score"], model["threshold"])
        self.assertTrue(all(abs(e["standardized_residual"]) < 2 for e in changed["evidence"]))
        self.assertEqual(predict(sample, p, {"phases": {"running": model}})["state"], "change")


class ProfileValidationTests(unittest.TestCase):
    def test_invalid_threshold_counts_and_tolerance_fail_before_running(self):
        cases = [
            ("minimum_score_threshold", 0),
            ("persistence_windows", 0),
            ("max_rows_per_session", 0),
            ("min_rows_per_session", 0),
            ("max_invalid_fraction", 1.2),
        ]
        for name, value in cases:
            p = profile()
            p["model"][name] = value
            with self.subTest(parameter=name), self.assertRaises(ValueError):
                load_profile(p)
        p = profile()
        p["context_tolerance"]["load_pct"] = -5
        with self.assertRaises(ValueError):
            load_profile(p)

    def test_hybrid_source_constraints_cannot_be_accidentally_omitted(self):
        for remove in (False, True):
            p = json.loads((ROOT / "profiles/fusion-hybrid-demo.json").read_text())
            if remove:
                p["fields"]["vibration_rms_g"].pop("allowed_sources")
            else:
                p["fields"]["vibration_rms_g"]["allowed_sources"] = []
            with self.subTest(remove=remove), self.assertRaises(ValueError):
                load_profile(p)


if __name__ == "__main__":
    unittest.main()
