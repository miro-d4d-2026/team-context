"""Independent acceptance checks for review boundaries and preserved source data."""
from copy import deepcopy
import json
import tempfile
import unittest

from review_learning.service import Service, canonical_frame, signature
from review_learning.model import PROFILES
from learning_workspace import LearningWorkspace
from review_learning.label_model import fit_label_model, predict_label


def meta(kind='hardware'):
    return {'asset_id': 'audit-phone', 'installation_id': 'audit-mount',
            'label': '원본 녹화', 'condition': '아이폰 자극 변화 시험',
            'profile': 'vibration', 'source_kind': kind,
            'dataset_id': '', 'sensor_config': 'legacy-range-unknown'}


def frame(seq=1, end=2., source='recorded_imu'):
    return {'seq': seq, 'elapsed_s': end, 'duration_s': 2., 'phase': 'running',
            'quality': 'ok', 'values': {'vibration_rms_g': .05, 'envelope_cv': .04},
            'sources': {'vibration_rms_g': source, 'envelope_cv': source}}


class LearningAcceptanceAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(self.temp.name)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_gapped_recording_labels_use_actual_two_second_bounds(self):
        sid = self.service.start(meta())
        self.service.ingest(sid, frame(1, 12.5))
        second = frame(2, 22.5)
        second['intentional_gap'] = True
        self.service.ingest(sid, second)
        self.service.stop()
        with self.assertRaises(ValueError):
            self.service.add_label(sid, 'abnormal', 21, 22.5, '우지', '경계 창 제외')
        saved = self.service.add_label(sid, 'abnormal', 20.5, 22.5, '우지', '자극 변화 시험')
        self.assertEqual(saved['windows'], 1)
        rows = self.service._labeled_rows(self.service.session(sid))
        self.assertEqual([(r['frame']['seq'], r['label']) for r in rows], [(2, 'abnormal')])
        self.assertEqual(self.service.session(sid)['review'], 'segmented')

    def test_human_review_does_not_rewrite_recorded_model_result(self):
        sid = self.service.start(meta())
        old = self.service.ingest(sid, frame())
        self.service.stop()
        self.service.review(sid, 'normal', 'fit', '우지', '부착 상태 확인')
        version = self.service.session(sid)['review_version']
        self.service.review(sid, 'abnormal', 'fit', '우지', '같은 기록 재검토')
        self.assertGreater(self.service.session(sid)['review_version'], version)
        self.assertEqual(self.service.frames(sid)[0]['result'], old)
        audits = list(self.service.db.execute("SELECT data FROM audit WHERE action='review'"))
        self.assertEqual(len(audits), 2)

    def test_last_segment_revocation_does_not_restore_old_normal_label(self):
        sid = self.service.start(meta())
        self.service.ingest(sid, frame())
        self.service.stop()
        self.service.review(sid, 'normal', 'fit', '우지', '초기 확인')
        label = self.service.add_label(sid, 'abnormal', 0, 2, '우지', '일부 구간 재검토')
        self.service.revoke_label(label['id'], '우지', '근거 부족')
        self.assertNotEqual(self.service.session(sid)['review'], 'normal')
        self.assertEqual(self.service._labeled_rows(self.service.session(sid)), [])
        self.assertEqual(self.service.labels(sid)['labels'][0]['active'], 0)

    def test_synthetic_values_cannot_enter_hardware_record(self):
        sid = self.service.start(meta())
        contaminated = frame(source='scenario')
        with self.assertRaises(ValueError):
            self.service.ingest(sid, contaminated)
        self.assertEqual(self.service.session(sid)['count'], 0)

    def test_invalid_units_and_stale_values_are_not_normal(self):
        sid = self.service.start(meta())
        wrong = {'seq': 1, 'elapsed_s': 2, 'phase': 'running', 'quality': 'ok',
                 'readings': {'vibration_rms_g': {'value': .05, 'unit': 'm/s2',
                    'age_s': 0, 'quality': 'ok', 'source': 'recorded_imu'},
                    'envelope_cv': {'value': .04, 'unit': '1', 'age_s': 0,
                    'quality': 'ok', 'source': 'recorded_imu'}}}
        self.assertEqual(self.service.ingest(sid, wrong)['state'], 'unknown')
        stale = deepcopy(wrong)
        stale.update(seq=2, elapsed_s=4)
        stale['readings']['vibration_rms_g'].update(unit='g', age_s=6)
        self.assertEqual(self.service.ingest(sid, stale)['state'], 'unknown')

    def test_vibration_profile_and_numeric_frame_do_not_invent_peak_or_use_labels(self):
        self.assertEqual(PROFILES['vibration']['targets'], ['vibration_rms_g', 'envelope_cv'])
        raw = frame()
        raw.update(expected_label='increased', scenario='strong', filename='normal.csv')
        parsed = canonical_frame(raw)
        self.assertEqual(set(parsed['values']), {'vibration_rms_g', 'envelope_cv'})
        self.assertFalse({'expected_label', 'scenario', 'filename'} & set(parsed))
        sid = self.service.start(meta())
        self.assertEqual(self.service.session(sid)['meta']['sensor_config'], 'legacy-range-unknown')

    def test_window_duration_cannot_fabricate_label_coverage(self):
        for duration in (-2., 0., float('nan'), float('inf')):
            with self.subTest(duration=duration):
                raw = frame()
                raw['duration_s'] = duration
                with self.assertRaises(ValueError):
                    canonical_frame(raw)

    def test_dependency_revision_and_original_prediction_survive_restart(self):
        sid = self.service.start(meta())
        original = self.service.ingest(sid, frame())
        self.service.stop()
        self.service.review(sid, 'normal', 'fit', '우지', '정상 확인')
        expected = self.service.feedback_fingerprint(sid)
        model = {'signature': signature(self.service.session(sid)['meta']),
                 'splits': {'fit': [sid], 'calibration': [], 'validation': []},
                 'dependencies': {sid: expected}}
        self.assertTrue(self.service.dependencies_valid(model))
        self.service.review(sid, 'normal', 'fit', '우지', '근거 수정도 새로운 버전')
        self.assertFalse(self.service.dependencies_valid(model))
        self.service.note(sid, '체결 상태 재점검', '우지')
        self.service.close()
        self.service = Service(self.temp.name)
        self.assertEqual(self.service.frames(sid)[0]['result'], original)
        self.assertEqual(self.service.report(sid)['notes'][0]['body'], '체결 상태 재점검')
        self.assertFalse(self.service.dependencies_valid(model))



