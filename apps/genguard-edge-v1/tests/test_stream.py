import argparse
import json
import io
import math
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import numpy as np

from genguard.stream import (StreamSessionError, UINT32, WindowBuilder,
                             feature_values, read_controller_snapshot, run_stream, main)


def meta():
    return {"type": "meta", "odr_hz": 208, "accel_range_g": 4}


def packet(index, *, initial_us=0, initial_seq=0):
    stamp = round(index * 1_000_000 / 208)
    return {"type": "sample", "seq": (initial_seq + index) % UINT32,
            "t_us": (initial_us + stamp) % UINT32,
            "ax_g": 0.1 * math.sin(2 * math.pi * 20 * stamp / 1_000_000),
            "ay_g": 0.0, "az_g": 1.0}


def collect(builder, transform=lambda x, i: x, **kwargs):
    frames = []
    for i in range(418):
        sample = transform(packet(i, **kwargs), i)
        if sample is not None:
            frames.extend(builder.add_sample(sample))
    return frames


class WindowTests(unittest.TestCase):
    def builder(self):
        builder = WindowBuilder("GEN-1", "session-1", "mount-1")
        builder.set_meta(meta())
        return builder

    def test_two_second_vector_rms_removes_gravity(self):
        frames = collect(self.builder())
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame["elapsed_s"], 2)
        self.assertEqual(frame["seq"], 1)
        reading = frame["readings"]["vibration_rms_g"]
        self.assertEqual(reading["quality"], "ok")
        self.assertEqual(reading["source"], "live_imu")
        self.assertAlmostEqual(reading["value"], 0.1 / math.sqrt(2), places=5)
        self.assertLess(frame["readings"]["envelope_cv"]["value"], 0.05)

    def test_features_match_population_variance_and_per_bin_demean(self):
        times = np.arange(0, 2, 0.005)
        # Each bin has an unrelated DC offset. Per-bin demeaning must remove it.
        bins = np.floor(times / 0.1 + 1e-10).astype(int)
        signal = np.sin(2 * np.pi * 30 * times) * (1 + bins % 2)
        axes = np.column_stack((signal + bins * 2, times * 0, times * 0 + 9))
        rms, cv = feature_values(times.tolist(), axes.tolist(), 0)
        expected_bins = [np.sqrt(axes[bins == i].var(axis=0).sum()) for i in range(20)]
        self.assertAlmostEqual(rms, np.sqrt(axes.var(axis=0).sum()))
        self.assertAlmostEqual(cv, np.std(expected_bins) / np.mean(expected_bins))

    def test_clock_and_sequence_wrap_preserve_features(self):
        frames = collect(self.builder(), initial_us=UINT32 - 600_000,
                         initial_seq=UINT32 - 100)
        self.assertEqual(frames[0]["readings"]["vibration_rms_g"]["quality"], "ok")
        self.assertEqual(frames[0]["elapsed_s"], 2)

    def test_missing_sequence_invalidates_window(self):
        builder = self.builder()
        frames = collect(builder, lambda sample, i: None if i == 200 else sample)
        self.assertEqual(frames[0]["readings"]["vibration_rms_g"]["quality"], "invalid")
        self.assertIsNone(frames[0]["readings"]["vibration_rms_g"]["value"])
        self.assertIn("missing_sample_sequence", builder.last_quality_reasons)

    def test_saturation_invalidates_window(self):
        def mutate(sample, i):
            if i == 200:
                sample["ax_g"] = 3.999
            return sample
        frame = collect(self.builder(), mutate)[0]
        self.assertEqual(frame["readings"]["vibration_rms_g"]["quality"], "invalid")

    def test_metadata_is_required_not_inferred_from_values(self):
        builder = WindowBuilder("GEN-1", "session-1", "mount-1")
        frame = collect(builder)[0]
        self.assertEqual(frame["readings"]["envelope_cv"]["quality"], "invalid")

    def test_nonfinite_is_invalid_and_never_serialized_as_nan(self):
        def mutate(sample, i):
            if i == 30:
                sample["ax_g"] = float("nan")
            return sample
        frame = collect(self.builder(), mutate)[0]
        self.assertIsNone(frame["readings"]["vibration_rms_g"]["value"])
        json.dumps(frame, allow_nan=False)

    def test_irregular_mcu_time_invalidates_despite_sequential_ids(self):
        def mutate(sample, i):
            if i == 200:
                sample["t_us"] += 3000
            return sample
        builder = self.builder()
        frame = collect(builder, mutate)[0]
        self.assertEqual(frame["readings"]["vibration_rms_g"]["quality"], "invalid")
        self.assertIn("irregular_mcu_timing", builder.last_quality_reasons)

    def test_reset_and_duplicate_do_not_auto_resume(self):
        builder = self.builder()
        builder.add_sample(packet(100))
        with self.assertRaises(StreamSessionError):
            builder.add_sample(packet(0))
        builder = self.builder()
        builder.add_sample(packet(0))
        with self.assertRaises(StreamSessionError):
            builder.add_sample(packet(0))

    def test_clock_reset_does_not_look_like_rollover(self):
        builder = self.builder()
        builder.add_sample(packet(100))
        changed = packet(101)
        changed["t_us"] = 0
        with self.assertRaises(StreamSessionError):
            builder.add_sample(changed)

    def test_changed_range_requires_new_session(self):
        builder = self.builder()
        builder.add_sample(packet(0))
        with self.assertRaises(StreamSessionError):
            builder.set_meta({**meta(), "accel_range_g": 8})

    def test_invalid_metadata_cannot_hide_configuration_change(self):
        builder = self.builder()
        builder.add_sample(packet(0))
        builder.set_meta({"type": "meta"})
        with self.assertRaises(StreamSessionError):
            builder.set_meta({**meta(), "accel_range_g": 8})

    def test_gateway_read_error_increase_invalidates_window(self):
        builder = self.builder()
        builder.gateway_status({"read_error_count": 0})
        builder.gateway_status({"read_error_count": 1})
        frame = collect(builder)[0]
        self.assertEqual(frame["readings"]["envelope_cv"]["quality"], "invalid")

    def test_gateway_ready_false_stops_session(self):
        with self.assertRaises(StreamSessionError):
            self.builder().gateway_status({"sensor_ready": False})


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "snapshot.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, **updates):
        value = {"monotonic_s": 100.0, "phase": "running", "readings": {
            "oil_bar": {"value": 3, "unit": "bar", "source": "scenario",
                        "age_s": 1, "quality": "ok"}}}
        value.update(updates)
        self.path.write_text(json.dumps(value))

    def test_age_is_added_and_source_unit_preserved(self):
        self.write()
        phase, values = read_controller_snapshot(self.path, 102)
        self.assertEqual(phase, "running")
        self.assertEqual(values["oil_bar"]["age_s"], 3)
        self.assertEqual(values["oil_bar"]["source"], "scenario")
        self.assertEqual(values["oil_bar"]["unit"], "bar")

    def test_stale_and_future_clock_never_mark_normal(self):
        self.write()
        self.assertEqual(read_controller_snapshot(self.path, 110, max_age_s=5)[1]["oil_bar"]["quality"], "invalid")
        self.assertEqual(read_controller_snapshot(self.path, 99)[1]["oil_bar"]["quality"], "invalid")

    def test_missing_file_and_phase_fail_instead_of_inventing_running(self):
        with self.assertRaises(FileNotFoundError):
            read_controller_snapshot(self.path, 100)
        self.write(phase="unknown")
        with self.assertRaises(ValueError):
            read_controller_snapshot(self.path, 100)

    def test_snapshot_cannot_override_real_imu_readings(self):
        self.write(readings={"vibration_rms_g": {"value": 999}})
        self.assertEqual(read_controller_snapshot(self.path, 100)[1], {})

    def test_disconnect_does_not_reconnect_or_ingest_normal(self):
        self.write(monotonic_s=100)
        args = argparse.Namespace(session="s", installation="m", host="127.0.0.1",
                                  port=9877, controller_snapshot=str(self.path),
                                  max_controller_age_s=5, sensor_timeout_s=3)
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, value): pass
            def recv(self, size): return b""
        class Runtime:
            def ingest(self, frame): raise AssertionError("must not fabricate a normal result")
        with patch("genguard.stream.socket.create_connection", return_value=Connection()) as connect:
            with patch("genguard.stream.time.monotonic", return_value=101):
                with self.assertRaises(StreamSessionError):
                    run_stream(args, Runtime(), "GEN-1")
        self.assertEqual(connect.call_count, 1)


