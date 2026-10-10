"""Live UI history must not disguise stale, invalid or replayed samples."""
import unittest
from unittest.mock import patch
import test_operation_api as operation_tests
from test_api import request
from adapter.imu_to_api import utc_text


class IMUHistoryTests(unittest.TestCase):
    setUp = operation_tests.OperationAPITests.setUp
    tearDown = operation_tests.OperationAPITests.tearDown

    def packet(self, seq=1, **changes):
        self.clock.advance(2)
        packet = {'schema_version': 'genguard.imu.window/1', 'source': 'live_imu',
                  'stream_id': 'physical-test-stream', 'installation_id': 'phone-fixture',
                  'window_seq': seq, 'duration_s': 2, 'sample_count': 416,
                  'sample_rate_hz': 208, 'configured_sample_rate_hz': 208,
                  'accel_range_g': 4, 'measured_at': utc_text(self.clock.wall()),
                  'features': {'vibration_rms_g': .035, 'envelope_cv': .1},
                  'quality': {'valid': True, 'flags': []}}
        packet.update(changes)
        code, data, _ = request(self.base, '/api/imu/windows', packet)
        self.assertEqual(code, 200, data)
        return packet

    def history(self):
        code, data, _ = request(self.base, '/api/imu/history')
        self.assertEqual(code, 200, data)
        return data

    def test_waiting_no_fabricated_chart(self):
        history = self.history()
        self.assertEqual(history['state'], 'waiting')
        self.assertEqual(history['points'], [])
        self.assertIsNone(history['baseline']['rms_g'])
        code, _, _ = request(self.base, '/api/imu/history', token=None)
        self.assertEqual(code, 401)

    def test_live_duplicate_and_stale_do_not_create_fresh_samples(self):
        packet = self.packet()
        request(self.base, '/api/imu/windows', packet)
        history = self.history()
        self.assertEqual(history['source'], 'live_imu')
        self.assertEqual(len(history['points']), 1)
        self.assertEqual(history['points'][0]['rms_g'], .035)
        self.clock.advance(6)
        request(self.base, '/api/imu/windows', packet)
        history = self.history()
        self.assertEqual(history['state'], 'stale')
        self.assertEqual(len(history['points']), 1)

    def test_replay_identity_cannot_join_physical_history(self):
        self.packet()
        self.packet(source='recorded_imu', stream_id='replay-test-stream')
        history = self.history()
        self.assertEqual(history['source'], 'recorded_imu')
        self.assertEqual(len(history['points']), 1)
        self.assertTrue(all(p['source'] == 'recorded_imu' for p in history['points']))

    def test_invalid_window_is_chart_gap_and_not_zero(self):
        self.packet()
        self.packet(2, quality={'valid': False, 'flags': ['sample_gap']},
                    features={'vibration_rms_g': None, 'envelope_cv': None})
        history = self.history()
        self.assertEqual(history['state'], 'invalid')
        self.assertIsNone(history['points'][-1]['rms_g'])
        self.assertFalse(history['points'][-1]['valid'])

    def test_history_is_bounded_and_baseline_requires_confirmation(self):
        for seq in range(1, 183):
            self.packet(seq)
        history = self.history()
        self.assertEqual(len(history['points']), 180)
        self.assertEqual(history['points'][0]['window_id'], 'physical-test-stream:3')
        self.assertFalse(history['baseline']['ready'])
        code, _, _ = request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})
        self.assertEqual(code, 200)
        for seq in range(183, 198):
            self.packet(seq)
        history = self.history()
        self.assertTrue(history['baseline']['ready'])
        self.assertAlmostEqual(history['baseline']['rms_g'], .035)

    def test_unknown_model_result_is_not_normal(self):
        self.packet()
        request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})
        for seq in range(2, 17):
            self.packet(seq)
        for sample in self.body['samples']:
            sample['vibration_rms_g'] = None
        self.body['mode'] = 'hybrid'
        with patch('hybrid_model.score_vibration', return_value={'state': 'unknown', 'reason': 'incompatible_baseline'}):
            code, result, _ = request(self.base, '/api/operation/evaluate', self.body)
        self.assertEqual(code, 200, result)
        channel = next(c for c in result['analysis']['channels'] if c['field'] == 'vibration_rms_g')
        self.assertEqual(channel['state'], 'unavailable')
        self.assertEqual(result['operational_status']['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
