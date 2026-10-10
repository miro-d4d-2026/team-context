"""Synthetic socket-pair fixtures, never connected to a real board."""
import json
import socket
import tempfile
import threading
import time
import unittest

from server import Lab


class GatewayTransportTest(unittest.TestCase):
    def test_fragmented_packets_and_eof_invalidate_live_state(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-transport-test-') as folder:
            lab = Lab(folder)
            receiver, sender = socket.socketpair()
            receiver.settimeout(.1)
            stop = threading.Event()
            lab.connected = True
            lab.source = 'unoq_ble'
            thread = threading.Thread(target=lab._read_gateway, args=(receiver, stop))
            thread.start()
            packets = [dict(type='gateway', board='Arduino UNO Q', ble_connected=True),
                       dict(type='sample', seq=8, t_us=1234, ax_g=0, ay_g=0, az_g=1,
                            gx_dps=0, gy_dps=0, gz_dps=0)]
            encoded = ('\n'.join(json.dumps(p) for p in packets) + '\n').encode()
            try:
                sender.sendall(encoded[:17])
                sender.sendall(encoded[17:])
                deadline = time.monotonic() + 1
                while lab.count == 0 and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(lab.count, 1)
                self.assertTrue(lab.status()['fresh'])
                self.assertTrue(lab.status()['gateway']['ble_connected'])
                lab.start_recording('synthetic transport test', 'fixture')
                sender.close()
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertFalse(lab.status()['fresh'])
                self.assertIsNone(lab.recording)
                self.assertEqual(lab.recordings()[0]['stop_reason'], 'gateway_disconnected')
            finally:
                stop.set()
                sender.close()
                receiver.close()
                thread.join(1)


if __name__ == '__main__':
    unittest.main()
