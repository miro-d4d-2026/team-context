"""IMU-driven synthetic telemetry keeps source and model boundaries intact."""
import copy
import unittest
import test_imu_history as history_tests
import test_operation_api as operation_tests
from test_api import request


class LinkedSimulationAPITests(unittest.TestCase):
    setUp = operation_tests.OperationAPITests.setUp
    tearDown = operation_tests.OperationAPITests.tearDown
    packet = history_tests.IMUHistoryTests.packet

    def start_linked(self):
        self.body['mode'] = 'hybrid'
        for sample in self.body['samples']:
            sample['vibration_rms_g'] = None
        code, _, _ = request(self.base, '/api/operation/evaluate', self.body)
        self.assertEqual(code, 200)
        code, result, _ = request(self.base, '/api/operation/linked-simulation', {'enabled': True})
        self.assertEqual(code, 200)
        return result

    def overview(self):
        code, result, _ = request(self.base, '/api/operation/overview')
        self.assertEqual(code, 200)
        return result

    def sample(self, seq, rms=.055, cv=.015):
        self.packet(seq, features={'vibration_rms_g': rms, 'envelope_cv': cv})
        return self.overview()

    def test_requires_hybrid_and_strict_boolean(self):
        code, _, _ = request(self.base, '/api/operation/linked-simulation', {'enabled': True})
        self.assertEqual(code, 409)
        for body in ({}, {'enabled': 1}, {'enabled': True, 'rpm': 1800}):
            self.assertEqual(request(self.base, '/api/operation/linked-simulation', body)[0], 422)

    def test_incoming_windows_are_analyzed_without_overview_polling(self):
        self.start_linked()
        self.packet(1, features={'vibration_rms_g': .001, 'envelope_cv': .02})
        self.packet(2, features={'vibration_rms_g': .001, 'envelope_cv': .02})
        code, status, _ = request(self.base, '/api/imu')
        self.assertEqual(code, 200)
        self.assertEqual(status['last_analysis']['window_id'], status['window_id'])
        self.assertEqual(status['last_analysis']['pattern'], 'idle')
        self.assertEqual(status['last_analysis']['state'], 'normal')
        self.assertIn('execution', status['last_analysis'])

    def test_zero_vibration_stops_without_low_voltage_or_oil_fault(self):
        self.start_linked()
        self.sample(1, .001)
        result = self.sample(2, .001)
        rows = {r['key']: r for r in result['telemetry']}
        self.assertEqual(result['linked_simulation']['phase'], 'stopped')
        for key in ('engine_rpm', 'voltage_ll_v', 'load_pct', 'current_a'):
            self.assertEqual(rows[key]['value'], 0)
        self.assertAlmostEqual(rows['coolant_c']['value'], 28.5)
        self.assertEqual(result['operational_status']['state'], 'normal')
        self.assertFalse(result['operational_status']['alerts'])
        self.assertEqual(rows['vibration_rms_g']['source'], 'live_imu')
        self.assertEqual(rows['coolant_c']['source'], 'imu_driven_simulation')

    def test_startup_ramps_then_stop_cools_without_changing_manual_input(self):
        result = self.start_linked()
        original = copy.deepcopy(result['current_run'])
        self.sample(1)
        for seq in range(2, 25):
            result = self.sample(seq)
            self.assertFalse(any(a['field'] in ('engine_rpm', 'oil_kgf_cm2', 'voltage_ll_v')
                                 for a in result['operational_status']['alerts']))
        values = result['linked_simulation']['values']
        self.assertGreater(values['engine_rpm'], 1750)
        self.assertGreater(values['load_pct'], 50)
        self.assertGreater(values['coolant_c'], 35)
        heat = values['coolant_c']
        for seq in range(25, 40):
            result = self.sample(seq, .001)
        values = result['linked_simulation']['values']
        self.assertEqual(values['engine_rpm'], 0)
        self.assertLess(values['coolant_c'], heat)
        self.assertGreater(values['coolant_c'], 28.5)
        self.assertEqual(result['current_run']['context'], original['context'])
        self.assertEqual(result['current_run']['samples'], original['samples'])
        self.assertEqual(result['analysis']['model']['training_record_count'], 0)
        self.assertIsNone(result['reference_id'])
        self.assertEqual(request(self.base, '/api/operation/save', {'request_id': 'linked-no-save-1'})[0], 409)
        self.assertEqual(request(self.base, '/api/operation/retrain', {})[0], 422)

    def test_no_data_does_not_imply_stopped_or_keep_generating(self):
        result = self.start_linked()
        self.assertEqual(result['operational_status']['state'], 'unavailable')
        for seq in range(1, 10):
            result = self.sample(seq)
        self.clock.advance(8)
        result = self.overview()
        self.assertEqual(result['linked_simulation']['phase'], 'signal_lost')
        self.assertEqual(result['operational_status']['state'], 'unavailable')
        frozen = copy.deepcopy(result['linked_simulation']['values'])
        self.clock.advance(60)
        self.assertEqual(self.overview()['linked_simulation']['values'], frozen)

    def test_disable_restores_manual_values_and_recorded_source_cannot_drive(self):
        self.start_linked()
        self.packet(1, source='recorded_imu')
        self.assertEqual(self.overview()['operational_status']['state'], 'unavailable')
        code, result, _ = request(self.base, '/api/operation/linked-simulation', {'enabled': False})
        self.assertEqual(code, 200)
        self.assertFalse(result['linked_simulation']['enabled'])
        coolant = next(r for r in result['telemetry'] if r['key'] == 'coolant_c')
        self.assertEqual(coolant['source'], 'scenario')
        self.assertEqual(coolant['value'], self.body['samples'][2]['coolant_c'])

    def test_intermittent_pattern_reports_only_simulated_rpm_alert(self):
        self.start_linked()
        for seq in range(1, 12):
            self.sample(seq)
        self.sample(12, .035, 1.4)
        result = self.sample(13, .035, 1.4)
        self.assertEqual(result['linked_simulation']['phase'], 'irregular')
        rpm_alert = next(a for a in result['operational_status']['alerts'] if a['field'] == 'engine_rpm')
        self.assertEqual(rpm_alert['source'], 'imu_driven_simulation')
        self.assertIn('모의', rpm_alert['title'])
        actual = next(r for r in result['telemetry'] if r['key'] == 'vibration_rms_g')
        self.assertEqual(actual['source'], 'live_imu')


if __name__ == '__main__':
    unittest.main()
