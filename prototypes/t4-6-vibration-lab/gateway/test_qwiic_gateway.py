"""Synthetic tests only; no I2C, MCU, router, network socket or USB is opened."""
import asyncio
import unittest
from unittest.mock import patch

from unoq_qwiic_gateway import META, SAMPLE, Gateway, decode_batch, decode_meta, decode_status


def meta_packet(ready=1, who=0x6A, gyro=500):
    return META.pack(1, ready, who, 0x6A, 208, 4, gyro, 0)


class QwiicProtocolTest(unittest.TestCase):
    def test_binary_batch_preserves_timestamps_and_converts_units(self):
        data = (SAMPLE.pack(0xFFFFFFFF, 0xFFFFFF00, 8197, -8197, 0, 1000, -1000, 0)
                + SAMPLE.pack(0, 4800, 0, 0, 8197, 0, 0, 0))
        samples = decode_batch(data)
        self.assertEqual([s['seq'] for s in samples], [0xFFFFFFFF, 0])
        self.assertEqual([s['t_us'] for s in samples], [0xFFFFFF00, 4800])
        self.assertAlmostEqual(samples[0]['ax_g'], 1.000034)
        self.assertAlmostEqual(samples[0]['gy_dps'], -17.5)

    def test_empty_batch_does_not_create_a_sample(self):
        gateway = Gateway()
        self.assertEqual(gateway.receive_batch(b''), 0)
        self.assertEqual(gateway.sample_count, 0)
        self.assertIsNone(gateway.last_sample_at)

    def test_wrong_types_lengths_and_ranges_are_rejected(self):
        for value in ('text', [0] * 20, bytes(19), bytes(21), bytes(180)):
            with self.assertRaises(ValueError):
                decode_batch(value)
        for value in ('text', bytes(11), meta_packet(gyro=245), meta_packet(who=0)):
            with self.assertRaises(ValueError):
                decode_meta(value)

    def test_unavailable_sensor_is_not_identified_as_real_sensor(self):
        meta, ready = decode_meta(meta_packet(ready=0, who=0))
        self.assertFalse(ready)
        self.assertEqual(meta['sensor'], '미확인')
        self.assertEqual(meta['board'], 'Arduino UNO Q')

    def test_samples_require_valid_ready_metadata(self):
        gateway = Gateway()
        sample = SAMPLE.pack(1, 5000, 0, 0, 8197, 0, 0, 0)
        with self.assertRaises(ValueError):
            gateway.receive_batch(sample)
        gateway.receive_meta(meta_packet())
        self.assertEqual(gateway.receive_batch(sample), 1)
        self.assertEqual(gateway.sample_count, 1)
        self.assertIsNone(gateway.error)

    def test_status_reports_mcu_led_claim_with_evidence_boundary(self):
        gateway = Gateway()
        gateway.router_connected = True
        gateway.mcu_status = decode_status([1, 1, 200, 3, 8, 1, 4, 125000])
        status = gateway.status()
        self.assertTrue(status['led_ready'])
        self.assertEqual(status['ring_overflow_count'], 3)
        self.assertAlmostEqual(status['mcu_rms_g'], 0.125)
        self.assertIn('physical visibility unverified', status['led_verification'])
        gateway.router_connected = False
        self.assertFalse(gateway.status()['sensor_ready'])
        self.assertFalse(gateway.status()['led_ready'])

    def test_invalid_status_is_rejected(self):
        for value in (bytes(8), [0] * 7, [True] + [0] * 7,
                      [1, 1, 0, 0, 257, 0, 0, 0], [1, 1, 0, 0, 0, 0, 9, 0]):
            with self.assertRaises(ValueError):
                decode_status(value)


class PollTest(unittest.IsolatedAsyncioTestCase):
    async def test_mock_rpc_poll_decodes_batch_then_waits_on_empty(self):
        class SyntheticBridge:
            batches = 0

            def call(self, method, timeout):
                if method == 'imu_meta':
                    return meta_packet()
                if method == 'imu_status':
                    return [1, 1, 2, 0, 2, 0, 0, 0]
                self.batches += 1
                if self.batches == 1:
                    return SAMPLE.pack(10, 1000, 0, 0, 8197, 0, 0, 0)
                if self.batches == 2:
                    return b''
                raise asyncio.CancelledError()

        gateway = Gateway()
        emitted = []
        gateway.emit = emitted.append
        with self.assertRaises(asyncio.CancelledError):
            await gateway.poll(SyntheticBridge())
        samples = [p for p in emitted if p['type'] == 'sample']
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['seq'], 10)
        self.assertEqual(gateway.sample_count, 1)

    async def test_rpc_send_runtime_error_is_reported_and_retried(self):
        class DisconnectedBridge:
            calls = 0

            def call(self, method, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError('Failed to call method: Not connected to router')
                raise asyncio.CancelledError()

        gateway = Gateway()
        emitted = []
        gateway.emit = emitted.append
        bridge = DisconnectedBridge()
        with patch('unoq_qwiic_gateway.asyncio.sleep', return_value=None):
            with self.assertRaises(asyncio.CancelledError):
                await gateway.poll(bridge)
        self.assertEqual(bridge.calls, 2)
        self.assertFalse(gateway.router_connected)
        self.assertEqual(gateway.sample_count, 0)
        self.assertTrue(any(p['type'] == 'error' for p in emitted))


if __name__ == '__main__':
    unittest.main()
