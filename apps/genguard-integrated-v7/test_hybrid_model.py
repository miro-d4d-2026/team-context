import copy
import json
from pathlib import Path
import unittest

import numpy as np
from hybrid_model import HybridAnalyzer, fit_vibration, score_vibration

BASE = Path(__file__).parent / 'model_bundle'
VALUES = dict(engine_rpm=1800, coolant_c=80, oil_kgf_cm2=3.2, voltage_ll_v=208,
              load_pct=62, ambient_c=28.5)


def windows(rms=.018, cv=.12):
    return [dict(window_id=f'w-{i}', source='live_imu', quality={'valid': True, 'flags': []}, features={
        'vibration_rms_g': rms * (1 + .008 * np.sin(i)),
        'envelope_cv': cv * (1 + .02 * np.cos(i))}) for i in range(15)]


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((BASE/'profile.json').read_text())
        self.bundle = json.loads((BASE/'model.json').read_text())
        self.original = copy.deepcopy(self.bundle)
        self.model = HybridAnalyzer(self.profile, self.bundle)

    def test_normal(self):
        result = self.model.score_generator(VALUES)
        self.assertEqual(result['state'], 'within_baseline')
        self.assertLess(result['score'], 2)
        self.assertEqual(self.original, self.bundle)

    def test_joint_change_within_fixed_limits(self):
        result = self.model.score_generator(dict(VALUES, coolant_c=89, oil_kgf_cm2=2.6))
        self.assertEqual(result['state'], 'change')
        self.assertGreater(result['score'], 2)
        self.assertTrue(result['checks'])

    def test_overheat(self):
        self.assertEqual(self.model.score_generator(dict(VALUES, coolant_c=100))['state'], 'change')

    def test_invalid_missing_nan_boolean_and_out_of_range(self):
        for value in [None, float('nan'), float('inf'), True, -1]:
            with self.subTest(value=value):
                self.assertEqual(self.model.score_generator(dict(VALUES, engine_rpm=value))['state'], 'unknown')

    def test_stale_and_missing_quality(self):
        for quality in [{'age_s': 6}, {'quality': 'missing'}, {'age_s': -1}]:
            self.assertEqual(self.model.score_generator(VALUES, {'coolant_c': quality})['state'], 'unknown')

    def test_outside_context(self):
        self.assertEqual(self.model.score_generator(dict(VALUES, load_pct=145))['state'], 'unknown')

    def test_pressure_units_display_converted_back(self):
        result = self.model.score_generator(VALUES)
        oil = next(x for x in result['evidence'] if x['field']=='oil_kgf_cm2')
        self.assertAlmostEqual(oil['value'], 3.2)
        self.assertEqual(oil['unit'], 'kgf/cm²')
        self.assertGreater(oil['expected'], 2)
        self.assertLess(oil['expected'], 4)

    def test_marginal_covariance_not_precision_submatrix(self):
        indices = [0,1,2,3]
        full = np.asarray(self.bundle['phases']['running']['precision'])
        expected = np.linalg.inv(np.linalg.inv(full)[np.ix_(indices, indices)])
        np.testing.assert_allclose(self.model.precision, expected)

    def test_vibration_cannot_affect_generator_score(self):
        a = self.model.score_generator(VALUES)
        b = self.model.score_generator(dict(VALUES, vibration_rms_g=25, envelope_cv=90))
        self.assertEqual(a, b)


class VibrationTests(unittest.TestCase):
    def setUp(self):
        self.windows = windows()
        self.baseline = fit_vibration(self.windows)

    def test_learns_actual_fixture_scale_not_synthetic_045(self):
        self.assertAlmostEqual(self.baseline['normal_values']['vibration_rms_g'], .018, places=3)
        self.assertEqual(score_vibration({'vibration_rms_g':.018,'envelope_cv':.12}, self.baseline)['state'], 'within_baseline')
        self.assertEqual(self.baseline['training_count'], 10)
        self.assertEqual(self.baseline['validation_count'], 5)
        self.assertTrue(self.baseline['validation']['passed'])

    def test_doubled_vibration(self):
        self.assertEqual(score_vibration({'vibration_rms_g':.036,'envelope_cv':.12}, self.baseline)['state'], 'change')

    def test_intermittent_cv(self):
        self.assertEqual(score_vibration({'vibration_rms_g':.018,'envelope_cv':.8}, self.baseline)['state'], 'change')

    def test_stopped_source_is_change_after_learning(self):
        self.assertEqual(score_vibration({'vibration_rms_g':0,'envelope_cv':0}, self.baseline)['state'], 'change')

    def test_no_baseline_is_unknown(self):
        self.assertEqual(score_vibration({}, None)['state'], 'unknown')

    def test_invalid_features_is_unknown(self):
        for value in [float('nan'), True, -1, 31, None]:
            self.assertEqual(score_vibration({'vibration_rms_g':value,'envelope_cv':.1}, self.baseline)['state'], 'unknown')

    def test_no_duplicate_window_training(self):
        data = self.windows[:14] + [self.windows[0]]
        with self.assertRaisesRegex(ValueError, 'unique'):
            fit_vibration(data)

    def test_too_few_and_quiet_windows(self):
        with self.assertRaises(ValueError):
            fit_vibration(self.windows[:10])
        with self.assertRaisesRegex(ValueError, 'too low'):
            fit_vibration(windows(rms=.0001))

    def test_unstable_training_rejected(self):
        data = windows()
        for i in range(5):
            data[i]['features']['vibration_rms_g'] *= 4
        with self.assertRaisesRegex(ValueError, 'unstable'):
            fit_vibration(data)

    def test_held_out_change_rejects_baseline(self):
        data = windows()
        data[-1]['features']['vibration_rms_g'] *= 4
        with self.assertRaisesRegex(ValueError, 'validation'):
            fit_vibration(data)

    def test_bad_quality_rejected(self):
        data = windows()
        data[2]['quality'] = {'quality':'stale'}
        with self.assertRaisesRegex(ValueError, 'quality'):
            fit_vibration(data)

    def test_packet_invalid_quality_rejected(self):
        data = windows()
        data[2]['quality'] = {'valid': False, 'flags': ['gap']}
        with self.assertRaisesRegex(ValueError, 'quality'):
            fit_vibration(data)

    def test_recorded_source_preserved_and_mixed_rejected(self):
        data = windows()
        for packet in data:
            packet['source'] = 'recorded_imu'
        baseline = fit_vibration(data)
        self.assertEqual(score_vibration(data[0]['features'], baseline)['source'], 'recorded_imu')
        data[1]['source'] = 'live_imu'
        with self.assertRaisesRegex(ValueError, 'source'):
            fit_vibration(data)

    def test_json_reproducible_no_mutation(self):
        before = copy.deepcopy(self.baseline)
        score_vibration({'vibration_rms_g':.1,'envelope_cv':.3}, self.baseline)
        self.assertEqual(before, self.baseline)
        self.assertEqual(fit_vibration(windows())['baseline_id'], self.baseline['baseline_id'])
        self.assertEqual(json.loads(json.dumps(self.baseline, allow_nan=False)), self.baseline)

    def test_independent_mount_baselines(self):
        stronger = fit_vibration(windows(rms=.25))
        self.assertEqual(score_vibration({'vibration_rms_g':.25,'envelope_cv':.12}, stronger)['state'], 'within_baseline')
        self.assertEqual(score_vibration({'vibration_rms_g':.25,'envelope_cv':.12}, self.baseline)['state'], 'change')


if __name__ == '__main__':
    unittest.main()
