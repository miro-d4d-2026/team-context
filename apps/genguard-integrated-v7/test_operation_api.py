"""HTTP checks for time-aware channels, source separation and history."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from test_api import FakeClock, running_server, request
from operation_fixtures import seed_current
from operation_service import OperationService


class OperationAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='genguard-operation-test-')
        self.clock = FakeClock()
        self.patch = patch('test_api.HybridBridgeService', OperationService)
        self.patch.start()
        self.server = running_server(self.clock, self.tmp.name)
        self.base = self.server.__enter__()
        current = seed_current()
        self.body = {'samples': current['samples'], 'context': current['context'],
                     'mode': 'scenario', 'auxiliary': {}, 'selected_elapsed_s': 600}

    def tearDown(self):
        self.server.__exit__(None, None, None)
        self.patch.stop(); self.tmp.cleanup()

    def post(self, body=None, path='/api/operation/evaluate', status=200):
        code, data, _ = request(self.base, path, self.body if body is None else body)
        self.assertEqual(code, status, data)
        return data

    def channel(self, result, field):
        return next(c for c in result['analysis']['channels'] if c['field'] == field)

    def test_normal_warmup_and_acceleration_below_absolute_limit(self):
        self.assertEqual(self.post()['operational_status']['state'], 'normal')
        for s in self.body['samples']:
            if s['elapsed_s'] >= 600: s['coolant_c'] += 12
        result = self.post()
        temperature = self.channel(result, 'coolant_c')
        self.assertEqual(temperature['state'], 'inspect')
        self.assertAlmostEqual(temperature['current'], 80, delta=1)
        self.assertEqual(temperature['interval'], {'start_s': 300, 'end_s': 600})
        self.assertAlmostEqual(temperature['observed_rate'], 4, delta=.1)
        self.assertFalse(result['operational_status']['alerts'])
        self.assertEqual(result['operational_status']['state'], 'inspect')

    def test_future_points_never_enter_current_assessment_or_save(self):
        self.body['samples'][-1]['coolant_c'] = 130
        self.body['selected_elapsed_s'] = 300
        result = self.post()
        self.assertEqual(result['operational_status']['state'], 'normal')
        self.assertEqual(len(result['observed_run']['samples']), 2)
        saved = self.post({'request_id': 'save-observed-0001', 'label': '5분 시점'}, '/api/operation/save')['saved_record']
        self.assertEqual(saved['samples'][-1]['elapsed_s'], 300)
        self.assertEqual(len(saved['samples']), 2)

    def test_pressure_decline_and_vibration_step_have_different_evidence(self):
        self.body['selected_elapsed_s'] = 1200
        normal = self.post()
        self.assertEqual(self.channel(normal, 'oil_kgf_cm2')['state'], 'normal')
        self.body['samples'][-1].update(oil_kgf_cm2=2.5, vibration_rms_g=.09)
        result = self.post()
        self.assertEqual(self.channel(result, 'oil_kgf_cm2')['state'], 'inspect')
        self.assertEqual(self.channel(result, 'vibration_rms_g')['state'], 'inspect')
        self.assertIsNone(self.channel(result, 'vibration_rms_g')['observed_rate'])

    def test_absolute_heat_limit_still_dominates(self):
        self.body['samples'][2]['coolant_c'] = 101
        result = self.post()
        self.assertEqual(result['operational_status']['state'], 'urgent')

    def test_stopped_engine_does_not_trigger_running_low_pressure_alarm(self):
        self.body['context']['engine_rpm'] = 0
        for sample in self.body['samples']: sample['oil_kgf_cm2'] = 0
        result = self.post()
        self.assertFalse(any(a['field'] in ('engine_rpm', 'oil_kgf_cm2') for a in result['operational_status']['alerts']))
        self.assertEqual(result['operational_status']['state'], 'unavailable')

    def test_active_voltage_missing_does_not_report_whole_system_normal(self):
        self.body['auxiliary']['voltage_ll_v'] = None
        result = self.post()
        self.assertEqual(result['operational_status']['state'], 'unavailable')
        self.assertTrue(result['operational_status']['missing_active'])

    def test_full_catalog_and_derived_missing_values(self):
        self.body['auxiliary'] = {'voltage_ll_v': 180, 'pf': None}
        result = self.post()
        self.assertEqual(len(result['catalog']), 34)
        rows = {r['key']: r for r in result['telemetry']}
        self.assertEqual(rows['voltage_ll_v']['state'], 'inspect')
        self.assertEqual(rows['frequency_hz']['source'], 'derived')
        self.assertEqual(rows['frequency_hz']['value'], 60)
        self.assertIsNone(rows['current_a']['value'])
        for key in ('battery_charge_current_a', 'oil_temperature_c', 'breaker_state'):
            self.assertIsNone(rows[key]['value'])
            self.assertEqual(rows[key]['source'], 'unconnected')

    def test_invalid_time_inputs_and_sensor_override_rejected(self):
        for mutation in ('duplicate', 'reverse', 'negative', 'infinity_like'):
            body = copy.deepcopy(self.body)
            if mutation == 'duplicate': body['samples'][1]['elapsed_s'] = 0
            elif mutation == 'reverse': body['samples'].reverse()
            elif mutation == 'negative': body['samples'][0]['elapsed_s'] = -1
            else: body['samples'][0]['coolant_c'] = True
            self.post(body, status=422)
        body = copy.deepcopy(self.body); body['selected_elapsed_s'] = 333
        self.post(body, status=422)
        body = copy.deepcopy(self.body); body.update(mode='replay', recording_id='random', frame_index=0)
        self.post(body, status=422)

    def test_real_replay_is_separate_from_generator_curves(self):
        for s in self.body['samples']: s['vibration_rms_g'] = None
        self.body.update(mode='replay', recording_id='random', frame_index=0)
        result = self.post()
        self.assertEqual(result['vibration_replay']['prediction']['label'], 'increased')
        self.assertEqual(self.channel(result, 'vibration_rms_g')['method'], 'recorded_pattern_classifier')
        self.assertEqual(self.channel(result, 'vibration_rms_g')['reference_source'], 'recorded_imu')
        self.assertIsNone(self.channel(result, 'vibration_rms_g')['previous'])
        self.assertEqual(self.channel(result, 'coolant_c')['state'], 'normal')
        self.assertTrue(all(s['vibration_rms_g'] is None for s in result['observed_run']['samples']))
        self.post({'request_id': 'dont-mix-real-001'}, '/api/operation/save', status=409)
        self.post({}, '/api/operation/retrain', status=422)
        self.body['mode'] = 'hybrid'
        result = self.post()
        self.assertIsNone(self.channel(result, 'vibration_rms_g')['current'])
        self.assertEqual(self.channel(result, 'vibration_rms_g')['state'], 'unavailable')

    def test_save_idempotency_and_abnormal_normal_rejection(self):
        self.post()
        body = {'request_id': 'save-normal-0001', 'label': '정상 운전', 'confirmed_normal': True}
        first = self.post(body, '/api/operation/save')
        repeated = self.post(body, '/api/operation/save')
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(first['saved_record']['id'], repeated['saved_record']['id'])
        self.post(dict(body, label='변경'), '/api/operation/save', status=409)
        self.body['samples'][2]['coolant_c'] = 85
        self.post()
        self.post(dict(body, request_id='save-abnormal-0002'), '/api/operation/save', status=422)

    def test_auth_and_pages(self):
        self.assertEqual(request(self.base, '/api/operation/overview', token=None)[0], 401)
        for path in ('/', '/comparison', '/records', '/operation.css', '/operation.mjs', '/inspection-v5'):
            self.assertEqual(request(self.base, path)[0], 200, path)


if __name__ == '__main__': unittest.main()
