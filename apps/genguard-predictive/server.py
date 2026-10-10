#!/usr/bin/env python3
"""Local GenGuard replay/JSON telemetry service; never writes to a controller."""
import argparse
from collections import deque
import copy
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

from contract import FIELDS, TARGETS, HORIZONS, finite
from runtime import Monitor, load_json
from simulator import generate_run, SCENARIOS
from vibration import VibrationMonitor, replay_windows
from recorder import Recorder

ROOT = Path(__file__).resolve().parent


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('계측 서버 리디렉션은 허용하지 않습니다.')


class State:
    def __init__(self, recording_directory=None):
        self.lock = threading.RLock()
        self.monitor = Monitor()
        self.profile = self.monitor.profile
        self.reports = {key: load_json(ROOT / f'reports/{name}.json') for key, name in
                        [('telemetry', 'evaluation'), ('training', 'training'), ('vibration', 'vibration')]}
        self.model_id = 'gg-sequence-' + hashlib.sha256((ROOT / 'models/telemetry.json').read_bytes()).hexdigest()[:12]
        self.recorder = Recorder(recording_directory or ROOT / 'recordings')
        self.playing = False
        self.speed = 30
        self.clock = 0.
        self.mode = 'replay'
        self.vibration_mode = 'recorded'
        self.vib_monitor = VibrationMonitor()
        self.vib_windows = replay_windows('random')
        self.vib_port = 8765
        self.profile_confirmed = False
        self.vib_rows = deque(maxlen=2000)
        self.vib_after = 0
        self.vib_received = None
        self.vib_error = None
        self.vib_meta = {}
        self.vib_result = None
        self.last_vib_window = None
        self.stream_received = None
        self.reset('cooling_decline')
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.vibration_worker, daemon=True).start()

    def reset(self, scenario=None):
        self.scenario = scenario or self.scenario
        self.rows = generate_run(self.scenario, seed=100001, duration_s=1200)
        self.duration_s = self.rows[-1]['timestamp_s']
        self.monitor.reset()
        self.mode = 'replay'
        self.playing = False
        self.clock = 0.
        self.position = 0
        self.history = deque(maxlen=360)
        self.events = deque(maxlen=150)
        self.analysis = None
        self.latest = None
        self.accept(self.rows[0])
        self.position = 1

    def accept(self, row):
        previous = self.analysis['state'] if self.analysis else None
        self.analysis = self.monitor.update(row)
        self.recorder.record(row, self.analysis, self.model_id)
        self.latest = {k: row.get(k) for k in ['timestamp_s', 'phase', 'data_source', 'run_id']}
        self.latest.update({k: row[k] if finite(row.get(k)) else None for k in FIELDS})
        self.history.append(dict(self.latest))
        if previous != self.analysis['state']:
            self.events.appendleft({'timestamp_s': row.get('timestamp_s'), 'state': self.analysis['state'],
                                    'message': self.analysis['observation']})

    def worker(self):
        last = time.monotonic()
        while True:
            current = time.monotonic()
            dt = min(current - last, 1.)
            last = current
            with self.lock:
                if self.mode == 'replay' and self.playing:
                    self.clock = min(self.duration_s, self.clock + dt * self.speed)
                    while self.position < len(self.rows) and self.rows[self.position]['timestamp_s'] <= self.clock:
                        self.accept(self.rows[self.position]); self.position += 1
                    if self.position == len(self.rows):
                        self.playing = False
            time.sleep(.08)

    def vibration_worker(self):
        opener = build_opener(ProxyHandler({}), NoRedirect())
        while True:
            with self.lock:
                mode, port, after = self.vibration_mode, self.vib_port, self.vib_after
            if mode == 'live':
                try:
                    req = Request(f'http://127.0.0.1:{port}/api/samples?after={after}', headers={'Accept': 'application/json'})
                    with opener.open(req, timeout=1.) as response:
                        raw = response.read(3 * 1024 * 1024 + 1)
                    if len(raw) > 3 * 1024 * 1024:
                        raise ValueError('계측 응답 크기 초과')
                    payload = json.loads(raw)
                    if not isinstance(payload.get('samples'), list):
                        raise ValueError('기존 /api/samples 응답이 아닙니다.')
                    status = payload.get('status') or {}
                    with self.lock:
                        if self.vibration_mode != 'live' or port != self.vib_port:
                            continue
                        self.vib_meta = status.get('meta') or {}
                        if status.get('fresh') is False or status.get('connected') is False:
                            raise ValueError(status.get('error') or '새 센서 입력이 없습니다.')
                        for sample in payload['samples']:
                            if not isinstance(sample, dict) or not isinstance(sample.get('id'), int):
                                raise ValueError('센서 표본 형식 오류')
                            if sample['id'] <= self.vib_after:
                                continue
                            if self.vib_rows and sample.get('elapsed_s', -1) <= self.vib_rows[-1].get('elapsed_s', -1):
                                self.vib_rows.clear()
                            self.vib_rows.append(sample)
                            self.vib_after = sample['id']
                            self.vib_received = time.monotonic()
                        # Restart can reset upstream ids; next fetch begins a clean history.
                        latest = status.get('latest') or {}
                        if not payload['samples'] and isinstance(latest.get('id'), int) and latest['id'] < self.vib_after:
                            self.vib_rows.clear(); self.vib_after = 0; self.vib_received = None
                        fresh = self.vib_received is not None and time.monotonic() - self.vib_received < 1.5
                        meta = dict(self.vib_meta, profile_confirmed=self.profile_confirmed, fresh=fresh)
                        self.vib_result = self.vib_monitor.process_samples(list(self.vib_rows), meta)
                        self.vib_error = None
                except Exception as exc:
                    with self.lock:
                        self.vib_error = str(exc)[:180]
                        self.vib_result = None
            time.sleep(.25)

    def vibration_snapshot(self):
        if self.vibration_mode == 'off':
            return {'source': 'disconnected', 'pattern_id': 'unknown', 'label': '분석 꺼짐', 'quality_notes': []}
        if self.vibration_mode == 'recorded':
            # Replay source has no causal connection to simulated generator deterioration.
            duration = max(w['timestamp_s'] for w in self.vib_windows) + 2.
            cursor = (self.latest['timestamp_s'] if self.latest else 0) % duration
            candidates = [w for w in self.vib_windows if w['timestamp_s'] <= cursor]
            if not candidates:
                return {'source': 'recorded_imu', 'pattern_id': 'unknown', 'label': '실측 기록 재생 대기',
                        'quality_notes': ['휴대폰 자극 별도 녹화 재생 · 발전기 고장 판정 아님']}
            window = candidates[-1]
            result = self.vib_monitor.classify_features(window['rms_vector_g'], window['envelope_cv'], True)
            result.update(source='recorded_imu', timestamp_s=window['timestamp_s'],
                          rms_vector_g=window['rms_vector_g'], envelope_cv=window['envelope_cv'],
                          quality_notes=['별도 녹화된 휴대폰 진동 재생 · 실제 발전기 진동과의 인과관계 없음'])
            return result
        fresh = self.vib_received is not None and time.monotonic() - self.vib_received < 1.5
        if not fresh or self.vib_error or self.vib_result is None:
            return {'source': 'live_imu', 'pattern_id': 'unknown', 'label': '센서 입력 대기',
                    'quality_notes': [self.vib_error or '최근 1.5초 이내의 새 입력이 없습니다.'], 'fresh': False}
        result = copy.deepcopy(self.vib_result)
        features = result.get('features') or {}
        result.update(source='live_imu', rms_vector_g=features.get('rms_vector_g'),
                      envelope_cv=features.get('envelope_cv'), fresh=True)
        return result

    def snapshot(self):
        with self.lock:
            analysis = copy.deepcopy(self.analysis)
            if self.mode == 'stream' and (self.stream_received is None or time.monotonic() - self.stream_received > 5):
                analysis.update(state='unknown', reason='외부 계측 입력이 5초 넘게 갱신되지 않았습니다.',
                                observation='새 외부 계측값 대기', forecast=[], crossings=[], alerts=[], guidance=[],
                                anomaly_score=None, evidence=[])
            training = self.reports['training']
            recording = self.recorder.status()
            recording.update(enabled=True, written=recording['records_written'])
            return {'mode': self.mode, 'scenario': self.scenario, 'playing': self.playing, 'speed': self.speed,
                    'elapsed_s': self.latest['timestamp_s'] if self.latest else 0, 'duration_s': self.duration_s,
                    'model_id': self.model_id, 'profile': self.profile, 'latest': copy.deepcopy(self.latest),
                    'analysis': analysis, 'history': list(self.history), 'events': list(self.events),
                    'vibration': self.vibration_snapshot(), 'vibration_mode': self.vibration_mode,
                    'recording': recording,
                    'report_summary': {'training': training, 'forecast_horizons_s': HORIZONS,
                                       'forecast_targets': TARGETS, 'real_generator_validation': False,
                                       'online_training': False},
                    'scenarios': [{'id': k, 'label': v} for k, v in SCENARIOS.items()]}

    def control(self, data):
        with self.lock:
            action = data.get('action')
            if action == 'scenario':
                scenario = data.get('scenario')
                if scenario not in SCENARIOS:
                    raise ValueError('지원하지 않는 시나리오')
                self.reset(scenario)
            elif action == 'reset':
                self.reset()
            elif action == 'start':
                if self.mode != 'replay':
                    raise ValueError('외부 입력 중입니다. 시나리오를 선택해 재생 모드로 돌아가세요.')
                if self.position == len(self.rows):
                    self.reset()
                self.playing = True
            elif action == 'pause':
                self.playing = False
            elif action == 'speed':
                if isinstance(data.get('speed'), bool) or data.get('speed') not in [1, 10, 30, 60]:
                    raise ValueError('속도는 1/10/30/60배 중 선택하세요.')
                self.speed = data['speed']
            else:
                raise ValueError('지원하지 않는 재생 명령')

    def set_vibration(self, data):
        mode = data.get('mode')
        port = data.get('port', 8765)
        if mode not in ('recorded', 'live', 'off'):
            raise ValueError('진동 입력 모드 오류')
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise ValueError('포트 번호 오류')
        with self.lock:
            self.vibration_mode = mode; self.vib_port = port
            self.profile_confirmed = data.get('profile_confirmed') is True
            self.vib_rows.clear(); self.vib_after = 0; self.vib_received = None
            self.vib_result = None; self.vib_error = None
            self.vib_monitor = VibrationMonitor()

    def ingest(self, data):
        rows = data.get('samples', [data['sample']] if 'sample' in data else [])
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError('sample 1개 또는 samples 1~100개가 필요합니다.')
        if any(not isinstance(r, dict) or not finite(r.get('timestamp_s')) for r in rows):
            raise ValueError('각 표본에 유한한 timestamp_s가 필요합니다.')
        if any(not isinstance(r.get('run_id'), str) or not r['run_id'] or
               not isinstance(r.get('phase'), str) for r in rows):
            raise ValueError('각 표본에 비어 있지 않은 run_id와 phase가 필요합니다.')
        if any(k in r and r[k] is not None and not finite(r[k]) for r in rows for k in FIELDS):
            raise ValueError('계측값은 유한한 숫자 또는 null이어야 합니다.')
        with self.lock:
            if self.mode != 'stream':
                self.monitor.reset(); self.history.clear(); self.events.clear()
                self.analysis = None
            self.mode = 'stream'; self.playing = False
            for row in rows:
                self.accept(row)
            self.stream_received = time.monotonic()


