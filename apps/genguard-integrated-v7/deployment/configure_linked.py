"""Select the existing live-IMU demo contract on the local board server."""
import json
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

BASE = 'http://127.0.0.1:9883'


def request(path, body=None):
    data = None if body is None else json.dumps(body, allow_nan=False).encode()
    with urlopen(Request(BASE + path, data=data, headers={'Content-Type': 'application/json'}), timeout=4) as response:
        return json.load(response)


for attempt in range(30):
    try:
        if request('/api/health')['ready']:
            break
    except (URLError, TimeoutError):
        time.sleep(.5)
else:
    raise SystemExit('Board API did not start; inspect logs/server.log before starting sensor.')

overview = request('/api/operation/overview')
body = {'mode': 'hybrid', 'samples': overview['current_run']['samples'],
        'context': overview['current_run']['context'], 'auxiliary': overview['auxiliary'],
        'selected_elapsed_s': overview['view']['selected_elapsed_s'], 'reference_id': None}
for sample in body['samples']:
    sample['vibration_rms_g'] = None
request('/api/operation/evaluate', body)
request('/api/operation/linked-simulation', {'enabled': True})
print('Live IMU + explicitly simulated telemetry enabled; existing baseline will be restored on first valid packet.')
