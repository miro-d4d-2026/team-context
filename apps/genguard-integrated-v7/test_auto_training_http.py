"""Exercise the simplified public API without changing any live recording."""
import tempfile
import unittest
from unittest.mock import patch

from operation_service import OperationService
from test_api import FakeClock, request, running_server


class AutomaticTrainingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='genguard-auto-http-')
        self.factory = patch('test_api.HybridBridgeService', OperationService)
        self.factory.start()
        self.server = running_server(FakeClock(), self.tmp.name)
        self.base = self.server.__enter__()

    def tearDown(self):
        self.server.__exit__(None, None, None)
        self.factory.stop()
        self.tmp.cleanup()

    def call(self, path, body=None, status=200):
        code, data, _ = request(self.base, path, body)
        self.assertEqual(code, status, data)
        return data

    def test_plan_auth_and_insufficient_data_are_not_a_training_success(self):
        self.assertEqual(request(self.base, '/ui-api/training-plan', {}, token=None)[0], 401)
        result = self.call('/ui-api/training-plan', {'session_ids': []})
        self.assertFalse(result['ready'])
        self.assertTrue(result['reason'])
        self.call('/ui-api/train', {'kind': 'auto', 'session_ids': []}, status=422)
        self.assertEqual(self.call('/ui-api/state')['models'], [])

    def test_review_without_role_and_server_plan_keep_human_labels_separate(self):
        ids = self.call('/ui-api/demo', {'action': 'seed-normal'})['ids']
        for sid in ids:
            self.call('/ui-api/review', {'session_id': sid, 'status': 'normal',
                'reviewer': 'HTTP synthetic test', 'note': 'Synthetic input only, not generator evidence.'})
        plan = self.call('/ui-api/training-plan', {'session_ids': ids})
        self.assertTrue(plan['normal']['ready'])
        self.assertFalse(plan['feedback']['ready'])
        splits = plan['normal']['splits']
        self.assertEqual(sorted(sum(splits.values(), [])), sorted(ids))
        self.assertFalse(set(splits['fit']) & set(splits['validation']))
        state = self.call('/ui-api/state')
        self.assertTrue(all(s['role'] == 'none' for s in state['sessions']))
        self.assertTrue(all(s['review'] == 'normal' for s in state['sessions']))
        self.assertEqual(state['models'], [])  # Planning neither trains nor applies a candidate.


if __name__ == '__main__':
    unittest.main()
