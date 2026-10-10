"""HTTP integration with the existing, unchanged IMU packet serializer."""
import copy
import tempfile
import unittest
from unittest.mock import patch, Mock

from adapter.imu_to_api import utc_text
from operation_service import OperationService
from test_api import FakeClock, adapter_template, request, running_server


class LearningAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='genguard-reviewed-api-')
        self.clock = FakeClock()
        self.factory = patch('test_api.HybridBridgeService', OperationService)
        self.factory.start()
        self.server = running_server(self.clock, self.tmp.name)
        self.base = self.server.__enter__()
        self.packet = adapter_template()

    def tearDown(self):
        self.server.__exit__(None, None, None)
        self.factory.stop()
        self.tmp.cleanup()

    def call(self, path, body=None, status=200):
        code, data, _ = request(self.base, path, body)
        self.assertEqual(code, status, data)
        return data

    def sample(self, seq):
        self.clock.advance(2)
        packet = copy.deepcopy(self.packet)
        packet.update(window_seq=seq, measured_at=utc_text(self.clock.wall()), sample_rate_hz=207.98 + seq * .001)
        return self.call('/api/imu/windows', packet)

    def test_auth_namespace_and_empty_state(self):
        self.assertEqual(request(self.base, '/ui-api/state', token=None)[0], 401)
        self.assertEqual(request(self.base, '/ui-api/review', {}, token=None)[0], 401)
        state = self.call('/ui-api/state')
        self.assertFalse(state['live'])
        self.assertIsNone(state['result'].get('score'))
        self.assertEqual(state['sessions'], [])
        self.call('/ui-api/session?id=a&id=b', status=422)
        self.call('/ui-api/start', {}, status=422)

    def test_live_packet_capture_keeps_numeric_features_and_existing_monitor(self):
        self.sample(1)
        sid = self.call('/ui-api/start', {'label': '실제 입력 계약 회귀검사', 'condition': '고정 설치'})['session_id']
        self.sample(2)
        self.sample(3)
        state = self.call('/ui-api/state')
        self.assertTrue(state['live'])
        self.assertEqual(state['session']['count'], 2)
        self.call('/ui-api/review', {'session_id': sid, 'status': 'normal', 'reviewer': '테스트', 'note': '진행 중'}, status=422)
        self.call('/ui-api/stop', {})
        record = self.call('/ui-api/session?id=' + sid)
        self.assertEqual(len(record['frames']), 2)
        values = record['frames'][0]['frame']['values']
        self.assertEqual(set(values), {'vibration_rms_g', 'envelope_cv'})
        self.assertEqual(values['vibration_rms_g'], self.packet['features']['vibration_rms_g'])
        self.assertNotIn('vibration_peak_g', values)
        self.assertEqual(record['session']['review'], 'unreviewed')
        self.assertTrue(record['raw_packets']['available'])
        self.assertEqual(self.call('/api/imu')['source'], 'live_imu')
        self.call('/ui-api/review', {'session_id': sid, 'status': 'abnormal', 'role': 'fit', 'reviewer': '검토자', 'note': '아이폰 자극 변화 시험'})
        updated = self.call('/ui-api/session?id=' + sid)
        self.assertEqual(updated['session']['review'], 'abnormal')
        self.assertEqual(updated['frames'], record['frames'])

    def test_recording_import_idempotent_labels_not_automatically_approved(self):
        first = self.call('/ui-api/import-recording', {'recording': 'random'})
        repeat = self.call('/ui-api/import-recording', {'recording': 'random'})
        self.assertEqual(first['session_id'], repeat['session_id'])
        self.assertTrue(repeat['already_imported'])
        detail = self.call('/ui-api/session?id=' + first['session_id'])
        self.assertEqual(detail['session']['count'], 48)
        self.assertEqual(detail['session']['review'], 'unreviewed')
        self.assertEqual(detail['session']['role'], 'none')
        self.assertEqual(detail['frames'][0]['frame']['duration_s'], 2)
        original = self.call('/ui-api/raw?id=' + first['session_id'])
        self.assertEqual(original['run_id'], first['original_run_id'])
        self.assertEqual(original['frame_count'], 48)
        self.assertEqual(request(self.base, '/ui-api/raw?id=' + first['session_id'], token=None)[0], 401)
        self.call('/ui-api/train', {'kind': 'feedback', 'session_ids': [first['session_id']]}, status=422)

    def test_classifier_does_not_suppress_alerts_or_mix_synthetic_input(self):
        service = OperationService.__new__(OperationService)
        service.learning_workspace = Mock()
        base = {'view': {'mode': 'hybrid'}, 'operational_status': {
            'state': 'urgent', 'alerts': [{'id': 'existing_heat'}], 'headline': '기존 보호 경보'}}
        learned = {'live': True, 'fresh': True, 'source_kind': 'hardware',
                   'result': {'state': 'stable', 'reason': '검토 사례와 유사'}}
        service.learning_workspace.summary.return_value = learned
        self.assertEqual(service._learning_overlay(copy.deepcopy(base))['operational_status'], base['operational_status'])
        learned['result']['state'] = 'attention'
        elevated = service._learning_overlay(copy.deepcopy(base))['operational_status']
        self.assertEqual(elevated['state'], 'urgent')
        self.assertEqual(elevated['alerts'][0]['id'], 'existing_heat')
        self.assertEqual(len(elevated['alerts']), 2)
        learned['source_kind'] = 'synthetic'
        self.assertEqual(service._learning_overlay(copy.deepcopy(base))['operational_status'], base['operational_status'])
        learned.update(source_kind='hardware', fresh=False)
        self.assertEqual(service._learning_overlay(copy.deepcopy(base))['operational_status'], base['operational_status'])


if __name__ == '__main__':
    unittest.main()
