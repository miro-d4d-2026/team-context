"""Real HTTP + Node rule tests with deterministic synthetic IMU fixtures.

The injected clock accelerates 30-second calibration in tests. These are local
software integration tests, not physical UNO Q or generator measurements.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import math
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from adapter.imu_to_api import FeatureWindows, utc_text
from hybrid_service import HybridBridgeService
from server import Handler, HTTPServer

ROOT = Path(__file__).resolve().parent
TOKEN = 'integration-test-token-not-a-deployment-secret'
ORIGIN = 'http://127.0.0.1:54321'


class FakeClock:
    def __init__(self):
        self.epoch = 1_800_000_000.0
        self.elapsed = 1000.0
        self.lock = threading.Lock()

    def wall(self):
        with self.lock:
            return self.epoch

    def monotonic(self):
        with self.lock:
            return self.elapsed

    def advance(self, seconds):
        with self.lock:
            self.epoch += seconds
            self.elapsed += seconds


class QuietHandler(Handler):
    def log_message(self, fmt, *args):
        pass


@contextmanager
def running_server(clock, state_dir):
    """Create SQLite, serve HTTP, and close SQLite on one owner thread."""
    ready = queue.Queue()
    errors = []

    def run():
        service = server = None
        try:
            service = HybridBridgeService(state_dir, clock=clock.wall, monotonic=clock.monotonic)
            server = HTTPServer(('127.0.0.1', 0), QuietHandler)
            server.service, server.token = service, TOKEN
            server.allowed_origins, server.loopback_only = {ORIGIN}, True
            ready.put(server)
            server.serve_forever(poll_interval=.01)
        except BaseException as error:
            errors.append(error)
            if server is None:
                ready.put(error)
        finally:
            if server:
                server.server_close()
            if service:
                service.close()

    thread = threading.Thread(target=run, name='genguard-api-test', daemon=True)
    thread.start()
    item = ready.get(timeout=15)
    if isinstance(item, BaseException):
        thread.join(timeout=5)
        raise item
    try:
        yield f'http://127.0.0.1:{item.server_port}'
    finally:
        item.shutdown()
        thread.join(timeout=10)
        if thread.is_alive():
            raise AssertionError('Test server did not stop')
        if errors:
            raise errors[0]


def request(base, path, payload=None, *, method=None, token=TOKEN, origin=None, raw=None, extra_headers=None):
    headers = dict(extra_headers or {})
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    if origin:
        headers['Origin'] = origin
    data = raw if raw is not None else (json.dumps(payload, allow_nan=False).encode() if payload is not None else None)
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = Request(base + path, data=data, method=method or ('POST' if data is not None else 'GET'), headers=headers)
    try:
        response = urlopen(req, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            decoded = body.decode(errors='replace')
        return response.status, decoded, dict(response.headers)


def adapter_template():
    """Use the real adapter serializer, fed a clearly synthetic 20 Hz waveform."""
    builder = FeatureWindows('test-mount', 'imu-test')
    builder.set_meta({'odr_hz': 208, 'accel_range_g': 4})
    frames = []
    for i in range(418):
        stamp = round(i * 1_000_000 / 208)
        frames += builder.add_sample({'type': 'sample', 'seq': i, 't_us': stamp,
            'ax_g': .06 * math.sin(2 * math.pi * 20 * stamp / 1_000_000), 'ay_g': 0., 'az_g': 1.})
    return builder.as_packet(frames[0], source='live_imu', measured_at=1_800_000_000)


class HybridAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protected = {name: hashlib.sha256((ROOT / 'model_bundle' / name).read_bytes()).hexdigest()
                         for name in ('model.json', 'profile.json', 'baseline.sqlite')}
        cls.template = adapter_template()

    @classmethod
    def tearDownClass(cls):
        for name, expected in cls.protected.items():
            actual = hashlib.sha256((ROOT / 'model_bundle' / name).read_bytes()).hexdigest()
            if actual != expected:
                raise AssertionError('Frozen model was mutated: ' + name)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='genguard-hybrid-http-')
        self.state_dir = Path(self.directory.name) / 'state'
        self.clock = FakeClock()
        self.context = running_server(self.clock, self.state_dir)
        self.base = self.context.__enter__()
        self.session = None
        self.seq = 0
        self.imu_seq = 0
        self.stream = 'imu-' + uuid4().hex
        self.source = 'live_imu'
        code, self.config, _ = request(self.base, '/api/config')
        self.assertEqual(code, 200, self.config)
        self.defaults = self.config['default_values']

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.directory.cleanup()

    def get(self, path):
        code, body, _ = request(self.base, path)
        self.assertEqual(code, 200, body)
        return body

    def start(self, request_id=None):
        code, body, _ = request(self.base, '/api/sessions', {'request_id': request_id or str(uuid4())})
        self.assertEqual(code, 200, body)
        self.session = body['session_id']
        return body

    def imu(self, *, features=None, valid=True, advance=2.0, **updates):
        self.clock.advance(advance)
        self.imu_seq += 1
        packet = copy.deepcopy(self.template)
        packet.update(stream_id=self.stream, source=self.source, window_seq=self.imu_seq,
                      measured_at=utc_text(self.clock.wall()))
        if features:
            packet['features'].update(features)
        if not valid:
            packet['quality'] = {'valid': False, 'flags': ['missing_sample_sequence']}
            packet['features'] = {'vibration_rms_g': None, 'envelope_cv': None}
        packet.update(updates)
        code, body, _ = request(self.base, '/api/imu/windows', packet)
        self.assertEqual(code, 200, body)
        self.assertTrue(body['accepted'])
        return packet, body

    def calibrate(self, features=None):
        self.imu(features=features)
        code, result, _ = request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['calibration']['count'], 0)
        for _ in range(15):
            self.imu(features=features)
        status = self.get('/api/imu')
        self.assertEqual(status['calibration']['state'], 'ready', status)
        self.assertEqual(status['calibration']['count'], 15)
        self.assertIsNotNone(status['calibration']['baseline_id'])
        return status

    def sample(self, overrides=None, **extra):
        if not self.session:
            self.start()
        self.seq += 1
        payload = {'seq': self.seq, 'source': 'hybrid_demo', 'phase': 'running',
                   'values': {**self.defaults, **(overrides or {})}, 'quality': {}, **extra}
        code, result, _ = request(self.base, '/api/sessions/' + self.session + '/samples', payload)
        self.assertEqual(code, 200, result)
        return result, payload

    def step(self, overrides=None, *, features=None, **extra):
        self.imu(features=features)
        return self.sample(overrides, **extra)

    def test_01_config_locks_imu_and_no_sensor_is_unknown(self):
        fields = {item['key']: item for item in self.config['fields']}
        self.assertFalse(fields['vibration_rms_g']['editable'])
        self.assertFalse(fields['envelope_cv']['editable'])
        self.assertNotIn('vibration_rms_g', self.defaults)
        self.assertEqual(self.config['data_domain'], 'hybrid_demo')
        self.assertEqual(self.config['time_policy']['kind'], 'server_elapsed')
        self.assertEqual(self.get('/api/imu')['state'], 'waiting')
        result, _ = self.sample()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['ai']['components']['vibration']['state'], 'unknown')
        self.assertEqual(result['notification_events'], [])

    def test_02_calibrates_15_new_windows_and_normal_has_no_warning(self):
        baseline = self.calibrate()
        result, _ = self.sample()
        self.assertEqual(result['status'], 'normal', result['fixed']['coverage'])
        self.assertEqual(result['ai']['state'], 'within_baseline')
        self.assertEqual(result['alerts'], [])
        self.assertEqual(result['notification_events'], [])
        self.assertEqual(result['imu']['calibration']['baseline_id'], baseline['calibration']['baseline_id'])
        self.assertEqual(result['provenance']['vibration_rms_g']['source'], 'live_imu')
        self.assertEqual(result['provenance']['coolant_c']['source'], 'scenario')
        self.assertEqual(result['ai']['components']['generator']['algorithm'], 'context-ridge-residual-covariance-marginal-4/1')
        self.assertEqual(result['ai']['components']['vibration']['algorithm'], 'log-feature-gaussian/1')

    def test_03_recorded_imu_remains_recorded_in_evidence_and_metrics(self):
        self.source = 'recorded_imu'
        self.calibrate()
        result, _ = self.sample()
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['imu']['source'], 'recorded_imu')
        self.assertEqual(result['provenance']['vibration_rms_g']['source'], 'recorded_imu')
        self.assertEqual(result['fixed']['metrics']['vibration_rms_g']['source'], 'recorded_imu')
        for item in result['ai']['components']['vibration']['evidence']:
            self.assertEqual(item['source'], 'recorded_imu')

    def test_04_web_cannot_override_sensor_values_or_quality(self):
        self.start()
        base = {'seq': 1, 'source': 'hybrid_demo', 'phase': 'running', 'values': self.defaults, 'quality': {}}
        for changes in ({'values': {**self.defaults, 'vibration_rms_g': .001}},
                        {'values': {**self.defaults, 'envelope_cv': 0}},
                        {'quality': {'vibration_rms_g': {'quality': 'ok'}}}):
            code, body, _ = request(self.base, '/api/sessions/' + self.session + '/samples', base | changes)
            self.assertEqual(code, 422, body)
            self.assertEqual(body['error']['code'], 'imu_override_forbidden')
        result, _ = self.sample()
        self.assertEqual(result['seq'], 1)

    def test_05_vibration_needs_three_unique_sensor_windows(self):
        self.calibrate()
        first, _ = self.step(features={'vibration_rms_g': .12})
        self.assertEqual(first['ai']['components']['vibration']['change_streak'], 1)
        self.assertFalse(first['notification_events'])
        for _ in range(5):
            repeated, _ = self.sample()
        self.assertEqual(repeated['ai']['components']['vibration']['change_streak'], 1)
        self.assertFalse(repeated['ai']['persistent_change'])
        second, _ = self.step(features={'vibration_rms_g': .12})
        self.assertEqual(second['ai']['components']['vibration']['change_streak'], 2)
        third, _ = self.step(features={'vibration_rms_g': .12})
        self.assertEqual(third['status'], 'watch')
        self.assertTrue(third['ai']['components']['vibration']['persistent_change'])
        self.assertTrue(any(a['id'] == 'vibration_pattern_change' for a in third['alerts']))
        self.assertTrue(third['notification_events'])
        fourth, _ = self.step(features={'vibration_rms_g': .12})
        self.assertFalse(fourth['notification_events'])

    def test_06_joint_mock_pattern_inside_fixed_limits(self):
        self.calibrate()
        for _ in range(3):
            result, _ = self.step({'coolant_c': 89, 'oil_kgf_cm2': 2.6})
        self.assertEqual(result['status'], 'watch')
        self.assertTrue(result['ai']['components']['generator']['persistent_change'])
        self.assertFalse(result['ai']['components']['vibration']['persistent_change'])
        self.assertFalse([a for a in result['alerts'] if a['kind'] == 'fixed'])
        evidence = result['ai']['components']['generator']['evidence']
        self.assertTrue({'coolant_c', 'oil_kgf_cm2'} <= {e['field'] for e in evidence})
        self.assertTrue(all(item['source'] == 'scenario' for item in evidence))

    def test_07_fresh_urgent_mock_alarm_survives_missing_imu(self):
        result, _ = self.sample({'coolant_c': 100})
        self.assertEqual(result['status'], 'urgent')
        self.assertEqual(result['imu']['state'], 'waiting')
        self.assertTrue(result['notification_events'])
        self.assertEqual(result['ai']['components']['vibration']['state'], 'unknown')

    def test_08_stale_imu_is_unknown_and_duplicate_does_not_refresh(self):
        self.calibrate()
        original, _ = self.imu()
        self.clock.advance(6)
        code, result, _ = request(self.base, '/api/imu/windows', original)
        self.assertEqual(code, 200, result)
        self.assertTrue(result['duplicate'])
        self.assertEqual(self.get('/api/imu')['state'], 'stale')
        sample_result, _ = self.sample()
        self.assertEqual(sample_result['status'], 'unknown')
        self.assertEqual(sample_result['provenance']['vibration_rms_g']['quality'], 'stale')
        urgent, _ = self.sample({'coolant_c': 100})
        self.assertEqual(urgent['status'], 'urgent')

    def test_09_invalid_window_immediately_replaces_old_fresh_value(self):
        self.calibrate()
        self.imu(valid=False)
        status = self.get('/api/imu')
        self.assertEqual(status['state'], 'invalid')
        self.assertIsNone(status['features']['vibration_rms_g'])
        result, _ = self.sample()
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['fixed']['metrics']['vibration_rms_g']['value'])

    def test_10_sensor_duplicate_conflict_order_and_expired_capture(self):
        packet, accepted = self.imu()
        code, duplicate, _ = request(self.base, '/api/imu/windows', packet)
        self.assertEqual(code, 200, duplicate)
        self.assertEqual(accepted['id'], duplicate['id'])
        self.assertTrue(duplicate['duplicate'])
        changed = copy.deepcopy(packet)
        changed['features']['vibration_rms_g'] += .01
        self.assertEqual(request(self.base, '/api/imu/windows', changed)[0], 409)
        for delta in (-6, 3):
            altered = dict(packet, window_seq=2, measured_at=utc_text(self.clock.wall() + delta))
            self.assertEqual(request(self.base, '/api/imu/windows', altered)[0], 422)
        oldtime = dict(packet, window_seq=2, measured_at=utc_text(self.clock.wall() - 1))
        self.assertEqual(request(self.base, '/api/imu/windows', oldtime)[0], 409)

    def test_11_new_stream_invalidates_baseline(self):
        self.calibrate()
        self.stream = 'imu-new-' + uuid4().hex
        self.imu_seq = 0
        self.imu()
        self.assertEqual(self.get('/api/imu')['calibration']['state'], 'failed')
        result, _ = self.sample()
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['ai']['persistent_change'])

    def test_12_calibration_requires_fresh_imu_and_confirmation(self):
        self.assertEqual(request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})[0], 409)
        self.imu()
        self.assertEqual(request(self.base, '/api/imu/calibration/start', {})[0], 400)
        self.assertEqual(request(self.base, '/api/imu/calibration/start', {'confirmed_normal': False})[0], 400)
        self.assertEqual(request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})[0], 200)
        packet, _ = self.imu()
        for _ in range(20):
            self.assertEqual(request(self.base, '/api/imu/windows', packet)[0], 200)
        self.assertEqual(self.get('/api/imu')['calibration']['count'], 1)
        self.clock.advance(6)
        self.assertEqual(self.get('/api/imu')['calibration']['state'], 'failed')

    def test_13_calibration_rejects_fast_burst_and_invalid_window(self):
        self.imu()
        request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})
        for _ in range(15):
            self.imu(advance=.1)
        self.assertEqual(self.get('/api/imu')['calibration']['state'], 'failed')
        request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})
        self.imu(valid=False)
        self.assertEqual(self.get('/api/imu')['calibration']['state'], 'failed')

    def test_14_http_sample_retry_is_cached_with_original_window_id(self):
        self.calibrate()
        first, payload = self.sample({'coolant_c': 100})
        self.imu(features={'vibration_rms_g': .12})
        code, again, _ = request(self.base, '/api/sessions/' + self.session + '/samples', payload)
        self.assertEqual(code, 200, again)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['imu']['window_id'], first['imu']['window_id'])
        self.assertEqual(again['elapsed_s'], first['elapsed_s'])
        self.assertFalse(again['notification_events'])
        payload['values']['coolant_c'] = 99
        self.assertEqual(request(self.base, '/api/sessions/' + self.session + '/samples', payload)[0], 409)

    def test_15_input_failures_do_not_consume_sequence(self):
        self.start()
        path = '/api/sessions/' + self.session + '/samples'
        base = {'seq': 1, 'source': 'hybrid_demo', 'phase': 'running', 'values': self.defaults, 'quality': {}}
        for change in ({'source': 'scenario'}, {'values': self.defaults | {'engine_rpm': '1800'}},
                       {'unexpected': True}, {'seq': 2}):
            self.assertIn(request(self.base, path, base | change)[0], (400, 409, 422))
        for raw in (b'{"seq": NaN}', b'{"seq":1,"seq":2}'):
            self.assertEqual(request(self.base, path, raw=raw)[0], 400)
        result, _ = self.sample()
        self.assertEqual(result['seq'], 1)

    def test_16_auth_cors_and_sensor_endpoint_protection(self):
        for path in ('/api/config', '/api/imu'):
            self.assertEqual(request(self.base, path, token=None)[0], 401)
        self.assertEqual(request(self.base, '/api/imu/windows', {}, token=None)[0], 401)
        self.assertEqual(request(self.base, '/api/imu/calibration/start', {}, token=None)[0], 401)
        self.assertEqual(request(self.base, '/api/imu', origin='http://unapproved.invalid')[0], 403)
        code, _, headers = request(self.base, '/api/config', origin=ORIGIN)
        self.assertEqual(code, 200)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        code, _, headers = request(self.base, '/api/imu/windows', method='OPTIONS', token=None,
                                   origin=ORIGIN, extra_headers={'Access-Control-Request-Method': 'POST',
                                       'Access-Control-Request-Headers': 'authorization,content-type'})
        self.assertEqual(code, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)

    def test_17_start_retry_close_and_restart_require_new_session(self):
        key = str(uuid4())
        first = self.start(key)
        code, again, _ = request(self.base, '/api/sessions', {'request_id': key})
        self.assertEqual(code, 200)
        self.assertEqual(again['session_id'], first['session_id'])
        self.assertEqual(request(self.base, '/api/sessions', {'request_id': str(uuid4())})[0], 409)
        path = '/api/sessions/' + self.session + '/close'
        self.assertEqual(request(self.base, path, {})[0], 200)
        self.assertEqual(request(self.base, path, {})[0], 200)
        body = {'seq': 1, 'source': 'hybrid_demo', 'phase': 'running', 'values': self.defaults, 'quality': {}}
        self.assertEqual(request(self.base, '/api/sessions/' + self.session + '/samples', body)[0], 409)

    def test_18_static_whitelist_body_limit_and_health(self):
        code, body, _ = request(self.base, '/', token=None)
        self.assertEqual(code, 200)
        self.assertIn('GenGuard', body)
        for path in ('/model_bundle/baseline.sqlite', '/state/bridge.sqlite', '/adapter/imu_to_api.py'):
            self.assertEqual(request(self.base, path)[0], 404)
        self.assertEqual(request(self.base, '/api/imu/windows', raw=b' ' * 65537)[0], 413)
        code, health, _ = request(self.base, '/api/health', token=None)
        self.assertEqual(code, 200)
        self.assertTrue(health['ready'])
        self.assertEqual(health['data_domain'], 'hybrid_demo')

    def test_19_recovery_then_recurrence_emits_new_notification(self):
        self.calibrate()
        for _ in range(3):
            first, _ = self.step(features={'vibration_rms_g': .12})
        self.assertTrue(first['notification_events'])
        normal, _ = self.step()
        self.assertEqual(normal['status'], 'normal')
        self.assertFalse(normal['ai']['persistent_change'])
        for _ in range(3):
            again, _ = self.step(features={'vibration_rms_g': .12})
        self.assertTrue(again['notification_events'])
        self.assertNotEqual(first['notification_events'][0]['id'], again['notification_events'][0]['id'])

    def test_20_missing_mock_input_and_inactive_phase(self):
        self.calibrate()
        missing, _ = self.sample({'oil_kgf_cm2': None})
        self.assertEqual(missing['status'], 'unknown')
        stale, _ = self.sample(quality={'oil_kgf_cm2': {'quality': 'ok', 'age_s': 6}})
        self.assertEqual(stale['status'], 'unknown')
        warming, _ = self.sample(phase='warming_up')
        self.assertEqual(warming['status'], 'inactive')
        self.assertFalse(warming['notification_events'])

    def test_21_restart_discards_latest_and_calibration(self):
        self.calibrate()
        self.start()
        old_session = self.session
        self.context.__exit__(None, None, None)
        self.context = running_server(self.clock, self.state_dir)
        self.base = self.context.__enter__()
        imu = self.get('/api/imu')
        self.assertEqual(imu['state'], 'waiting')
        self.assertEqual(imu['calibration']['state'], 'untrained')
        body = {'seq': 1, 'source': 'hybrid_demo', 'phase': 'running', 'values': self.defaults, 'quality': {}}
        self.assertEqual(request(self.base, '/api/sessions/' + old_session + '/samples', body)[0], 409)
        self.start()
        self.assertNotEqual(self.session, old_session)

    def test_22_calibration_gap_and_metadata_mutation_cannot_reuse_baseline(self):
        packet, _ = self.imu()
        request(self.base, '/api/imu/calibration/start', {'confirmed_normal': True})
        self.imu_seq += 1  # One sensor window was lost.
        self.imu()
        self.assertEqual(self.get('/api/imu')['calibration']['state'], 'failed')
        self.clock.advance(2)
        packet.update(window_seq=self.imu_seq + 1, measured_at=utc_text(self.clock.wall()), accel_range_g=8)
        code, body, _ = request(self.base, '/api/imu/windows', packet)
        self.assertEqual(code, 409, body)
        self.assertEqual(body['error']['code'], 'imu_metadata_changed')

    def test_23_actual_sensor_range_overrides_mock_field_limit(self):
        # This synthetic fixture is valid for a +/-4g sensor, although the old
        # mock-only profile capped RMS at 2g. Its learned baseline must govern.
        self.calibrate(features={'vibration_rms_g': 2.5})
        result, _ = self.sample()
        self.assertEqual(result['imu']['accel_range_g'], 4)
        self.assertEqual(result['status'], 'normal')
        metric = result['fixed']['metrics']['vibration_rms_g']
        self.assertTrue(metric['valid'])
        self.assertIsNone(metric['issue'])
        self.assertEqual(metric['value'], 2.5)
        self.assertEqual(metric['basis']['kind'], 'installation_baseline')
        self.assertEqual(result['notification_events'], [])

    def test_24_old_duplicate_result_reports_age_without_relabeling_history(self):
        self.calibrate()
        first, payload = self.sample()
        self.assertEqual(first['status'], 'normal')
        self.assertEqual(first['analysis_age_s'], 0)
        self.clock.advance(30)
        code, duplicate, _ = request(self.base, '/api/sessions/' + self.session + '/samples', payload)
        self.assertEqual(code, 200, duplicate)
        self.assertTrue(duplicate['duplicate'])
        self.assertGreaterEqual(duplicate['analysis_age_s'], 30)
        self.assertEqual(duplicate['analyzed_at_epoch_s'], first['analyzed_at_epoch_s'])
        # Idempotency preserves the original result; explicit age tells the UI
        # it is historical. A genuinely new analysis observes current staleness.
        self.assertEqual(duplicate['status'], 'normal')
        self.assertEqual(duplicate['imu']['state'], 'fresh')
        current, _ = self.sample()
        self.assertEqual(current['status'], 'unknown')
        self.assertEqual(current['imu']['state'], 'stale')
        self.assertEqual(current['analysis_age_s'], 0)

    def test_25_generator_recovery_clears_streak_within_same_clock_tick(self):
        self.calibrate()
        for _ in range(3):
            changed, _ = self.step({'coolant_c': 89, 'oil_kgf_cm2': 2.6})
        self.assertTrue(changed['ai']['components']['generator']['persistent_change'])
        # No sensor upload or fake-clock advancement between the two requests.
        recovered, _ = self.sample()
        self.assertEqual(recovered['elapsed_s'], changed['elapsed_s'])
        generator = recovered['ai']['components']['generator']
        self.assertEqual(generator['state'], 'within_baseline')
        self.assertEqual(generator['change_streak'], 0)
        self.assertFalse(generator['persistent_change'])
        self.assertEqual(recovered['status'], 'normal')
        self.assertEqual(recovered['notification_events'], [])


if __name__ == '__main__':
    unittest.main()
