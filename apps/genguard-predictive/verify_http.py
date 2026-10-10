#!/usr/bin/env python3
"""Exercise a separate temporary loopback server; do not touch the user's demo."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from http.server import ThreadingHTTPServer

from server import State, make_handler
from simulator import generate_run


def main():
    checks = []
    opener = build_opener(ProxyHandler({}))
    with TemporaryDirectory() as directory:
        with patch('server.threading.Thread.start'):
            state = State(recording_directory=directory)
        http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))
        worker = Thread(target=http.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(http.server_port)

        def request(path, payload=None, raw=None):
            body = raw if raw is not None else (json.dumps(payload, allow_nan=False).encode() if payload is not None else None)
            req = Request(base + path, data=body, headers={'Content-Type': 'application/json'})
            try:
                with opener.open(req, timeout=3) as response:
                    return response.status, response.read()
            except HTTPError as exc:
                return exc.code, exc.read()

        def check(name, condition):
            assert condition, name
            checks.append({'name': name, 'passed': True})

        try:
            code, raw = request('/api/health')
            check('health endpoint identifies exported model', code == 200 and json.loads(raw)['model_id'] == state.model_id)
            code, raw = request('/')
            check('offline static page served', code == 200 and b'GenGuard' in raw)
            code, raw = request('/api/evaluation')
            check('actual evaluation report served', code == 200 and json.loads(raw)['telemetry']['test_runs'] == 36)
            rows = generate_run('cooling_decline', 100001)
            code, raw = request('/api/telemetry', {'samples': rows[:91]})
            snap = json.loads(raw)
            check('180-second history yields four real forecasts', code == 200 and snap['analysis']['history_seconds'] == 180 and len(snap['analysis']['forecast']) == 4)
            for begin in range(91, 251, 80):
                code, raw = request('/api/telemetry', {'samples': rows[begin:min(begin + 80, 251)]})
                check('causal JSON streaming batch ' + str(begin), code == 200)
            snap = json.loads(raw)
            check('continuous deterioration produces learned warning', snap['analysis']['state'] == 'inspection' and any(a['source'].startswith('learned_') for a in snap['analysis']['alerts']))
            check('recording persists exact input count', snap['recording']['written'] == 252 and Path(snap['recording']['telemetry_path']).exists())
            before = state.snapshot()['latest']
            code, raw = request('/api/telemetry', raw=b'{"sample":{"timestamp_s":NaN}}')
            check('NaN returns 400 and does not mutate stream', code == 400 and state.snapshot()['latest'] == before)
            code, _ = request('/api/telemetry', {'sample': {'timestamp_s': 502}})
            check('missing run identity returns 400', code == 400)
            state.stream_received -= 10
            code, raw = request('/api/state')
            stale = json.loads(raw)['analysis']
            check('stale external stream suppresses predictions', code == 200 and stale['state'] == 'unknown' and not stale['forecast'])
            code, raw = request('/api/control', {'action': 'scenario', 'scenario': 'normal'})
            reset = json.loads(raw)
            check('reset clears previous stream history', code == 200 and reset['mode'] == 'replay' and reset['elapsed_s'] == 0 and reset['analysis']['state'] == 'collecting')
            check('no actual stop ETA claimed', reset['analysis']['stop_eta_s'] is None)
        finally:
            http.shutdown()
            worker.join(timeout=3)
            http.server_close()
            state.recorder.close()
    report = {'passed': all(c['passed'] for c in checks), 'checks': checks,
              'scope': 'Temporary local HTTP server; synthetic generator stream. No physical generator or UNO Q tested.'}
    path = Path(__file__).resolve().parent / 'reports/http-verification.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'checks': len(checks), 'report': str(path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
