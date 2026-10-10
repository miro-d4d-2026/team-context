import unittest
from unittest.mock import patch

from .usb_connect import USBConnectionError, adb_path, forward_exists, select_device


class USBConnectionTests(unittest.TestCase):
    def test_single_ready_device(self):
        self.assertEqual(select_device('List of devices attached\nboard1\tdevice\n'), 'board1')

    def test_offline_and_unauthorized_are_not_ready(self):
        for state in ('offline', 'unauthorized'):
            with self.assertRaises(USBConnectionError):
                select_device('board1\t' + state)

    def test_multiple_devices_require_explicit_choice(self):
        listing = 'board1\tdevice\nphone1\tdevice'
        with self.assertRaises(USBConnectionError):
            select_device(listing)
        self.assertEqual(select_device(listing, 'board1'), 'board1')
        with self.assertRaises(USBConnectionError):
            select_device(listing, 'unknown')

    def test_reuse_only_exact_owned_device_and_target(self):
        self.assertTrue(forward_exists('board1 tcp:19877 tcp:9877', 'board1', 19877, 9877))
        self.assertFalse(forward_exists('board1 tcp:18888 tcp:9877', 'board1', 19877, 9877))
        for listing in ('phone1 tcp:19877 tcp:9877', 'board1 tcp:19877 tcp:9999'):
            with self.assertRaises(USBConnectionError):
                forward_exists(listing, 'board1', 19877, 9877)

    def test_missing_adb_is_actionable_and_does_not_download(self):
        with patch('pathlib.Path.is_file', return_value=False), patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(USBConnectionError, 'USB_CONNECTION.md'):
                adb_path()


if __name__ == '__main__':
    unittest.main()
