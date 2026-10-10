#!/usr/bin/env python3
"""Replay synthetic JSONL or a generated scenario using frozen learned weights."""
import argparse
import gzip
import json
from pathlib import Path
import sys
from runtime import Monitor
from simulator import generate_run, SCENARIOS


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, default='cooling_decline')
    parser.add_argument('--seed', type=int, default=100001)
    parser.add_argument('--input', help='Flat telemetry JSONL, gzip JSONL, or - for stdin')
    parser.add_argument('--output', default='-')
    parser.add_argument('--events-only', action='store_true')
    args = parser.parse_args()
    if args.input and args.input != '-' and args.output != '-' and Path(args.input).resolve() == Path(args.output).resolve():
        parser.error('입력과 출력 파일은 달라야 합니다.')
    source = None
    if args.input:
        source = sys.stdin if args.input == '-' else (gzip.open(args.input, 'rt') if args.input.endswith('.gz') else open(args.input))
        rows = (json.loads(line) for line in source if line.strip())
    else:
        rows = generate_run(args.scenario, args.seed)
    destination = sys.stdout if args.output == '-' else open(args.output, 'w')
    monitor = Monitor(); previous = None
    try:
        for row in rows:
            result = monitor.update(row)
            result['timestamp_s'] = row.get('timestamp_s')
            result['run_id'] = row.get('run_id')
            if not args.events_only or result['state'] != previous:
                destination.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
            previous = result['state']
    finally:
        if source is not None and source is not sys.stdin:
            source.close()
        if destination is not sys.stdout:
            destination.close()


if __name__ == '__main__':
    run()
