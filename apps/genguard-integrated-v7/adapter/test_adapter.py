import argparse
import csv
import io
import json
import math
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from .features import StreamSessionError, UINT32, feature_values
from .imu_to_api import FeatureWindows, Publisher, csv_samples, run_live, run_replay


def sample(i, *, start_seq=0, start_us=0):
    stamp = round(i * 1_000_000 / 208)
    return {"type": "sample", "seq": (start_seq + i) % UINT32,
            "t_us": (start_us + stamp) % UINT32,
            "ax_g": 0.1 * math.sin(2 * math.pi * 20 * stamp / 1_000_000),
            "ay_g": 0.0, "az_g": 1.0}


def args(**updates):
    values = dict(installation="mount-test", gateway_host="127.0.0.1", gateway_port=9877,
                  sensor_timeout_s=3.0, csv=None, recorded_odr_hz=208.0,
                  recorded_range_g=4.0)
    return argparse.Namespace(**(values | updates))


class MemoryPublisher:
    def __init__(self):
        self.packets = []

    def publish(self, packet):
        self.packets.append(packet)
        return {"id": f'{packet["stream_id"]}:{packet["window_seq"]}',
                "accepted": True, "duplicate": False}


class FakeConnection:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, value): pass
    def recv(self, size):
        value = next(self.chunks, b"")
        if isinstance(value, BaseException):
            raise value
        return value


def encoded(packet):
    return (json.dumps(packet) + "\n").encode()


class FeatureTests(unittest.TestCase):
    def builder(self):
        builder = FeatureWindows("mount-test", "imu-test")
        builder.set_meta({"odr_hz": 208, "accel_range_g": 4})
        return builder

    def collect(self, builder, transform=lambda packet, index: packet, **kwargs):
        frames = []
        for i in range(418):
            value = transform(sample(i, **kwargs), i)
            if value is not None:
                frames.extend(builder.add_sample(value))
        return frames

    def test_rms_removes_gravity_and_packet_preserves_live_source(self):
        builder = self.builder()
        frame = self.collect(builder)[0]
        packet = builder.as_packet(frame, source="live_imu", measured_at=100.0)
        self.assertEqual(packet["schema_version"], "genguard.imu.window/1")
        self.assertEqual(packet["source"], "live_imu")
        self.assertAlmostEqual(packet["features"]["vibration_rms_g"], 0.1 / math.sqrt(2), places=5)
        self.assertLess(packet["features"]["envelope_cv"], 0.05)
        self.assertEqual(packet["sample_count"], 416)
        self.assertAlmostEqual(packet["sample_rate_hz"], 208, places=3)
        self.assertTrue(packet["quality"]["valid"])

    def test_per_bin_demeaning_matches_original_definition(self):
        t = np.arange(0, 2, .005)
        bins = np.floor(t / .1 + 1e-10).astype(int)
        axes = np.column_stack((np.sin(2 * np.pi * 30 * t) * (1 + bins % 2) + 2 * bins,
                                t * 0, t * 0 + 1))
        rms, cv = feature_values(t.tolist(), axes.tolist(), 0)
        envelope = [np.sqrt(axes[bins == i].var(axis=0).sum()) for i in range(20)]
        self.assertAlmostEqual(rms, np.sqrt(axes.var(axis=0).sum()))
        self.assertAlmostEqual(cv, np.std(envelope) / np.mean(envelope))

    def test_missing_sequence_is_null_not_zero(self):
        builder = self.builder()
        frame = self.collect(builder, lambda packet, i: None if i == 100 else packet)[0]
        packet = builder.as_packet(frame, source="live_imu", measured_at=100.0)
        self.assertFalse(packet["quality"]["valid"])
        self.assertIn("missing_sample_sequence", packet["quality"]["flags"])
        self.assertIsNone(packet["features"]["vibration_rms_g"])
        json.dumps(packet, allow_nan=False)

    def test_clipping_is_invalid(self):
        builder = self.builder()
        frame = self.collect(builder, lambda packet, i: {**packet, "ax_g": 3.99} if i == 100 else packet)[0]
        packet = builder.as_packet(frame, source="live_imu", measured_at=100)
        self.assertFalse(packet["quality"]["valid"])
        self.assertIsNone(packet["features"]["envelope_cv"])

    def test_uint32_rollover_is_preserved(self):
        builder = self.builder()
        frame = self.collect(builder, start_seq=UINT32 - 50, start_us=UINT32 - 700_000)[0]
        self.assertEqual(frame["readings"]["vibration_rms_g"]["quality"], "ok")

    def test_clock_or_sequence_reset_requires_new_stream(self):
        builder = self.builder()
        builder.add_sample(sample(40))
        with self.assertRaises(StreamSessionError):
            builder.add_sample(sample(0))

    def test_meta_change_requires_new_stream(self):
        builder = self.builder()
        builder.add_sample(sample(0))
        with self.assertRaises(StreamSessionError):
            builder.set_meta({"odr_hz": 416, "accel_range_g": 4})