class RuntimeContractTests(unittest.TestCase):
    profile = Path(__file__).resolve().parents[1] / "profiles/fusion-live-template.json"

    def test_real_core_accepts_stream_frame_and_rejects_mixed_provenance(self):
        from genguard.core import EdgeRuntime
        with tempfile.TemporaryDirectory() as directory:
            runtime = EdgeRuntime(Path(directory) / "state.db", self.profile)
            try:
                runtime.start_session("s", "m")
                builder = WindowBuilder("GEN-FIELD-001", "s", "m")
                builder.set_meta(meta())
                frame = collect(builder)[0]
                values = {"load_pct": (50, "%"), "ambient_c": (25, "degC"),
                          "engine_rpm": (1800, "rpm"), "coolant_c": (80, "degC"),
                          "oil_bar": (3, "bar"), "voltage_v": (240, "V"),
                          "current_a": (15, "A")}
                frame["readings"].update({key: {"value": value, "unit": unit,
                    "source": "live_controller", "age_s": 0, "quality": "ok"}
                    for key, (value, unit) in values.items()})
                first = runtime.ingest(frame)
                self.assertIn("baseline_collecting", json.dumps(first))
                frame["seq"], frame["elapsed_s"] = 2, 4
                frame["readings"]["oil_bar"]["source"] = "scenario"
                second = runtime.ingest(frame)
                self.assertIn("source_mismatch:oil_bar", json.dumps(second))
            finally:
                runtime.close()

    def test_keyboard_interrupt_closes_actual_sqlite_session(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.db"
            args = ["--db", str(database), "--profile", str(self.profile),
                    "--session", "s", "--installation", "m",
                    "--controller-snapshot", str(Path(directory) / "unused.json")]
            with patch("genguard.stream.run_stream", side_effect=KeyboardInterrupt):
                with patch("sys.stderr", new=io.StringIO()):
                    self.assertEqual(main(args), 0)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT status FROM sessions").fetchone()[0], "closed")


if __name__ == "__main__":
    unittest.main()
