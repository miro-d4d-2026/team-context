"""Read-only live verification. Select the physical phone pattern separately."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['normal', 'strong', 'intermittent'])
parser.add_argument('--output', type=Path, default=Path('verification/unoq-live-patterns.json'))
args = parser.parse_args()
observations = []
for index in range(7):
    with urlopen('http://127.0.0.1:9883/api/imu', timeout=5) as response:
        packet = json.load(response)
    observations.append(packet)
    if index < 6:
        time.sleep(2.5)
valid = [p for p in observations if p['state'] == 'fresh' and p['source'] == 'live_imu'
         and p.get('quality', {}).get('valid')
         and p.get('last_analysis', {}).get('execution', {}).get('host') == 'uno_q'
         and p.get('last_analysis', {}).get('window_id') == p['window_id']]
unique = {p['window_id']: p for p in valid}
expected = 'normal' if args.stage == 'normal' else 'inspect'
matches = [p for p in unique.values() if p['last_analysis']['pattern'] == args.stage
           and p['last_analysis']['state'] == expected]
summary = {'fresh_valid_distinct_windows': len(unique), 'matching_pattern_and_state': len(matches),
           'passed': len(matches) >= 3,
           'baseline_ids': sorted({p['calibration']['baseline_id'] for p in valid}),
           'rms_g': [min(p['features']['vibration_rms_g'] for p in valid),
                     max(p['features']['vibration_rms_g'] for p in valid)] if valid else [],
           'cv': [min(p['features']['envelope_cv'] for p in valid),
                  max(p['features']['envelope_cv'] for p in valid)] if valid else []}
data = json.loads(args.output.read_text()) if args.output.exists() else {
    'scope': 'Physical IMU features and inference both run on UNO Q; generator channels remain simulated.',
    'stages': {}}
data['stages'][args.stage] = {'observations': observations, 'summary': summary}
args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False))
