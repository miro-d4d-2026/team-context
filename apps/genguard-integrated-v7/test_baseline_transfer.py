"""Unchanged cross-host baseline transfer; synthetic fixtures, no hardware claims."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from adapter.imu_to_api import utc_text
from baseline_transfer import checksum, export_baseline, load_transfer, validate_transfer
from bridge_service import BridgeError
from hybrid_model import score_vibration
from hybrid_service import HybridBridgeService
from operation_service import OperationService
from test_api import FakeClock, adapter_template


class BaselineTransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='genguard-baseline-transfer-')
        self.root = Path(self.directory.name)
        self.clock = FakeClock()
        self.template = adapter_template()
        self.source = self.service('source')
        self.send(self.source, 1, stream='original')
        self.source.begin_calibration({'confirmed_normal': True})
        for seq in range(2, 17):
            self.send(self.source, seq, stream='original')
        self.baseline = copy.deepcopy(self.source.baseline)
        self.assertIsNotNone(self.baseline)
        self.export_file = self.root / 'baseline.json'
        self.transfer = export_baseline(self.root / 'source' / 'bridge.sqlite',
                                       self.baseline['baseline_id'], self.export_file)
        self.targets = []

    def tearDown(self):
        for target in self.targets:
            target.close()
        self.source.close()
        self.directory.cleanup()

    def service(self, name, transfer=None, cls=HybridBridgeService):
        return cls(self.root / name, clock=self.clock.wall, monotonic=self.clock.monotonic,
                   imu_baseline_file=transfer)

    def target(self, name='target', cls=HybridBridgeService):
        target = self.service(name, self.export_file, cls)
        self.targets.append(target)
        return target

    def packet(self, seq, stream='target-stream', **updates):
        self.clock.advance(2)
        packet = copy.deepcopy(self.template)
        packet.update(stream_id=stream, window_seq=seq, measured_at=utc_text(self.clock.wall()))
        packet['features'] = {'vibration_rms_g': .055, 'envelope_cv': .014}
        packet.update(updates)
        return packet

    def send(self, service, seq, stream='target-stream', **updates):
        return service.receive_imu(self.packet(seq, stream, **updates))

    def events(self, service):
        return [json.loads(row[0]) for row in service.runtime.db.execute(
            'SELECT payload FROM hybrid_baseline_transfer_events ORDER BY event_id')]

    def test_export_read_only_round_trip_preserves_every_coefficient(self):
        before = self.source.runtime.db.total_changes
        again = export_baseline(self.root / 'source' / 'bridge.sqlite',
                                self.baseline['baseline_id'], self.root / 'again.json')
        self.assertEqual(before, self.source.runtime.db.total_changes)
        self.assertEqual(load_transfer(self.export_file)['baseline'], self.baseline)
        self.assertEqual(again['baseline'], self.baseline)
        self.assertEqual(self.transfer['source_calibration']['source_stream_id'], 'original')
        self.assertEqual(len(self.transfer['calibration_windows']), 15)

    def test_explicit_transfer_waits_for_fresh_packet_and_never_refits(self):
        target = self.target()
        self.assertIsNone(target.baseline)
        self.assertEqual(target.imu_status()['baseline_transfer']['state'], 'pending')
        with patch('hybrid_service.fit_vibration', side_effect=AssertionError('must not fit')):
            self.send(target, 1)
        self.assertEqual(target.baseline, self.baseline)
        self.assertEqual(target.calibration['state'], 'ready')
        self.assertEqual(target.calibration['count'], 15)
        self.assertEqual(target.imu_status()['baseline_transfer']['target_stream_id'], 'target-stream')
        self.assertEqual([e['event'] for e in self.events(target)], ['requested', 'activated'])
        self.assertFalse(self.events(target)[-1]['retrained'])

    def test_normal_strong_and_intermittent_scores_are_identical_after_transfer(self):
        target = self.target(cls=OperationService)
        self.send(target, 1)
        for features, state in [({'vibration_rms_g': .055, 'envelope_cv': .014}, 'within_baseline'),
                                ({'vibration_rms_g': .16, 'envelope_cv': .014}, 'change'),
                                ({'vibration_rms_g': .055, 'envelope_cv': .9}, 'change')]:
            with self.subTest(features=features):
                before = score_vibration(features, self.baseline)
                after = score_vibration(features, target.baseline)
                self.assertEqual(before, after)
                self.assertEqual(after['state'], state)

    def test_restart_without_flag_stays_untrained(self):
        name = 'restart-state'
        target = self.target(name)
        self.send(target, 1)
        target.close()
        self.targets.remove(target)
        restarted = self.service(name)
        self.targets.append(restarted)
        self.send(restarted, 2)
        self.assertIsNone(restarted.baseline)
        self.assertEqual(restarted.calibration['state'], 'untrained')
        self.assertNotIn('baseline_transfer', restarted.imu_status())

    def test_invalid_window_cannot_activate_and_first_fresh_condition_controls(self):
        target = self.target()
        self.send(target, 1, stream='invalid-old-stream', installation_id='different',
                  quality={'valid': False, 'flags': ['missing_samples']},
                  features={'vibration_rms_g': None, 'envelope_cv': None})
        self.assertIsNone(target.baseline)
        self.assertEqual(target.baseline_transfer['state'], 'pending')
        self.send(target, 1)
        self.assertEqual(target.baseline, self.baseline)

    def test_expired_packet_cannot_activate(self):
        target = self.target()
        packet = self.packet(1)
        self.clock.advance(6)
        with self.assertRaises(BridgeError):
            target.receive_imu(packet)
        self.assertEqual(target.baseline_transfer['state'], 'pending')
        self.assertIsNone(target.baseline)

    def test_all_four_sensor_conditions_must_match_and_rejection_is_sticky(self):
        for key, value in [('source', 'recorded_imu'), ('installation_id', 'other-mount'),
                           ('configured_sample_rate_hz', 416), ('accel_range_g', 8)]:
            with self.subTest(key=key):
                target = self.target('target-' + key)
                self.send(target, 1, **{key: value})
                self.assertEqual(target.baseline_transfer['state'], 'rejected')
                self.assertEqual(target.calibration['state'], 'failed')
                self.assertIsNone(target.baseline)
                self.assertEqual(target.baseline_transfer['mismatched_fields'], [key])
                self.send(target, 1, stream='matching-after-rejection')
                self.assertIsNone(target.baseline)
                self.assertNotIn('activated', [e['event'] for e in self.events(target)])

    def test_new_stream_after_activation_invalidates_and_never_auto_restores(self):
        target = self.target()
        self.send(target, 1)
        self.send(target, 1, stream='restart-stream')
        self.assertIsNone(target.baseline)
        self.assertEqual(target.calibration['state'], 'failed')
        self.assertEqual(target.baseline_transfer['state'], 'invalidated')
        self.send(target, 2, stream='restart-stream')
        self.assertIsNone(target.baseline)
        self.assertEqual([e['event'] for e in self.events(target)], ['requested', 'activated', 'invalidated'])

    def test_duplicate_packet_never_creates_another_activation_event(self):
        target = self.target()
        packet = self.packet(1)
        target.receive_imu(packet)
        self.assertTrue(target.receive_imu(packet)['duplicate'])
        self.assertEqual(len(self.events(target)), 2)

    def test_manual_new_calibration_supersedes_transferred_baseline(self):
        target = self.target()
        self.send(target, 1)
        target.begin_calibration({'confirmed_normal': True})
        self.assertIsNone(target.baseline)
        self.assertEqual(target.calibration['state'], 'collecting')
        self.assertEqual(target.baseline_transfer['state'], 'superseded')

    def test_tampered_coefficients_rejected_before_state_created(self):
        bad = copy.deepcopy(self.transfer)
        bad['baseline']['mean'][0] += 1
        file = self.root / 'tampered.json'
        file.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            self.service('should-not-exist', file)
        self.assertFalse((self.root / 'should-not-exist').exists())

    def resign(self, data):
        baseline = data['baseline']
        original = {k: v for k, v in baseline.items() if k != 'baseline_id'}
        baseline['baseline_id'] = 'imu-' + checksum(original)[:20]
        data['source_calibration']['id'] = baseline['baseline_id']
        data['baseline_sha256'] = checksum(baseline)
        data['transfer_sha256'] = checksum({k: v for k, v in data.items() if k != 'transfer_sha256'})
        return data

    def test_checksum_is_not_only_validation(self):
        for label, mutation in [
            ('covariance', lambda d: d['baseline']['covariance'].__setitem__(0, [-1., 0.])),
            ('feature_order', lambda d: d['baseline'].__setitem__('features', ['envelope_cv', 'vibration_rms_g'])),
            ('window_source', lambda d: d['calibration_windows'][3].__setitem__('source', 'recorded_imu')),
            ('old_scores', lambda d: d['baseline']['validation'].__setitem__('scores', [9.] * 5)),
            ('boolean_mean', lambda d: d['baseline']['mean'].__setitem__(0, True)),
        ]:
            with self.subTest(label=label):
                data = copy.deepcopy(self.transfer)
                mutation(data)
                with self.assertRaises(ValueError):
                    validate_transfer(self.resign(data))

    def test_duplicate_json_key_nan_and_oversize_rejected(self):
        for name, raw in [('duplicate', '{"schema_version":"x","schema_version":"y"}'),
                          ('nan', '{"value":NaN}'), ('big', ' ' * (256 * 1024 + 1))]:
            file = self.root / (name + '.json')
            file.write_text(raw)
            with self.assertRaises(ValueError):
                load_transfer(file)

    def test_export_never_overwrites_destination(self):
        before = self.export_file.read_bytes()
        with self.assertRaises(FileExistsError):
            export_baseline(self.root / 'source' / 'bridge.sqlite',
                            self.baseline['baseline_id'], self.export_file)
        self.assertEqual(before, self.export_file.read_bytes())

    def test_missing_source_evidence_is_not_exportable(self):
        with self.source.runtime.db:
            self.source.runtime.db.execute('DELETE FROM hybrid_imu_windows WHERE id=?', ('original:2',))
        with self.assertRaises(ValueError):
            export_baseline(self.root / 'source' / 'bridge.sqlite',
                            self.baseline['baseline_id'], self.root / 'missing.json')
        self.assertFalse((self.root / 'missing.json').exists())


if __name__ == '__main__':
    unittest.main()
