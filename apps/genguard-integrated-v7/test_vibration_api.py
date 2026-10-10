"""Measured-record replay integration, with no physical board or generator claim."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from test_api import FakeClock, running_server, request
from vibration_service import VibrationService
from vibration_classifier import VibrationClassifier


class VibrationAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='genguard-vibration-test-')
        self.patch = patch('test_api.HybridBridgeService', VibrationService)
        self.patch.start()
        self.server = running_server(FakeClock(), self.tmp.name)
        self.base = self.server.__enter__()
        _, self.initial, _ = request(self.base, '/api/inspections/overview')
        self.body = {'recording_id': 'random', 'frame_index': 0,
            'values': {k: v for k, v in self.initial['defaults'].items() if k != 'vibration_rms_g'}}
        self.frames = VibrationClassifier().load_recording('random')['frames']

    def tearDown(self):
        self.server.__exit__(None, None, None)
        self.patch.stop()
        self.tmp.cleanup()

    def post(self, body=None, path='/api/vibration/replay', status=200):
        actual, result, _ = request(self.base, path, self.body if body is None else body)
        self.assertEqual(actual, status, result)
        return result

    def select(self, label):
        self.body['frame_index'] = next(i for i, f in enumerate(self.frames) if f['expected_label'] == label)
        return self.post()

    def test_catalog_is_authenticated_and_keeps_holdout_scope(self):
        self.assertEqual(request(self.base, '/api/vibration/catalog', token=None)[0], 401)
        status, result, _ = request(self.base, '/api/vibration/catalog')
        self.assertEqual(status, 200)
        self.assertEqual({r['id'] for r in result['recordings']}, {'training', 'random'})
        self.assertTrue(all(r['frame_count'] == 48 for r in result['recordings']))
        self.assertEqual(request(self.base, '/api/vibration/replay', self.body, token=None)[0], 401)

    def test_four_patterns_and_source_labels(self):
        for label, state in [('baseline', 'normal'), ('increased', 'inspect'), ('intermittent', 'inspect'), ('idle', 'unavailable')]:
            result = self.select(label)
            self.assertEqual(result['vibration_replay']['prediction']['label'], label)
            self.assertEqual(result['operational_status']['state'], state)
            self.assertEqual(result['current']['sources']['vibration_rms_g'], 'recorded_imu')
            self.assertEqual(result['current']['sources']['coolant_c'], 'scenario')
            self.assertEqual(result['current']['sources']['oil_kgf_cm2'], 'scenario')
            channel = next(c for c in result['comparison']['channels'] if c['field'] == 'vibration_rms_g')
            self.assertEqual(channel['state'], 'unavailable')  # No fake generator comparison.
            self.assertEqual(result['current']['values']['vibration_rms_g'], self.frames[self.body['frame_index']]['rms_g'])

    def test_invalid_indices_and_browser_injections_are_rejected(self):
        for index in [-1, 48, True, 1.5, '0', None]:
            body = copy.deepcopy(self.body); body['frame_index'] = index
            self.post(body, status=422)
        for key, value in [('recording_id', '../training'), ('prediction', 'baseline'), ('expected_label', 'baseline')]:
            body = copy.deepcopy(self.body); body[key] = value
            self.post(body, status=422)
        body = copy.deepcopy(self.body); body['values']['vibration_rms_g'] = .001
        self.post(body, status=422)
        body = copy.deepcopy(self.body); body['values']['coolant_c'] = True
        self.post(body, status=422)

    def test_replayed_records_never_enter_generator_training_or_history(self):
        result = self.select('baseline')
        self.assertEqual(len(result['records']), len(self.initial['records']))
        self.post({}, '/api/inspections/retrain', status=409)
        self.post({'mode': 'replay', 'request_id': 'dont-save-replay-1'}, '/api/inspections/save', status=409)
        _, after, _ = request(self.base, '/api/inspections/overview')
        self.assertEqual(after['vibration_replay']['frame_index'], self.body['frame_index'])
        self.assertEqual(len(after['records']), len(self.initial['records']))

    def test_urgent_mock_values_dominate_every_pattern(self):
        self.body['values']['coolant_c'] = 101
        for label in ['baseline', 'increased', 'idle', 'intermittent']:
            self.assertEqual(self.select(label)['operational_status']['state'], 'urgent')
        self.body['values']['coolant_c'] = 80.2
        self.body['values']['oil_kgf_cm2'] = .5
        self.assertEqual(self.select('baseline')['operational_status']['state'], 'urgent')

    def test_normal_vibration_does_not_hide_changed_or_missing_mock_channels(self):
        self.body['values']['coolant_c'] = 87.2
        result = self.select('baseline')
        self.assertEqual(result['operational_status']['state'], 'inspect')
        self.assertTrue(result['comparison']['recommendations'])
        self.body['values']['coolant_c'] = None
        self.assertEqual(self.select('baseline')['operational_status']['state'], 'unavailable')

    def test_replay_to_hybrid_does_not_resurrect_recorded_data_as_live(self):
        self.select('increased')
        result = self.post({'mode': 'hybrid', 'values': self.body['values']}, '/api/inspections/compare')
        self.assertNotIn('vibration_replay', result)
        self.assertIsNone(result['current']['values']['vibration_rms_g'])
        self.assertEqual(result['current']['sources']['vibration_rms_g'], 'missing')
        _, refreshed, _ = request(self.base, '/api/inspections/overview')
        self.assertEqual(refreshed['mode'], 'hybrid')

    def test_selection_cannot_affect_inference_features(self):
        self.body['frame_index'] = 0
        first = self.post()
        self.body['values'].update(load_pct=90, engine_rpm=2000, coolant_c=110)
        second = self.post()
        self.assertEqual(first['vibration_replay']['prediction'], second['vibration_replay']['prediction'])
        self.assertFalse(first['fixed']['alerts'])
        self.assertTrue(second['fixed']['alerts'])


if __name__ == '__main__':
    unittest.main()