def make_handler(state):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)

        def log_message(self, fmt, *args):
            if not args or str(args[0]).startswith(('POST', 'GET /api/state')):
                return
            super().log_message(fmt, *args)

        def respond(self, payload, code=200):
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/api/state':
                self.respond(state.snapshot())
            elif path == '/api/evaluation':
                self.respond(state.reports)
            elif path == '/api/health':
                self.respond({'ok': True, 'model_id': state.model_id, 'scope': 'synthetic_generator_and_phone_vibration'})
            else:
                super().do_GET()

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError('요청 크기 오류')
                def bad_constant(value):
                    raise ValueError('NaN/Infinity는 JSON 계측값으로 사용할 수 없습니다.')
                data = json.loads(self.rfile.read(length), parse_constant=bad_constant)
                if not isinstance(data, dict):
                    raise ValueError('JSON 객체가 필요합니다.')
                path = urlparse(self.path).path
                if path == '/api/control':
                    state.control(data)
                elif path == '/api/vibration':
                    state.set_vibration(data)
                elif path == '/api/telemetry':
                    state.ingest(data)
                else:
                    self.respond({'error': '알 수 없는 경로'}, 404); return
                self.respond(state.snapshot())
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                self.respond({'error': str(exc)}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    state = State()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(state))
    print(f'GenGuard predictive demo: http://127.0.0.1:{args.port}/', flush=True)
    print('실제 학습 모델 · 모의 발전기 계측 + 휴대폰 진동 실측 기록 · 제어 명령 없음', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with state.lock:
            state.playing = False
            state.vibration_mode = 'off'
            state.recorder.close()
        server.server_close()


if __name__ == '__main__':
    main()
