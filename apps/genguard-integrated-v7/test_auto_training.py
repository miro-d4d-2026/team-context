"""Automatic user workflow: review evidence stays independent of ML split roles."""
import json
import math
import tempfile
import threading
import unittest
from unittest.mock import patch
from learning_workspace import LearningWorkspace


class AutomaticTrainingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = LearningWorkspace(self.tmp.name)
        self.s = self.w.service

    def tearDown(self):
        self.w.close()
        self.tmp.cleanup()

    def record(self, label='normal', role=None, review='normal', installation='fixed', segmented=False, count=40):
        meta = {'asset_id': 'demo', 'installation_id': installation, 'label': label, 'condition': 'fixture',
                'profile': 'vibration', 'source_kind': 'synthetic', 'sensor_config': 'test'}
        sid = self.s.start(meta)
        for i in range(count * (2 if segmented else 1)):
            abnormal = label == 'abnormal' or (segmented and i >= count)
            self.s.ingest(sid, {'seq': i+1, 'elapsed_s': (i+1)*2.,
                               'values': {'vibration_rms_g': (.15 if abnormal else .05)+.001*math.sin(i),
                                          'envelope_cv': (.7 if abnormal else .12)+.003*math.cos(i)},
                               'sources': {'vibration_rms_g': 'scenario', 'envelope_cv': 'scenario'}})
        self.s.stop()
        if segmented:
            self.s.add_label(sid, 'normal', 0, count*2, 'tester', 'reviewed normal')
            self.s.add_label(sid, 'abnormal', count*2, count*4, 'tester', 'reviewed abnormal')
        else:
            self.w.dispatch('review', {'session_id': sid, 'status': review if label == 'normal' else label,
                                      'reviewer': 'tester', 'note': 'human checked', **({'role': role} if role else {})})
        return sid

    def train(self, ids):
        self.w.dispatch('train', {'kind': 'auto', 'session_ids': ids})
        self.s.training_thread.join(10)
        self.assertNotEqual(self.s.job['state'], 'running')
        return self.s.job

    def test_normal_initial_learning_preserves_reviews_and_explicit_activation(self):
        ids = [self.record() for _ in range(4)]
        before = {sid: self.s.feedback_fingerprint(sid) for sid in ids}
        p = self.w.dispatch('training-plan', {'session_ids': ids})
        self.assertTrue(p['normal']['ready'])
        self.assertFalse(p['feedback']['ready'])
        self.assertEqual([len(p['normal']['splits'][r]) for r in ['fit', 'calibration', 'validation']], [2, 1, 1])
        self.assertEqual(p, self.w.dispatch('training-plan', {'session_ids': list(reversed(ids))}))
        self.assertEqual(self.train(ids)['state'], 'completed')
        model = self.s.models()[0]
        self.assertFalse(model['active'])
        self.assertEqual(model['auto_training']['normal_sessions'], 4)
        self.assertEqual(model['auto_training']['evaluation']['kind'], 'new_holdout')
        self.assertIsNone(model['auto_training']['evaluation_metrics']['abnormal_recall'])
        self.assertFalse(model['auto_training']['incumbent_comparison']['available'])
        self.assertEqual(before, {sid: self.s.feedback_fingerprint(sid) for sid in ids})
        self.assertEqual({self.s.session(sid)['role'] for sid in ids}, {'none'})
        self.s.activate(model['id'])
        current = self.s.start(self.s.session(ids[0])['meta'])
        self.assertEqual(self.s.session(current)['model_id'], model['id'])
        with self.assertRaises(ValueError): self.w.dispatch('train', {'kind': 'auto', 'session_ids': ids})
        self.s.stop()

    def test_retraining_shares_frozen_holdout_and_compares_incumbent(self):
        normal = [self.record() for _ in range(4)]
        self.assertEqual(self.train(normal)['state'], 'completed')
        old = self.s.models()[0]
        self.s.activate(old['id'])
        abnormal = [self.record('abnormal') for _ in range(4)]
        # More approved normal records accumulate into fitting, not old evaluation.
        new = self.record()
        ids = normal + abnormal + [new]
        p = self.s.automatic_plan(ids)
        self.assertTrue(p['feedback']['ready'])
        self.assertEqual(p['normal']['evaluation']['kind'], 'fixed_holdout')
        self.assertTrue(set(old['splits']['validation']) <= set(p['normal']['splits']['validation']))
        historical = set(old['splits']['fit'] + old['splits']['calibration'])
        self.assertFalse(historical & set(p['feedback']['splits']['validation']))
        self.assertEqual(self.train(ids)['state'], 'completed')
        n, f = self.s.models()[0], self.s.feedback_models()[0]
        self.assertFalse(n['active']); self.assertFalse(f['active'])
        self.assertEqual([m['id'] for m in self.s.models() if m['active']], [old['id']])
        self.assertTrue(n['auto_training']['incumbent_comparison']['available'])
        self.assertEqual(n['auto_training']['incumbent_comparison']['result'], 'unchanged')
        self.assertEqual(f['auto_training']['normal_sessions'], 5)
        self.assertEqual(f['auto_training']['abnormal_sessions'], 4)
        self.assertGreaterEqual(f['validation']['abnormal_recall'], .8)
        self.assertLessEqual(f['validation']['false_positive_rate'], .1)
        self.assertFalse(set(n['splits']['fit'] + n['splits']['calibration']) & set(f['splits']['validation']))
        frozen = json.dumps(n['auto_training'], sort_keys=True)
        self.w.close(); self.w = LearningWorkspace(self.tmp.name); self.s = self.w.service
        self.assertEqual(frozen, json.dumps(self.s.models()[0]['auto_training'], sort_keys=True))
        self.assertEqual([m['id'] for m in self.s.models() if m['active']], [old['id']])

    def test_four_segmented_runs_can_train_both_models_without_window_leakage(self):
        ids = [self.record(segmented=True) for _ in range(4)]
        p = self.s.automatic_plan(ids)
        self.assertTrue(p['feedback']['ready'])
        self.assertEqual(p['normal']['splits'], p['feedback']['splits'])
        self.assertEqual(self.train(ids)['state'], 'completed')
        f = self.s.feedback_models()[0]
        self.assertEqual(f['auto_training']['segmented_sessions'], 4)
        self.assertEqual(f['auto_training']['normal_sessions'], 4)
        self.assertEqual(f['auto_training']['abnormal_sessions'], 4)

    def test_shortage_and_excluded_quality_are_explained(self):
        ids = [self.record() for _ in range(3)]
        short = self.record(count=10)
        uncertain = self.record(review='uncertain')
        p = self.s.automatic_plan(ids + [short, uncertain])
        self.assertFalse(p['ready'])
        self.assertIn('학습 데이터 부족', p['reason'])
        self.assertEqual({r['session_id'] for r in p['excluded']}, {short, uncertain})
        with self.assertRaises(ValueError): self.train(ids)
        fourth = self.record()
        abnormal = self.record('abnormal')
        p = self.s.automatic_plan(ids + [fourth, abnormal])
        self.assertTrue(p['normal']['ready']); self.assertFalse(p['feedback']['ready'])
        self.assertEqual(self.train(ids + [fourth, abnormal])['state'], 'completed')
        self.assertFalse(self.s.feedback_models())

    def test_mixed_signature_duplicate_ids_and_limit_rejected(self):
        ids = [self.record() for _ in range(4)]
        other = self.record(installation='moved')
        self.assertFalse(self.s.automatic_plan(ids+[other])['ready'])
        self.assertFalse(self.s.automatic_plan(ids+[ids[0]])['ready'])
        self.assertFalse(self.s.automatic_plan(['unknown-'+str(i) for i in range(31)])['ready'])
        with self.assertRaises(ValueError): self.train(ids+[other])
        self.assertFalse(self.s.models())

    def test_incumbent_comparison_requires_each_class_denominator(self):
        ids = [self.record() for _ in range(4)]
        self.train(ids)
        old = self.s.models()[0]
        self.s.activate(old['id'])
        items = self.s._labeled_rows(self.s.session(old['splits']['validation'][0]))
        metrics = {'false_positive_rate': None, 'abnormal_recall': None, 'coverage': .99}
        with patch.object(self.s, '_candidate_evaluation', return_value=metrics):
            result = self.s._compare_incumbent(old, items, 'normal')
        self.assertFalse(result['available'])
        self.assertIn('클래스별', result['reason'])

    def test_review_without_role_preserves_legacy_role(self):
        sid = self.record(role='validation')
        self.w.dispatch('review', {'session_id': sid, 'status': 'abnormal', 'reviewer': 'test', 'note': 'rechecked'})
        self.assertEqual(self.s.session(sid)['role'], 'validation')

    def test_history_never_becomes_fresh_evaluation_after_candidate_rejected(self):
        ids = [self.record() for _ in range(4)]
        self.train(ids)
        old = self.s.models()[0]
        # Even a failed/inactive prior candidate has consumed its fit data.
        with self.s.lock:
            old['passed'] = False
            self.s.db.execute('UPDATE models SET data=? WHERE id=?', (json.dumps(old), old['id']))
            self.s.db.commit()
        # Exclude the prior evaluation by review: remaining consumed records cannot
        # turn into an apparently new holdout, even though 4+ records are available.
        self.w.dispatch('review', {'session_id': old['splits']['validation'][0], 'status': 'uncertain', 'reviewer': 'test', 'note': 'awaiting check'})
        p = self.s.automatic_plan(ids)
        self.assertFalse(p['ready'])
        extra = self.record()
        p = self.s.automatic_plan(ids+[extra])
        self.assertTrue(p['normal']['ready'])
        self.assertEqual(p['normal']['splits']['validation'], [extra])

    def test_review_change_and_capture_preempt_cancel_without_new_candidates(self):
        ids = [self.record() for _ in range(4)]
        started, release = threading.Event(), threading.Event()
        from review_learning.auto_training import fit_model as real_fit
        def slow(*args):
            started.set(); release.wait(2); return real_fit(*args)
        with patch('review_learning.auto_training.fit_model', slow):
            self.w.dispatch('train', {'kind': 'auto', 'session_ids': ids})
            self.assertTrue(started.wait(2))
            self.s.start(self.s.session(ids[0])['meta'])
            release.set(); self.s.training_thread.join(5)
        self.assertEqual(self.s.job['state'], 'cancelled')
        self.assertFalse(self.s.models()); self.s.stop()
        started.clear(); release.clear()
        with patch('review_learning.auto_training.fit_model', slow):
            self.w.dispatch('train', {'kind': 'auto', 'session_ids': ids})
            self.assertTrue(started.wait(2))
            self.w.dispatch('review', {'session_id': ids[0], 'status': 'uncertain', 'reviewer': 'test', 'note': 'recheck'})
            release.set(); self.s.training_thread.join(5)
        self.assertEqual(self.s.job['state'], 'failed')
        self.assertFalse(self.s.models())

if __name__ == '__main__': unittest.main()
