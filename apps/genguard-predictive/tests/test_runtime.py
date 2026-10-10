"""Frozen real export, causal inputs, persistence, quality and replay lifecycle."""
from collections import defaultdict
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contract import FIELDS, HORIZONS, TARGETS, build_features
from runtime import Monitor, Predictor, crossing_time
from simulator import generate_run


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = defaultdict(list)
        with gzip.open(ROOT / "data/telemetry-test.jsonl.gz", "rt") as handle:
            for line in handle:
                row = json.loads(line)
                cls.runs[row["run_id"]].append(row)
        cls.normal = cls.runs["normal-40000"]

    def setUp(self):
        self.monitor = Monitor()

    def warm(self, monitor=None):
        monitor = monitor or self.monitor
        result = None
        for row in self.normal[:91]:
            result = monitor.update(row)
        return result

    def test_180_seconds_required_then_real_forecasts(self):
        for row in self.normal[:90]:
            out = self.monitor.update(row)
            self.assertEqual(out["state"], "collecting")
            self.assertEqual(out["forecast"], [])
        out = self.monitor.update(self.normal[90])
        self.assertEqual(out["history_seconds"], 180)
        self.assertEqual([f["ahead_s"] for f in out["forecast"]], HORIZONS)
        self.assertTrue(all(set(f["values"]) == set(TARGETS) for f in out["forecast"]))
        self.assertIsNone(out["stop_eta_s"])
        self.assertIsNone(out["failure_probability"])

    def test_scenario_future_labels_and_time_origin_do_not_feed_model(self):
        history = copy.deepcopy(self.normal[:91])
        altered = copy.deepcopy(history)
        for row in altered:
            row.update(scenario="oil_decline", intervention_s=-99999,
                       injection_onset_s=0, future_coolant_c=150, expected_state="urgent")
            row["timestamp_s"] += 100000
        self.assertEqual(build_features(history), build_features(altered))
        predictor = self.monitor.predictor
        self.assertEqual(predictor.forecast(history), predictor.forecast(altered))
        self.assertEqual(predictor.anomaly(history), predictor.anomaly(altered))
        clean, poisoned = Monitor(), Monitor()
        for a, b in zip(history, altered):
            ca, cb = clean.update(a), poisoned.update(b)
        self.assertEqual(ca["forecast"], cb["forecast"])
        self.assertEqual(ca["anomaly_score"], cb["anomaly_score"])

    def test_future_mutation_cannot_change_previous_output(self):
        all_rows = copy.deepcopy(self.normal)
        original = self.monitor.predictor.forecast(all_rows[:91])
        for row in all_rows[91:]:
            row["coolant_c"] = 160
            row["oil_pressure_kgf_cm2"] = 0.1
        self.assertEqual(original, self.monitor.predictor.forecast(all_rows[:91]))

    def test_single_spike_is_observation_but_sustained_deviation_alerts(self):
        self.warm()
        spike = dict(self.normal[91], coolant_c=self.normal[91]["coolant_c"] + 3)
        out = self.monitor.update(spike)
        self.assertGreater(out["anomaly_score"], out["anomaly_threshold"])
        self.assertFalse(any(a["source"] == "learned_baseline" for a in out["alerts"]))
        next_out = self.monitor.update(self.normal[92])
        self.assertFalse(any(a["source"] == "learned_baseline" for a in next_out["alerts"]))
        sustained = Monitor()
        self.warm(sustained)
        for row in self.normal[91:97]:
            out = sustained.update(dict(row, coolant_c=row["coolant_c"] + 3))
        self.assertTrue(any(a["source"] == "learned_baseline" for a in out["alerts"]))
        self.assertEqual(out["state"], "inspection")

    def test_duplicate_does_not_advance_history_or_learn(self):
        self.warm()
        before = list(self.monitor.history)
        timers = dict(self.monitor.timers)
        out = self.monitor.update(dict(self.normal[90], coolant_c=110))
        self.assertEqual(out["state"], "unknown")
        self.assertEqual(out["forecast"], [])
        self.assertEqual(before, list(self.monitor.history))
        self.assertEqual(timers, self.monitor.timers)

    def test_gap_restarts_history_and_fixed_alarm_persistence(self):
        self.warm()
        out = self.monitor.update(dict(self.normal[100], timestamp_s=220))
        self.assertEqual(out["state"], "collecting")
        self.assertEqual(out["history_seconds"], 0)
        monitor = Monitor()
        base = dict(self.normal[0], coolant_c=95)
        for t in (0, 2, 4, 6, 8):
            out = monitor.update(dict(base, timestamp_s=t))
        self.assertFalse(out["alerts"])
        out = monitor.update(dict(base, timestamp_s=30))
        self.assertFalse(out["alerts"], "A telemetry gap must not count as sustained evidence")

    def test_stale_and_future_clock_clear_forecasts(self):
        for now_s in (300., 100., float("nan")):
            monitor = Monitor()
            self.warm(monitor)
            out = monitor.update(self.normal[91], now_s=now_s)
            self.assertEqual(out["state"], "unknown")
            self.assertEqual(out["forecast"], [])
            self.assertEqual(len(monitor.history), 0)

    def test_invalid_channel_preserves_independent_current_urgent(self):
        row = dict(self.normal[0], coolant_c=101, oil_pressure_kgf_cm2=None)
        out = self.monitor.update(row)
        self.assertEqual(out["state"], "urgent")
        self.assertTrue(any(a["field"] == "coolant_c" and a["source"] == "fixed_rule" for a in out["alerts"]))
        self.assertEqual(out["forecast"], [])

    def test_wrong_equipment_profile_does_not_apply_10kw_thresholds(self):
        row = dict(self.normal[0], profile_id="another-equipment", coolant_c=101)
        out = self.monitor.update(row)
        self.assertEqual(out["state"], "unknown")
        self.assertEqual(out["alerts"], [])
        self.assertEqual(out["forecast"], [])

    def test_ood_clears_history_and_all_learned_persistence(self):
        self.warm()
        # Set timer state explicitly to isolate a quality interruption, not to fake inference.
        self.monitor.timers.update({"anomaly": 170., "forecast:0:inspection": 170.})
        out = self.monitor.update(dict(self.normal[91], ambient_c=55))
        self.assertEqual(out["state"], "unknown")
        self.assertFalse(out["forecast"])
        self.assertEqual(len(self.monitor.history), 0)
        self.assertFalse(any(k == "anomaly" or k.startswith("forecast:") for k in self.monitor.timers))
        out = self.monitor.update(self.normal[92])
        self.assertEqual(out["state"], "collecting")
        self.assertEqual(out["history_seconds"], 0)

    def test_model_failure_resets_all_learned_persistence(self):
        self.warm()
        self.monitor.timers.update({"anomaly": 170., "forecast:0:inspection": 170.})
        with patch.object(self.monitor.predictor, "forecast", side_effect=ValueError("invalid model output")):
            out = self.monitor.update(self.normal[91])
        self.assertEqual(out["state"], "unknown")
        self.assertFalse(out["forecast"])
        self.assertFalse(any(k == "anomaly" or k.startswith("forecast:") for k in self.monitor.timers))

    def test_phases_and_new_run_restart_history(self):
        for phase in ("warming", "stopped", "not_a_phase"):
            monitor = Monitor()
            self.warm(monitor)
            out = monitor.update(dict(self.normal[91], phase=phase))
            self.assertEqual(out["state"], "unknown")
            self.assertFalse(out["forecast"])
            self.assertEqual(len(monitor.history), 0)
        self.warm()
        out = self.monitor.update(dict(self.normal[0], run_id="different-session"))
        self.assertEqual(out["state"], "collecting")
        self.assertEqual(out["history_seconds"], 0)

    def test_crossings_only_interpolate_within_forecast_horizon(self):
        self.assertEqual(crossing_time([(0, 80), (60, 90), (180, 100)], 95, "high"), 120)
        self.assertIsNone(crossing_time([(0, 80), (180, 90)], 100, "high"))
        self.assertEqual(crossing_time([(0, 3), (60, 2), (180, 1)], 1.5, "low"), 120)
        self.assertEqual(crossing_time([(0, 100), (180, 101)], 100, "high"), 0)

    def test_exported_parameters_stay_frozen_during_full_run(self):
        path = ROOT / "models/telemetry.json"
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        serialized_model = json.dumps(self.monitor.predictor.model, sort_keys=True)
        arrays = [np.copy(w) for w, _, _ in self.monitor.predictor.layers]
        for row in generate_run("recovery", 89001):
            out = self.monitor.update(row)
        self.assertFalse(out["online_training"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file_hash)
        self.assertEqual(json.dumps(self.monitor.predictor.model, sort_keys=True), serialized_model)
        for before, (after, _, _) in zip(arrays, self.monitor.predictor.layers):
            np.testing.assert_array_equal(before, after)

    def test_actual_export_reproduces_reported_holdout_forecast_errors(self):
        errors = []
        predictor = self.monitor.predictor
        for rows in self.runs.values():
            for i in range(90, len(rows) - 90, 5):
                forecast = predictor.forecast(rows[i - 90:i + 1])
                errors.append([[forecast[hi]["values"][k] - rows[i + h // 2][k]
                                for k in TARGETS] for hi, h in enumerate(HORIZONS)])
        errors = np.asarray(errors)
        report = json.loads((ROOT / "reports/evaluation.json").read_text())
        self.assertEqual(len(errors), report["test_windows"])
        self.assertEqual(len(self.runs), report["test_runs"])
        for hi, horizon in enumerate(HORIZONS):
            for ki, key in enumerate(TARGETS):
                expected = report["per_horizon"]["learned_mlp"][str(horizon)][key]
                self.assertAlmostEqual(float(np.mean(np.abs(errors[:, hi, ki]))), expected["mae"], places=10)
                self.assertAlmostEqual(float(np.sqrt(np.mean(errors[:, hi, ki] ** 2))), expected["rmse"], places=10)


class ServerLifecycleTests(unittest.TestCase):
    def setUp(self):
        from server import State
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # Exercise actual state/worker code without starting threads or network access.
        with patch("server.threading.Thread.start"):
            self.state = State(recording_directory=directory.name)
        self.addCleanup(self.state.recorder.close)

    def test_eof_stops_and_does_not_automatically_loop_or_duplicate(self):
        state = self.state
        for row in state.rows[1:-1]:
            state.accept(row)
        state.position = len(state.rows) - 1
        state.clock = state.duration_s - 2
        state.playing = True
        with patch("server.time.monotonic", side_effect=[0., 1.]), patch("server.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                state.worker()
        self.assertFalse(state.playing)
        self.assertEqual(state.position, len(state.rows))
        self.assertEqual(state.latest["timestamp_s"], state.duration_s)
        before = copy.deepcopy(state.history)
        with patch("server.time.monotonic", side_effect=[0., 1.]), patch("server.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                state.worker()
        self.assertEqual(before, state.history)

    def test_invalid_batch_structure_is_rejected_before_mutating_state(self):
        first = dict(self.state.rows[0])
        second = dict(self.state.rows[1])
        second.pop("phase")
        before = self.state.snapshot()
        with self.assertRaises(ValueError):
            self.state.ingest({"samples": [first, second]})
        self.assertEqual(before, self.state.snapshot())

    def test_nonfinite_partial_input_cannot_poison_json_snapshot(self):
        sample = dict(self.state.rows[0], coolant_c=float("nan"), oil_pressure_kgf_cm2=0.5)
        before = self.state.snapshot()
        with self.assertRaises(ValueError):
            self.state.ingest({"sample": sample})
        self.assertEqual(before, self.state.snapshot())
        # A missing channel can be represented honestly as null; another valid
        # urgent channel must still be preserved by the monitoring layer.
        sample["coolant_c"] = None
        self.state.ingest({"sample": sample})
        snapshot = self.state.snapshot()
        json.dumps(snapshot, allow_nan=False)
        self.assertEqual(snapshot["analysis"]["state"], "urgent")
        self.assertIsNone(snapshot["latest"]["coolant_c"])


if __name__ == "__main__":
    unittest.main()
