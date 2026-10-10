#!/usr/bin/env python3
"""Explicit transfer of an unchanged, reviewed IMU baseline between hosts.

Export is read-only. Import never fits a model and never silently reuses a stream.
SHA-256 detects accidental alteration; it is an integrity check, not a signature.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

from hybrid_model import (VIBRATION_FIELDS, VIBRATION_LOG_STD_FLOOR,
                          VIBRATION_MIN_RMS_G, VIBRATION_THRESHOLD)

SCHEMA = 'genguard.imu.baseline-transfer/1'
IDENTITY_FIELDS = ('source', 'installation_id', 'configured_sample_rate_hz', 'accel_range_g')
MAX_FILE_BYTES = 256 * 1024
BASELINE_KEYS = {'schema_version', 'algorithm', 'source', 'features', 'transform', 'mean',
    'covariance', 'precision', 'threshold', 'normal_values', 'sample_count', 'training_count',
    'validation_count', 'training_window_ids', 'validation_window_ids', 'training_rms_variation',
    'training_scores', 'validation', 'hyperparameters', 'parameter_provenance', 'scope', 'baseline_id'}


def checksum(value):
    # Same ASCII JSON representation used when fit_vibration originally assigns its ID.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _require(condition, message):
    if not condition:
        raise ValueError('Invalid IMU baseline transfer: ' + message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _json(text):
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))


def _matrix(value, shape, name):
    def numbers(item):
        return all(numbers(x) for x in item) if isinstance(item, list) else _number(item)
    _require(isinstance(value, list) and numbers(value), name + ' must contain finite numbers')
    array = np.asarray(value, dtype=float)
    _require(array.shape == shape, name + ' shape')
    return array


def sensor_identity(packet):
    return {key: packet[key] for key in IDENTITY_FIELDS}


def validate_transfer(data):
    """Validate structure, original model ID, sensor metadata and training evidence."""
    _require(isinstance(data, dict) and set(data) == {
        'schema_version', 'exported_at', 'source_calibration', 'sensor_identity',
        'baseline', 'calibration_windows', 'baseline_sha256', 'transfer_sha256'}, 'top-level fields')
    _require(data['schema_version'] == SCHEMA, 'unsupported schema')
    signed = {key: value for key, value in data.items() if key != 'transfer_sha256'}
    _require(data['transfer_sha256'] == checksum(signed), 'file checksum mismatch')
    try:
        exported = datetime.fromisoformat(data['exported_at'].replace('Z', '+00:00'))
        _require(exported.tzinfo is not None, 'export timestamp timezone')
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError('Invalid IMU baseline transfer: export timestamp') from error
    baseline = data['baseline']
    _require(isinstance(baseline, dict) and set(baseline) == BASELINE_KEYS, 'baseline fields')
    _require(data['baseline_sha256'] == checksum(baseline), 'baseline checksum mismatch')
    original = {key: value for key, value in baseline.items() if key != 'baseline_id'}
    _require(baseline['baseline_id'] == 'imu-' + checksum(original)[:20], 'original model ID mismatch')
    _require(baseline['schema_version'] == 'genguard.imu.baseline/1'
             and baseline['algorithm'] == 'log-feature-gaussian/1', 'unsupported algorithm')
    _require(baseline['features'] == list(VIBRATION_FIELDS)
             and baseline['transform'] == ['log(max(rms_g,1e-9))', 'log(envelope_cv+0.01)'], 'feature contract')
    _require(baseline['source'] in ('live_imu', 'recorded_imu'), 'source')
    for key, expected in (('sample_count', 15), ('training_count', 10), ('validation_count', 5)):
        _require(type(baseline[key]) is int and baseline[key] == expected, key)
    _require(_number(baseline['threshold']) and baseline['threshold'] == VIBRATION_THRESHOLD, 'threshold')
    _require(baseline['hyperparameters'] == {
        'covariance_shrinkage': .25, 'log_std_floor': VIBRATION_LOG_STD_FLOOR.tolist(),
        'minimum_calibration_rms_g': VIBRATION_MIN_RMS_G, 'max_training_rms_variation': .25}, 'hyperparameters')
    mean = _matrix(baseline['mean'], (2,), 'mean')
    covariance = _matrix(baseline['covariance'], (2, 2), 'covariance')
    precision = _matrix(baseline['precision'], (2, 2), 'precision')
    for name, matrix in (('covariance', covariance), ('precision', precision)):
        _require(np.allclose(matrix, matrix.T, rtol=1e-10, atol=1e-12)
                 and np.linalg.eigvalsh(matrix).min() > 0, name + ' must be positive definite')
    _require(np.allclose(covariance @ precision, np.eye(2), rtol=1e-8, atol=1e-8), 'covariance/precision mismatch')
    normal = baseline['normal_values']
    _require(isinstance(normal, dict) and set(normal) == set(VIBRATION_FIELDS)
             and all(_number(value) and value >= 0 for value in normal.values()), 'normal feature values')
    _require(np.allclose([normal['vibration_rms_g'], normal['envelope_cv']],
                        [np.exp(mean[0]), np.exp(mean[1]) - .01], rtol=1e-10, atol=1e-12), 'normal mean mismatch')
    expected_identity = data['sensor_identity']
    _require(isinstance(expected_identity, dict) and set(expected_identity) == set(IDENTITY_FIELDS), 'sensor identity fields')
    _require(expected_identity['source'] == baseline['source'], 'model/source mismatch')
    _require(isinstance(expected_identity['installation_id'], str)
             and 0 < len(expected_identity['installation_id']) <= 128, 'installation ID')
    _require(_number(expected_identity['configured_sample_rate_hz'])
             and 0 < expected_identity['configured_sample_rate_hz'] <= 10000, 'configured ODR')
    _require(_number(expected_identity['accel_range_g']) and expected_identity['accel_range_g'] in (2, 4, 8, 16), 'sensor range')
    evidence = data['source_calibration']
    _require(isinstance(evidence, dict) and set(evidence) == {'id', 'created_epoch', 'window_ids', 'source_stream_id'}, 'source calibration fields')
    _require(evidence['id'] == baseline['baseline_id'] and _number(evidence['created_epoch'])
             and evidence['created_epoch'] > 0, 'source calibration identity/time')
    window_ids = baseline['training_window_ids'] + baseline['validation_window_ids']
    _require(len(baseline['training_window_ids']) == 10 and len(baseline['validation_window_ids']) == 5
             and all(isinstance(x, str) and x for x in window_ids)
             and len(set(window_ids)) == 15 and evidence['window_ids'] == window_ids, 'training window IDs')
    windows = data['calibration_windows']
    _require(isinstance(windows, list) and len(windows) == 15, '15 calibration windows required')
    scores, rms, stamps = [], [], []
    last_seq = None
    for window_id, packet in zip(window_ids, windows):
        _require(isinstance(packet, dict) and all(key in packet for key in IDENTITY_FIELDS), 'window metadata')
        _require(sensor_identity(packet) == expected_identity, 'mixed sensor metadata')
        _require(packet.get('stream_id') == evidence['source_stream_id'] and isinstance(packet.get('stream_id'), str), 'source stream')
        seq = packet.get('window_seq')
        _require(type(seq) is int and seq >= 1 and (last_seq is None or seq == last_seq + 1), 'window sequence')
        last_seq = seq
        _require(window_id == packet['stream_id'] + ':' + str(seq), 'window identity')
        _require(packet.get('schema_version') == 'genguard.imu.window/1'
                 and packet.get('quality') == {'valid': True, 'flags': []}, 'window quality/schema')
        _require(_number(packet.get('duration_s')) and abs(packet['duration_s'] - 2) <= .01
                 and type(packet.get('sample_count')) is int and packet['sample_count'] >= 32
                 and _number(packet.get('sample_rate_hz')) and 0 < packet['sample_rate_hz'] <= 10000, 'window sampling')
        features = packet.get('features')
        _require(isinstance(features, dict) and set(features) == set(VIBRATION_FIELDS)
                 and all(_number(x) for x in features.values()), 'window features')
        r, cv = features['vibration_rms_g'], features['envelope_cv']
        _require(VIBRATION_MIN_RMS_G < r <= expected_identity['accel_range_g'] and 0 <= cv <= 100, 'feature range')
        vector = np.log([max(r, 1e-9), cv + .01])
        residual = vector - mean
        scores.append(float(np.sqrt(max(0, residual @ precision @ residual) / len(residual))))
        rms.append(r)
        try:
            stamp = datetime.fromisoformat(packet['measured_at'].replace('Z', '+00:00'))
            _require(stamp.tzinfo is not None, 'window timestamp timezone')
            stamps.append(stamp.timestamp())
        except (KeyError, AttributeError, ValueError, TypeError) as error:
            raise ValueError('Invalid IMU baseline transfer: window timestamp') from error
    _require(all(0 < b - a <= 5 for a, b in zip(stamps, stamps[1:])), 'window timestamp order/gap')
    variation = float(np.std(rms[:10]) / np.mean(rms[:10]))
    _require(_number(baseline['training_rms_variation']) and variation <= .25
             and math.isclose(variation, baseline['training_rms_variation'], rel_tol=1e-8, abs_tol=1e-12), 'training stability')
    train_scores = _matrix(baseline['training_scores'], (10,), 'training scores')
    validation = baseline['validation']
    _require(isinstance(validation, dict) and set(validation) == {'passed', 'scores', 'max_score', 'meaning'}
             and validation['passed'] is True, 'validation evidence')
    validation_scores = _matrix(validation['scores'], (5,), 'validation scores')
    _require(np.allclose(scores[:10], train_scores, rtol=1e-8, atol=1e-10)
             and np.allclose(scores[10:], validation_scores, rtol=1e-8, atol=1e-10)
             and max(scores[10:]) <= baseline['threshold'], 'saved validation scores')
    _require(_number(validation['max_score']) and math.isclose(validation['max_score'], max(scores[10:]), rel_tol=1e-8, abs_tol=1e-10), 'validation maximum')
    return copy.deepcopy(data)


def load_transfer(path):
    path = Path(path)
    _require(path.is_file() and path.stat().st_size <= MAX_FILE_BYTES, 'file missing or too large')
    return validate_transfer(_json(path.read_text(encoding='utf-8')))


def export_baseline(database, baseline_id, output):
    """Read a single coherent snapshot without writing or fitting anything."""
    uri = Path(database).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM hybrid_calibrations WHERE id=?', (baseline_id,)).fetchone()
        _require(row is not None, 'requested calibration does not exist')
        baseline, ids = _json(row['payload']), _json(row['window_ids'])
        windows = []
        for identifier in ids:
            record = db.execute('SELECT payload,fingerprint FROM hybrid_imu_windows WHERE id=?', (identifier,)).fetchone()
            _require(record is not None, 'source calibration window missing')
            packet = _json(record['payload'])
            original_hash = hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            _require(original_hash == record['fingerprint'], 'stored window fingerprint mismatch')
            windows.append(packet)
    _require(bool(windows), 'calibration windows missing')
    data = {'schema_version': SCHEMA, 'exported_at': datetime.now(timezone.utc).isoformat(),
        'source_calibration': {'id': row['id'], 'created_epoch': row['created'], 'window_ids': ids,
                               'source_stream_id': windows[0]['stream_id']},
        'sensor_identity': sensor_identity(windows[0]), 'baseline': baseline,
        'calibration_windows': windows, 'baseline_sha256': checksum(baseline)}
    data['transfer_sha256'] = checksum(data)
    validate_transfer(data)
    destination = Path(output)
    # Never overwrite an existing artifact or a mistakenly selected source database.
    with destination.open('x', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export', help='Export one exact baseline using read-only SQLite')
    export.add_argument('--db', type=Path, required=True)
    export.add_argument('--baseline-id', required=True)
    export.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = export_baseline(args.db, args.baseline_id, args.output)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps({'baseline_id': data['baseline']['baseline_id'],
                      'baseline_sha256': data['baseline_sha256'], 'sensor_identity': data['sensor_identity'],
                      'output': str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
