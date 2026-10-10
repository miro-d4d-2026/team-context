"""Deterministic checks for measured-input/synthetic-output separation."""
import unittest

from vibration_driven_sim import VibrationDrivenSimulator, VALUE_FIELDS


BASELINE = {'normal_values': {'vibration_rms_g': .055}}


def packet(seq, rms=.055, cv=.1, **changes):
    result = {'state': 'fresh', 'source': 'live_imu', 'window_id': f'imu:{seq}',
              'features': {'vibration_rms_g': rms, 'envelope_cv': cv}}
    result.update(changes)
    return result


class VibrationDrivenSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.sim = VibrationDrivenSimulator()

    def tick(self, now, rms=.055, cv=.1):
        return self.sim.update(packet(now, rms, cv), BASELINE, now)

    def run_until(self, end):
        result = None
        for now in range(0, end + 1, 2):
            result = self.tick(now)
        return result

    def test_idle_is_zero_and_coolant_starts_at_ambient(self):
        self.tick(0, rms=.001)
        result = self.tick(2, rms=.001)
        self.assertEqual(result['phase'], 'stopped')
        for field in ('engine_rpm', 'load_pct', 'voltage_ll_v', 'oil_kgf_cm2'):
            self.assertEqual(result['values'][field], 0)
        self.assertEqual(result['values']['coolant_c'], 28.5)

    def test_normal_start_has_ordered_ramp_then_warms(self):
        self.tick(0)
        started = self.tick(2)
        self.assertEqual(started['phase'], 'starting')
        middle = self.tick(6)
        self.assertEqual(middle['values']['engine_rpm'], 900)
        self.assertEqual(middle['values']['voltage_ll_v'], 104)
        self.assertEqual(middle['values']['load_pct'], 0)
        self.assertGreater(middle['values']['oil_kgf_cm2'], 0)
        self.assertGreater(middle['values']['coolant_c'], 28.5)
        at_speed = self.tick(10)
        self.assertEqual(at_speed['values']['engine_rpm'], 1800)
        self.assertEqual(at_speed['values']['load_pct'], 0)
        self.tick(14)
        loaded = self.tick(18)
        self.assertEqual(loaded['values']['load_pct'], 62)
        self.assertEqual(loaded['phase'], 'running')
        self.assertLess(loaded['values']['coolant_c'], 80)

    def test_stop_coasts_and_cools_without_instant_temperature_reset(self):
        running = self.run_until(100)
        self.tick(102, rms=.001)
        stopping = self.tick(104, rms=.001)
        self.assertEqual(stopping['phase'], 'coasting')
        hot = stopping['values']['coolant_c']
        self.assertGreaterEqual(hot, running['values']['coolant_c'])
        self.tick(106, rms=.001)
        stopped = self.tick(108, rms=.001)
        self.assertEqual(stopped['phase'], 'stopped')
        self.assertEqual(stopped['values']['engine_rpm'], 0)
        self.assertEqual(stopped['values']['oil_kgf_cm2'], 0)
        self.assertLess(stopped['values']['coolant_c'], hot)
        self.assertGreater(stopped['values']['coolant_c'], 28.5)

    def test_same_window_does_not_confirm_pattern(self):
        sample = packet(1)
        self.sim.update(sample, BASELINE, 0)
        for now in (.1, .5, 1, 2):
            result = self.sim.update(sample, BASELINE, now)
            self.assertEqual(result['pattern'], 'unknown')
            self.assertEqual(result['values']['engine_rpm'], 0)
        result = self.sim.update(packet(2), BASELINE, 2.1)
        self.assertEqual(result['pattern'], 'normal')

    def test_missing_data_freezes_and_draws_gaps_then_no_catch_up(self):
        running = self.run_until(40)
        frozen = self.sim.update({'state': 'stale'}, BASELINE, 42)
        self.assertEqual(frozen['phase'], 'signal_lost')
        self.assertEqual(frozen['values'], running['values'])
        self.assertTrue(all(frozen['history'][-1][key] is None for key in VALUE_FIELDS))
        self.assertFalse(frozen['history'][-1]['valid'])
        recovered = self.tick(1042)
        self.assertEqual(recovered['values'], running['values'])
        self.assertEqual(len(recovered['history']), 180)
        self.assertEqual(recovered['pattern'], 'normal')
        resumed = self.tick(1044)
        self.assertGreater(resumed['values']['coolant_c'], recovered['values']['coolant_c'])

    def test_invalid_recorded_and_nonfinite_values_cannot_run_machine(self):
        for sample in (packet(1, state='invalid'), packet(2, source='recorded_imu'),
                       packet(3, rms=float('nan')), packet(4, cv=float('inf'))):
            result = self.sim.update(sample, BASELINE, 0)
            self.assertEqual(result['phase'], 'waiting')
            self.assertEqual(result['values']['engine_rpm'], 0)

    def test_claimed_fresh_duplicate_expires(self):
        self.run_until(20)
        result = self.sim.update(packet(20), BASELINE, 26)
        self.assertEqual(result['phase'], 'signal_lost')

    def test_strong_changes_vibration_pattern_without_fake_rpm_drop(self):
        self.run_until(20)
        self.tick(22, rms=.156)
        result = self.tick(24, rms=.156)
        self.assertEqual(result['pattern'], 'strong')
        self.assertEqual(result['values']['engine_rpm'], 1800)
        self.assertEqual(result['values']['voltage_ll_v'], 208)
        self.assertEqual(result['values']['load_pct'], 62)

    def test_intermittent_is_explicit_synthetic_instability(self):
        self.run_until(20)
        self.tick(22, cv=1.0)
        result = self.tick(24, cv=1.0)
        self.assertEqual(result['phase'], 'irregular')
        self.assertEqual(result['pattern'], 'intermittent')
        self.assertLess(result['values']['engine_rpm'], 1800)
        self.assertIn('모의', result['message'])
        later = self.sim.update(packet(24, cv=1.0), BASELINE, 24.5)
        self.assertNotEqual(later['values']['engine_rpm'], result['values']['engine_rpm'])

    def test_poll_rate_does_not_change_values_or_history(self):
        slow, fast = VibrationDrivenSimulator(), VibrationDrivenSimulator()
        for now in range(0, 61, 2):
            slow_result = slow.update(packet(now), BASELINE, now)
        for quarter in range(241):
            now = quarter / 4
            fast_result = fast.update(packet(int(now // 2) * 2), BASELINE, now)
        for field in VALUE_FIELDS:
            self.assertAlmostEqual(slow_result['values'][field], fast_result['values'][field], places=10)
        self.assertEqual(slow_result['history'], fast_result['history'])

    def test_threshold_defaults_are_disclosed_and_hysteresis_avoids_noise(self):
        first = self.sim.update(packet(1, rms=.001), None, 0)
        self.assertEqual(first['assumptions']['reference_source'], 'demo_default')
        self.sim.update(packet(2, rms=.001), None, 2)
        for now in (4, 6, 8):
            result = self.sim.update(packet(now, rms=.020), None, now)
        self.assertEqual(result['pattern'], 'idle')
        self.assertEqual(result['values']['engine_rpm'], 0)

    def test_deadband_retains_operation_without_retaining_old_anomaly_pattern(self):
        self.run_until(20)
        for previous_rms, previous_cv in ((.156, .1), (.055, 1.0)):
            at = self.sim.last_now
            self.tick(at + 2, rms=previous_rms, cv=previous_cv)
            previous = self.tick(at + 4, rms=previous_rms, cv=previous_cv)
            self.assertIn(previous['pattern'], ('strong', 'intermittent'))
            self.tick(at + 6, rms=.020)
            result = self.tick(at + 8, rms=.020)
            self.assertEqual(result['pattern'], 'normal')
            self.assertEqual(result['phase'], 'running')
            self.assertEqual(result['values']['engine_rpm'], 1800)
            self.assertNotIn('강한 실측', result['message'])

    def test_measured_resting_background_stops_then_normal_restarts(self):
        self.run_until(20)
        first = self.tick(22, rms=.010)
        self.assertEqual(first['pattern'], 'normal')
        stopping = self.tick(24, rms=.010)
        self.assertEqual(stopping['pattern'], 'idle')
        self.assertEqual(stopping['phase'], 'coasting')
        self.assertAlmostEqual(stopping['assumptions']['stop_threshold_g'], .0165)
        self.assertAlmostEqual(stopping['assumptions']['start_threshold_g'], .025)
        self.tick(26, rms=.0049)
        stopped = self.tick(28, rms=.0096)
        self.assertEqual(stopped['phase'], 'stopped')
        self.assertEqual(stopped['values']['engine_rpm'], 0)
        self.tick(30, rms=.055)
        restarting = self.tick(32, rms=.055)
        self.assertEqual(restarting['phase'], 'starting')
        self.assertEqual(restarting['pattern'], 'normal')


if __name__ == '__main__':
    unittest.main()
