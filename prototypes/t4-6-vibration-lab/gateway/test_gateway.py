"""Synthetic protocol fixtures; no BLE, USB, router or socket is opened."""
import unittest
from unittest.mock import patch

from unoq_gateway import DATA_FORMAT, META_FORMAT, Gateway, ac_rms, decode_meta, decode_sample


def meta_packet(status=1, who=0x6A, gyro=500):
    return META_FORMAT.pack(1, status, who, 0x6A, 208, 4, gyro, 0)


class ProtocolTest(unittest.TestCase):
    def test_wire_format_and_physical_unit_conversion(self):
        sample = decode_sample(DATA_FORMAT.pack(0xFFFFFFFF, 0xFFFFFF00,
                                                8197, -8197, 0, 1000, -1000, 0))
        self.assertEqual(sample['seq'], 0xFFFFFFFF)
        self.assertEqual(sample['t_us'], 0xFFFFFF00)
        self.assertAlmostEqual(sample['ax_g'], 1.000034)
        self.assertAlmostEqual(sample['ay_g'], -1.000034)
        self.assertAlmostEqual(sample['gx_dps'], 17.5)
        self.assertAlmostEqual(sample['gy_dps'], -17.5)

    def test_partial_or_concatenated_notifications_are_rejected(self):
        for packet in (b'', bytes(19), bytes(21), bytes(40)):
            with self.assertRaises(ValueError):
                decode_sample(packet)
        with self.assertRaises(ValueError):
            decode_meta(bytes(11))

    def test_meta_identity_and_ranges(self):
        meta, ready = decode_meta(meta_packet())
        self.assertTrue(ready)
        self.assertEqual(meta['sensor'], 'LSM6DS3TR-C')
        self.assertEqual(meta['gyro_range_dps'], 500)
        self.assertEqual(decode_meta(meta_packet(who=0x69))[0]['sensor'], 'LSM6DS3TR')
        with self.assertRaises(ValueError):
            decode_meta(meta_packet(gyro=245))
        with self.assertRaises(ValueError):
            decode_meta(meta_packet(who=0))
        self.assertFalse(decode_meta(meta_packet(status=0, who=0))[1])

    def test_protocol_version_is_not_guessed(self):
        packet = bytearray(meta_packet())
        packet[0] = 2
        with self.assertRaises(ValueError):
            decode_meta(packet)

    def test_rms_removes_gravity_and_keeps_dynamic_component(self):
        samples = [dict(ax_g=-0.125, ay_g=0, az_g=1),
                   dict(ax_g=0.125, ay_g=0, az_g=1)]
        self.assertAlmostEqual(ac_rms(samples), 0.125)
        self.assertEqual(ac_rms([samples[0], samples[0]]), 0)

    def test_sensor_must_be_ready_before_sample_acceptance(self):
        gateway = Gateway(no_led=True)
        with self.assertRaises(ValueError):
            gateway.receive_sample(DATA_FORMAT.pack(0, 0, 0, 0, 8197, 0, 0, 0))
        self.assertEqual(gateway.sample_count, 0)

    def test_led_shows_live_then_stale_without_fabricating_samples(self):
        gateway = Gateway(no_led=True)
        gateway.ble_connected = True
        with patch('unoq_gateway.time.monotonic', return_value=100.0) as clock:
            gateway.receive_meta(meta_packet())
            gateway.receive_sample(DATA_FORMAT.pack(0, 0, -1024, 0, 8197, 0, 0, 0))
            gateway.receive_sample(DATA_FORMAT.pack(1, 4808, 1024, 0, 8197, 0, 0, 0))
            self.assertEqual(gateway.led_state(), (1, 3))
            self.assertEqual(gateway.status()['sample_count'], 2)
            clock.return_value = 102.0
            self.assertEqual(gateway.led_state(), (0, 0))
            self.assertEqual(gateway.sample_count, 2)

    def test_sensor_error_clears_live_led_state(self):
        gateway = Gateway(no_led=True)
        gateway.ble_connected = True
        gateway.receive_meta(meta_packet())
        gateway.receive_sample(DATA_FORMAT.pack(0, 0, 0, 0, 8197, 0, 0, 0))
        self.assertEqual(gateway.led_state()[0], 1)
        gateway.receive_meta(meta_packet(status=0))
        self.assertEqual(gateway.led_state(), (0, 0))
        self.assertEqual(gateway.sample_count, 1)


if __name__ == '__main__':
    unittest.main()
