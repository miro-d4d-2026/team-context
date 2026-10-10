#!/usr/bin/env python3
"""Re-evaluate historical features without changing the live service or training.

Run this exact script with the same exported baseline and evidence on both hosts.
This is historical feature parity/performance evidence, NOT a new live sensor test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from baseline_transfer import load_transfer
from hybrid_model import score_vibration


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percentile(values, percent):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * percent / 100))]


def run(baseline_file, evidence_file, repeats):
    transfer = load_transfer(baseline_file)
    baseline = transfer['baseline']
    evidence = json.loads(Path(evidence_file).read_text(encoding='utf-8'))
    if evidence.get('schema_version') != 'genguard.strong-intermittent-live/1':
        raise ValueError('Unsupported historical evidence schema')
    cases = []
    for index, packet in enumerate(transfer['calibration_windows']):
        cases.append({'group': 'normal_training' if index < 10 else 'normal_held_out',
                      'window_id': packet['stream_id'] + ':' + str(packet['window_seq']),
                      'features': packet['features'], 'expected_state': 'within_baseline'})
    for stage, expected_count in (('strong', 7), ('intermittent', 9)):
        snapshots = evidence['stages'][stage]['snapshots']
        if len(snapshots) != expected_count:
            raise ValueError(f'Expected exactly {expected_count} archived {stage} snapshots')
        for snapshot in snapshots:
            imu = snapshot['imu']
            if (imu['source'] != 'live_imu' or imu['state'] != 'fresh'
                    or imu['quality'] != {'valid': True, 'flags': []}
                    or imu['calibration']['baseline_id'] != baseline['baseline_id']):
                raise ValueError('Historical evidence does not belong to this baseline/source')
            cases.append({'group': stage, 'window_id': imu['window_id'],
                          'features': imu['features'], 'expected_state': 'change'})
    identifiers = [case['window_id'] for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Historical samples must use distinct sensor windows')
    results = []
    for case in cases:
        result = score_vibration(case['features'], baseline)
        results.append(dict(case, score=result['score'], state=result['state'], threshold=result['threshold'],
                            algorithm=result['algorithm'], baseline_id=result['baseline_id'],
                            matches_expected=result['state'] == case['expected_state']))
    # Warm caches before timing only the isolated score function. No IO, HTTP,
    # simulation, persistence, feature extraction or model fitting is timed here.
    for case in cases:
        score_vibration(case['features'], baseline)
    durations = []
    batch_started = time.perf_counter_ns()
    for _ in range(repeats):
        for case in cases:
            started = time.perf_counter_ns()
            score_vibration(case['features'], baseline)
            durations.append((time.perf_counter_ns() - started) / 1e6)
    batch_ms = (time.perf_counter_ns() - batch_started) / 1e6
    groups = {}
    for result in results:
        group = groups.setdefault(result['group'], {'count': 0, 'expected_matches': 0, 'states': {}})
        group['count'] += 1
        group['expected_matches'] += int(result['matches_expected'])
        group['states'][result['state']] = group['states'].get(result['state'], 0) + 1
    return {
        'schema_version': 'genguard.historical-model-parity-run/1',
        'evaluated_at': datetime.now(timezone.utc).isoformat(),
        'scope': '과거 IMU 특징을 동일한 학습 계수로 재평가한 수치 동등성 검사. 새로운 실측 라이브 실험이 아님.',
        'does_not_establish': ['새 실시간 센서 수신 검증', '실제 발전기 고장 진단 정확도', '종단간 지연', 'RUL 성능'],
        'side_effects': {'training': False, 'http_requests': False, 'database_writes': False,
                         'live_service_changes': False},
        'normal_scope': '정상15개는 기존 학습10개와 보류검증5개이며 새로운 독립 정상 실험이 아님.',
        'platform': {'system': platform.system(), 'release': platform.release(),
                     'machine': platform.machine(), 'python': platform.python_version(),
                     'numpy': np.__version__},
        'baseline_id': baseline['baseline_id'], 'baseline_coefficients_sha256': transfer['baseline_sha256'],
        'input_hashes': {'baseline_transfer_json': file_hash(baseline_file),
                         'historical_evidence_json': file_hash(evidence_file)},
        'frozen_model_hashes': {name: file_hash(ROOT / 'model_bundle' / name)
                                for name in ('model.json', 'profile.json', 'baseline.sqlite')},
        'implementation_hashes': {name: file_hash(ROOT / name)
                                  for name in ('hybrid_model.py', 'baseline_transfer.py',
                                               'deployment/verify_model_parity.py')},
        'case_count': len(results), 'group_summary': groups,
        'all_cases_match_expected': all(result['matches_expected'] for result in results),
        'results': results,
        'timing': {'scope': 'isolated score_vibration only; excludes acquisition, network, UI and preprocessing',
                   'repeats_per_case': repeats, 'total_calls': len(durations),
                   'total_batch_ms': batch_ms, 'mean_ms': statistics.mean(durations),
                   'median_ms': statistics.median(durations), 'p95_ms': percentile(durations, 95),
                   'maximum_ms': max(durations)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-transfer', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, default=ROOT / 'verification/strong-intermittent-live.json')
    parser.add_argument('--repeats', type=int, default=100)
    parser.add_argument('--output', type=Path, help='Optional NEW output file; stdout if omitted')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10000:
        parser.error('--repeats must be 1..10000')
    result = run(args.baseline_transfer, args.evidence, args.repeats)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as output:
            output.write(text)
    else:
        print(text, end='')
    return 0 if result['all_cases_match_expected'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