class LabelValidationAudit(unittest.TestCase):
    @staticmethod
    def dataset(outside_role):
        dataset = {role: [] for role in ('fit', 'calibration', 'validation')}
        for role in dataset:
            for label in ('normal', 'abnormal'):
                for index in range(40):
                    values = {'vibration_rms_g': .05 if label == 'normal' else .2,
                              'envelope_cv': .04, 'engine_rpm': 1800., 'coolant_c': 80.,
                              'oil_bar': 3., 'voltage_v': 208., 'current_a': 10.,
                              'load_pct': 90. if role == outside_role else 50.}
                    dataset[role].append({'frame': {'values': values, 'quality': 'ok', 'phase': 'running'},
                                          'label': label, 'session_id': f'{role}-{label}-{index % 2}'})
        return dataset

    def test_label_validation_cannot_pass_data_runtime_refuses_to_score(self):
        data = self.dataset('validation')
        model = fit_label_model(data, 'fusion', {'source_kind': 'synthetic'}, {}, {}, {r: 1. for r in data})
        with self.assertRaises(ValueError):
            predict_label(model, data['validation'][0]['frame'])
        self.assertFalse(model['passed'])
        self.assertEqual(model['validation']['coverage'], 0.)

    def test_label_calibration_cannot_select_boundary_on_unseen_operating_context(self):
        data = self.dataset('calibration')
        with self.assertRaises(ValueError):
            fit_label_model(data, 'fusion', {'source_kind': 'synthetic'}, {}, {}, {r: 1. for r in data})


class ExistingRecordingAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = LearningWorkspace(self.temp.name)

    def tearDown(self):
        self.workspace.close()
        self.temp.cleanup()

    def test_two_original_recordings_keep_shared_installation_signature_and_no_human_label(self):
        imported = [self.workspace.dispatch('import-recording', {'recording': key}) for key in ('training', 'random')]
        sessions = [self.workspace.service.session(r['session_id']) for r in imported]
        self.assertEqual(signature(sessions[0]['meta']), signature(sessions[1]['meta']))
        self.assertEqual([s['count'] for s in sessions], [48, 48])
        self.assertEqual([s['review'] for s in sessions], ['unreviewed', 'unreviewed'])
        self.assertEqual([s['role'] for s in sessions], ['none', 'none'])
        self.assertEqual(len(self.workspace.service.list_sessions()), 2)
        for row in imported:
            detail = self.workspace.session_detail(row['session_id'])
            for item in detail['frames']:
                self.assertEqual(item['frame']['duration_s'], 2)
                self.assertNotIn('expected_label', item['frame']['values'])
                self.assertEqual(set(item['frame']['sources'].values()), {'recorded_imu'})
        with self.assertRaises(ValueError):
            self.workspace.service.feedback_training([s['id'] for s in sessions])

    def test_new_learning_demo_keeps_the_selected_208v_equipment_reference(self):
        frames = list(self.workspace._demo_frames('voltage-check', 71, 'normal', count=30))
        volts = [f['readings']['voltage_v']['value'] for f in frames]
        self.assertTrue(all(205 < value < 211 for value in volts), volts[:3])
        self.assertEqual(set(f['readings']['voltage_v']['source'] for f in frames), {'scenario'})

    def test_reimport_after_ui_history_limit_does_not_create_independent_copy(self):
        first = self.workspace.dispatch('import-recording', {'recording': 'training'})
        for index in range(301):
            config = meta('synthetic')
            config['label'] = f'별도 기록 {index}'
            self.workspace.service.start(config)
            self.workspace.service.stop()
        again = self.workspace.dispatch('import-recording', {'recording': 'training'})
        self.assertEqual(again['session_id'], first['session_id'])
        self.assertTrue(again['already_imported'])


if __name__ == '__main__':
    unittest.main()