class TransportTests(unittest.TestCase):
    def test_live_transport_publishes_features_then_stops_on_disconnect(self):
        chunks = [encoded({"type": "meta", "odr_hz": 208, "accel_range_g": 4})]
        chunks += [encoded(sample(i)) for i in range(418)]
        publisher = MemoryPublisher()
        with patch("adapter.imu_to_api.socket.create_connection", return_value=FakeConnection(chunks)), \
             patch("adapter.imu_to_api.time.monotonic", return_value=10), \
             patch("adapter.imu_to_api.time.time", return_value=100), \
             patch("sys.stdout", new=io.StringIO()):
            with self.assertRaisesRegex(StreamSessionError, "disconnected"):
                run_live(args(), publisher)
        self.assertEqual(len(publisher.packets), 1)
        self.assertEqual(publisher.packets[0]["source"], "live_imu")
        self.assertEqual(publisher.packets[0]["measured_at"], "1970-01-01T00:01:42.000Z")

    def test_no_sample_timeout_does_not_publish_a_healthy_window(self):
        publisher = MemoryPublisher()
        with patch("adapter.imu_to_api.socket.create_connection", return_value=FakeConnection([socket.timeout()])), \
             patch("adapter.imu_to_api.time.monotonic", side_effect=[10, 14]), \
             patch("sys.stdout", new=io.StringIO()):
            with self.assertRaisesRegex(StreamSessionError, "no fresh"):
                run_live(args(), publisher)
        self.assertEqual(publisher.packets, [])

    def test_gateway_error_does_not_auto_reconnect(self):
        publisher = MemoryPublisher()
        with patch("adapter.imu_to_api.socket.create_connection",
                   return_value=FakeConnection([encoded({"type": "error", "message": "sensor missing"})])) as connect, \
             patch("sys.stdout", new=io.StringIO()):
            with self.assertRaisesRegex(StreamSessionError, "gateway reported"):
                run_live(args(), publisher)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(publisher.packets, [])

    def test_backlog_cannot_acquire_a_new_fresh_timestamp(self):
        chunks = [encoded({"type": "meta", "odr_hz": 208, "accel_range_g": 4})]
        chunks += [encoded(sample(i)) for i in range(418)]
        publisher = MemoryPublisher()
        with patch("adapter.imu_to_api.socket.create_connection", return_value=FakeConnection(chunks)), \
             patch("adapter.imu_to_api.time.monotonic", return_value=10), \
             patch("adapter.imu_to_api.time.time", side_effect=[100, 106]), \
             patch("sys.stdout", new=io.StringIO()):
            with self.assertRaisesRegex(StreamSessionError, "delayed"):
                run_live(args(), publisher)
        self.assertEqual(publisher.packets, [])

    def test_replay_is_recorded_and_wall_paced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.csv"
            columns = ["seq", "t_us", "ax_g", "ay_g", "az_g"]
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(sample(i) for i in range(834))
            clock = [0.0]
            sleeps = []
            def sleep(seconds):
                sleeps.append(seconds)
                clock[0] += seconds
            publisher = MemoryPublisher()
            with patch("sys.stdout", new=io.StringIO()):
                run_replay(args(csv=str(path)), publisher, clock=lambda: clock[0], sleeper=sleep,
                           wall_clock=lambda: 100 + clock[0])
            self.assertEqual(sleeps, [2.0, 2.0])
            self.assertEqual([p["source"] for p in publisher.packets], ["recorded_imu", "recorded_imu"])
            self.assertEqual([p["window_seq"] for p in publisher.packets], [1, 2])
            self.assertTrue(publisher.packets[0]["stream_id"].startswith("replay-"))

    def test_csv_missing_units_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.csv"
            path.write_text("seq,t_us,ax,ay,az\n1,100,0,0,1\n")
            with self.assertRaisesRegex(ValueError, "must already be in g"):
                list(csv_samples(path))

    def test_api_url_rejects_embedded_credentials_or_paths(self):
        for url in ("http://secret@localhost:9879", "http://localhost:9879/api", "http://localhost:9879?token=abc"):
            with self.assertRaises(ValueError):
                Publisher(url)
        with self.assertRaisesRegex(ValueError, "printable ASCII") as caught:
            Publisher("http://127.0.0.1:9879", "SECRET\nTOKEN")
        self.assertNotIn("SECRET", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
