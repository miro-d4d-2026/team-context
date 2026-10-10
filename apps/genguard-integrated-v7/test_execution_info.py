import unittest
from unittest.mock import patch
from execution_info import execution_info


class ExecutionInfoTests(unittest.TestCase):
    def test_arm_linux_alone_does_not_claim_uno_q(self):
        with patch('execution_info.platform.system', return_value='Linux'), \
             patch('execution_info.platform.machine', return_value='aarch64'), \
             patch('execution_info.Path.read_text', return_value='Another board\0'):
            self.assertEqual(execution_info()['host'], 'api_host')

    def test_board_identity_is_required(self):
        with patch('execution_info.platform.system', return_value='Linux'), \
             patch('execution_info.platform.machine', return_value='aarch64'), \
             patch('execution_info.Path.read_text', return_value='Arduino SA,Imola\0'):
            self.assertEqual(execution_info()['label'], 'UNO Q 내부 분석')
