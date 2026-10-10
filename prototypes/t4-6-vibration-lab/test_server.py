"""Synthetic unit fixtures only; these tests never open USB or a network port."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import Lab


class LabTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-imu-test-')
        self.addCleanup(self.temp.cleanup)
        self.lab = Lab(self.temp.name)
        self.addCleanup(self.lab.disconnect)
        self.clock = patch('server.time.monotonic', return_value=100.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        # Simulates reader state; this is not physical-device evidence.
        self.lab.connected = True
        self.lab.port = 'synthetic-unit-fixture'

    def packet(self, seq=0, t_us=1000, **values):
        packet = dict(type='sample', seq=seq, t_us=t_us,
                      ax_g=0.0, ay_g=0.0, az_g=1.0,
                      gx_dps=0.0, gy_dps=0.0, gz_dps=0.0)
        packet.update(values)
        return json.dumps(packet)

    def feed(self, seq=0, t_us=1000, at=None, **values):
        if at is not None:
            self.now.return_value = at
        self.lab.ingest(self.packet(seq, t_us, **values))

    def test_malformed_and_nonfinite_packets_do_not_create_samples(self):
        bad = [b'partial', b'[]', b'{}', b'\xff',
               self.packet(ax_g=math.nan), self.packet(ax_g=math.inf),
               self.packet(t_us=-1), self.packet(seq=True),
               self.packet(seq=2**32)]
        missing_axis = json.loads(self.packet())
        missing_axis.pop('az_g')
        bad.append(json.dumps(missing_axis))
        for raw in bad:
            self.lab.ingest(raw)
        self.assertEqual(self.lab.count, 0)
        self.assertEqual(self.lab.invalid, len(bad))
        self.assertIsNone(self.lab.status()['latest'])

    def test_axes_must_be_json_numbers_not_boolean_or_text(self):
        for seq, value in enumerate((True, '1.2')):
            self.lab.ingest(self.packet(seq=seq, ax_g=value))
        self.assertEqual(self.lab.count, 0)
        self.assertEqual(self.lab.invalid, 2)

    def test_real_device_timestamps_and_sequence_gaps(self):
        self.feed(10, 1000, at=100.0)
        self.feed(11, 5825, at=100.02)
        self.feed(14, 20250, at=100.03)
        samples = self.lab.samples(0)['samples']
        self.assertEqual([s['seq'] for s in samples], [10, 11, 14])
        self.assertAlmostEqual(samples[-1]['elapsed_s'], 0.01925)
        self.assertEqual(self.lab.dropped, 2)
        self.assertEqual(len(self.lab.samples(samples[0]['id'])['samples']), 2)

    def test_uint32_time_and_sequence_wrap(self):
        self.feed(2**32-1, 2**32-1000)
        self.feed(0, 4000, at=100.005)
        self.assertEqual(self.lab.invalid, 0)
        self.assertEqual(self.lab.dropped, 0)
        self.assertAlmostEqual(self.lab.status()['latest']['elapsed_s'], 0.005)

    def test_duplicate_is_rejected_without_advancing_time(self):
        self.feed(1, 1000)
        self.feed(1, 2000)
        self.feed(2, 6000)
        self.assertEqual(self.lab.count, 2)
        self.assertEqual(self.lab.invalid, 1)
        self.assertAlmostEqual(self.lab.status()['latest']['elapsed_s'], 0.005)

    def test_ac_rms_removes_constant_gravity(self):
        self.feed(0, 1000, ax_g=-0.25, at=100.0)
        self.feed(1, 6000, ax_g=0.25, at=100.005)
        status = self.lab.status()
        self.assertAlmostEqual(status['stats']['ac_rms_g'], 0.25)
        self.assertAlmostEqual(status['stats']['peak_g'], 0.25)
        self.assertAlmostEqual(status['stats']['sample_rate_hz'], 200.0)
        self.assertAlmostEqual(status['latest']['az_g'], 1.0)

    def test_stale_data_is_not_reported_as_live_or_recordable(self):
        self.feed()
        self.now.return_value = 102.0
        status = self.lab.status()
        self.assertFalse(status['fresh'])
        self.assertIsNone(status['stats']['ac_rms_g'])
        self.assertIsNone(status['stats']['sample_rate_hz'])
        with self.assertRaises(ValueError):
            self.lab.start_recording('synthetic only', 'stale fixture')

    def test_recording_csv_and_metadata_roundtrip(self):
        self.lab.ingest(json.dumps(dict(type='meta', board='synthetic fixture',
                                       sensor='synthetic fixture', odr_hz=208)))
        self.feed()
        record = self.lab.start_recording('synthetic only', 'unit fixture, not hardware')
        self.assertEqual(self.lab.recordings(), [])
        self.feed(1, 6000, ax_g=0.5)
        self.feed(2, 11000, ax_g=-0.5)
        finished = self.lab.stop_recording()
        self.assertEqual(finished['samples'], 2)
        self.assertEqual(finished['stop_reason'], 'user_stopped')
        self.assertIsNotNone(finished['ended_at'])
        replay = self.lab.get_recording(record['id'])
        self.assertEqual(replay['recording'], finished)
        self.assertEqual([s['seq'] for s in replay['samples']], [1, 2])
        self.assertEqual([s['ax_g'] for s in replay['samples']], [0.5, -0.5])
        self.assertEqual(self.lab.recordings(), [finished])
        self.assertEqual(len(list(Path(self.temp.name).glob('*.csv'))), 1)
        self.assertIsNone(self.lab.stop_recording())

    def test_device_restart_stops_recording_and_resets_elapsed(self):
        self.feed(50, 1_000_000)
        self.lab.start_recording('synthetic only', 'restart fixture')
        self.feed(51, 1_005_000)
        self.feed(0, 1000)
        self.assertIsNone(self.lab.recording)
        self.assertEqual(self.lab.recordings()[0]['stop_reason'], 'device_restarted')
        self.assertEqual(len(self.lab.history), 1)
        self.assertEqual(self.lab.status()['latest']['elapsed_s'], 0)

    def test_disconnect_stops_recording(self):
        self.feed()
        self.lab.start_recording('synthetic only', 'disconnect fixture')
        self.lab.disconnect()
        self.assertFalse(self.lab.status()['fresh'])
        self.assertEqual(self.lab.recordings()[0]['stop_reason'], 'disconnected')

    def test_reader_timeout_finalizes_recording_without_new_samples(self):
        self.feed()
        self.lab.start_recording('synthetic only', 'empty reader fixture')
        device = Mock()

        def empty_read(_limit):
            self.now.return_value = 104.0
            self.lab._stop.set()
            return b''

        device.readline.side_effect = empty_read
        self.lab._read(device, self.lab._stop)
        self.assertIsNone(self.lab.recording)
        self.assertEqual(self.lab.recordings()[0]['stop_reason'], 'sensor_stale')
        self.assertEqual(self.lab.count, 1)
        device.close.assert_called_once()

    def test_sensor_error_stops_recording_and_invalidates_freshness(self):
        self.feed()
        self.lab.start_recording('synthetic only', 'sensor error fixture')
        self.lab.ingest(json.dumps(dict(type='error', message='synthetic sensor error')))
        self.assertEqual(self.lab.recordings()[0]['stop_reason'], 'sensor_error')
        self.assertEqual(self.lab.status()['error'], 'synthetic sensor error')
        self.assertFalse(self.lab.status()['fresh'])
        with self.assertRaises(ValueError):
            self.lab.start_recording('synthetic only', 'must wait for new sample')
        self.feed(1, 6000)
        self.assertTrue(self.lab.status()['fresh'])
        self.assertIsNone(self.lab.status()['error'])

    def test_recording_path_cannot_escape_storage(self):
        with self.assertRaises(ValueError):
            self.lab.get_recording('../server.py')


if __name__ == '__main__':
    unittest.main()
