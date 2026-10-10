"""Metric-specific time-window analysis and compact dashboard API."""
from __future__ import annotations

import copy
import json
import re
from collections import deque
from uuid import uuid4

from bridge_service import BridgeError, canonical, digest, finite
from vibration_service import VibrationService
from operation_model import OperationAnalyzer
from operation_fixtures import seed_records, seed_current
from operation_catalog import catalog, telemetry_for, AUX_FIELDS, AUX_DEFAULTS
from vibration_driven_sim import VibrationDrivenSimulator
from linked_simulation_view import linked_overview
from learning_workspace import LearningWorkspace

CONTEXT_FIELDS = {'load_pct': (0, 120), 'ambient_c': (-30, 70), 'engine_rpm': (0, 4000)}
SAMPLE_FIELDS = {'coolant_c': (-30, 150), 'oil_kgf_cm2': (0, 10), 'vibration_rms_g': (0, 16)}


class OperationService(VibrationService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtime.db.executescript('''
            CREATE TABLE IF NOT EXISTS operation_records (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operation_metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        if not self.runtime.db.execute("SELECT 1 FROM operation_metadata WHERE key='initialized'").fetchone():
            with self.runtime.db:
                for record in seed_records():
                    self.runtime.db.execute('INSERT INTO operation_records VALUES (?,?)', (record['id'], canonical(record)))
                self.runtime.db.execute("INSERT INTO operation_metadata VALUES ('initialized','true')")
        self.operation_analyzer = OperationAnalyzer(self.operation_records())
        initial = seed_current()
        self.operation_input = {'samples': initial['samples'], 'context': initial['context'],
            'auxiliary': dict(AUX_DEFAULTS), 'selected_elapsed_s': 600, 'reference_id': None, 'mode': 'scenario',
            'recording_id': 'random', 'frame_index': 0}
        # Begin with the user's motivating example, still below the absolute heat warning.
        for sample in self.operation_input['samples']:
            if sample['elapsed_s'] >= 600:
                sample['coolant_c'] += 12
        self.operation_imu_state = {}
        self.imu_history_points = deque(maxlen=180)
        self.imu_history_identity = None
        self.linked_simulation_enabled = False
        self.linked_simulator = VibrationDrivenSimulator()
        self.latest_edge_analysis = None
        # Reviewed candidates live separately from the frozen production bundle.
        self.learning_workspace = LearningWorkspace(self.state_dir / 'review-learning')
        self.learning_capture_error = None

    def close(self):
        workspace = getattr(self, 'learning_workspace', None)
        if workspace is not None:
            workspace.close()
            self.learning_workspace = None
        super().close()

    def learning_state(self):
        result = self.learning_workspace.state()
        result['execution'] = self.execution
        result['preserved_baseline'] = {
            'baseline_id': self.calibration.get('baseline_id'),
            'state': self.calibration.get('state'),
            'frozen_model_id': self.bundle['model_id'],
            'unchanged_by_review_training': True,
        }
        result['capture_error'] = self.learning_capture_error or result.get('capture_error')
        return result

    def learning_session(self, sid):
        try:
            return self.learning_workspace.session_detail(sid)
        except ValueError as exc:
            raise BridgeError(422, 'invalid_learning_record', str(exc)) from exc

    def learning_raw(self, sid):
        try:
            return self.learning_workspace.raw_export(sid)
        except ValueError as exc:
            raise BridgeError(422, 'learning_raw_unavailable', str(exc)) from exc

    def learning_dispatch(self, action, body):
        if not isinstance(body, dict):
            raise BridgeError(422, 'invalid_learning_input', '학습 작업의 입력 형식을 확인하세요.')
        try:
            result = self.learning_workspace.dispatch(action, body)
            if action == 'start':
                self.learning_capture_error = None
            return result
        except ValueError as exc:
            raise BridgeError(422, 'learning_request_rejected', str(exc)) from exc

    def receive_imu(self, body):
        result = super().receive_imu(body)
        if not result.get('duplicate'):
            identity = self._sensor_identity(body)
            if identity != self.imu_history_identity:
                self.imu_history_points.clear()
                self.imu_history_identity = identity
                self.operation_imu_state = {}
            valid = body['quality']['valid']
            self.imu_history_points.append({
                'timestamp_s': self.clock(), 'window_id': result['id'],
                'source': body['source'], 'valid': valid,
                'rms_g': body['features']['vibration_rms_g'] if valid else None,
                'envelope_cv': body['features']['envelope_cv'] if valid else None})
            live_pattern = self.linked_simulator.update(self.imu_status(), self.baseline, self.monotonic())
            overview = None
            # Sensor arrival drives inference even when no browser is polling.
            if self.operation_input['mode'] == 'hybrid':
                try:
                    overview = self._operation_result(self.operation_input)
                    channel = next(c for c in overview['analysis']['channels'] if c['field'] == 'vibration_rms_g')
                    self.latest_edge_analysis = {
                        'window_id': result['id'], 'analyzed_at': self._now(),
                        'execution': self.execution, 'source': body['source'],
                        'baseline_id': self.calibration.get('baseline_id'),
                        'vibration': copy.deepcopy(channel),
                        'pattern': live_pattern.get('pattern'),
                        'state': overview['operational_status']['state'],
                        'summary': overview['operational_status']['summary']}
                except BridgeError as exc:
                    self.latest_edge_analysis = {'window_id': result['id'], 'analyzed_at': self._now(),
                        'execution': self.execution, 'state': 'unavailable', 'error': exc.code}
            else:
                self.latest_edge_analysis = {'window_id': result['id'], 'analyzed_at': self._now(),
                    'execution': self.execution, 'source': body['source'],
                    'pattern': live_pattern.get('pattern')}
            try:
                self.learning_workspace.ingest_imu(body, result, self.latest_edge_analysis)
            except Exception as exc:
                # A failed review archive must never interrupt the existing sensor
                # receiver; surface the failure instead of claiming successful storage.
                self.learning_capture_error = '학습 모델 분석 또는 기록 저장을 보류했습니다. 기존 실측 감시는 유지됩니다. 서버 기록을 확인하세요.'
                try:
                    with self.learning_workspace.service.lock:
                        self.learning_workspace.monitor.invalidate(self.learning_capture_error, result['id'])
                except Exception:
                    pass  # In-memory unknown state is set before any persistence attempt.
                import sys
                print(f'[review capture error] {type(exc).__name__}', file=sys.stderr)
                try:
                    self.learning_workspace.dispatch('stop', {})
                except Exception:
                    pass
            # Persist the reviewed judgment for the same accepted window. Browser
            # polling and report capture are neither inference triggers nor gates.
            reviewed = self.learning_workspace.summary()
            self.latest_edge_analysis['review_learning'] = reviewed
            if overview is not None:
                current = self._learning_overlay(overview)
                self.latest_edge_analysis.update(state=current['operational_status']['state'],
                    summary=current['operational_status']['summary'])
            else:
                self.latest_edge_analysis.update(state=reviewed['result']['state'],
                    summary=reviewed['result']['reason'])
            with self.runtime.db:
                self.runtime.db.execute("INSERT OR REPLACE INTO operation_metadata VALUES ('latest_edge_analysis',?)",
                                        (canonical(self.latest_edge_analysis),))
        return result

    def imu_status(self):
        result = super().imu_status()
        result['last_analysis'] = copy.deepcopy(getattr(self, 'latest_edge_analysis', None))
        return result

    def imu_history(self):
        status = self.imu_status()
        return {key: status.get(key) for key in
                ('state', 'source', 'stream_id', 'installation_id', 'age_s')} | {
            'points': list(self.imu_history_points), 'limit': 180,
            'baseline': {'ready': self.calibration['state'] == 'ready',
                         'rms_g': (self.baseline or {}).get('normal_values', {}).get('vibration_rms_g')}}

    def operation_records(self):
        records = [json.loads(row['payload']) for row in self.runtime.db.execute('SELECT payload FROM operation_records')]
        return sorted(records, key=lambda r: (r['inspected_at'], r['id']))

    @staticmethod
    def _numeric_map(values, limits, name):
        if not isinstance(values, dict) or set(values) - set(limits):
            raise BridgeError(422, 'invalid_fields', f'{name} 항목을 확인하세요.')
        for key, value in values.items():
            low, high = limits[key]
            if value is not None and (not finite(value) or not low <= value <= high):
                raise BridgeError(422, 'invalid_value', f'{key}의 값과 단위를 확인하세요.')

    def _operation_validate(self, body):
        allowed = {'samples', 'context', 'auxiliary', 'selected_elapsed_s', 'reference_id', 'mode', 'recording_id', 'frame_index'}
        if not isinstance(body, dict) or set(body) - allowed or body.get('mode', 'scenario') not in ('scenario', 'replay', 'hybrid'):
            raise BridgeError(422, 'invalid_operation', '운전 기록과 데이터 모드를 확인하세요.')
        self._numeric_map(body.get('context'), CONTEXT_FIELDS, '운전 조건')
        self._numeric_map(body.get('auxiliary', {}), AUX_FIELDS, '보조 계측')
        samples = body.get('samples')
        if not isinstance(samples, list) or not 1 <= len(samples) <= 30:
            raise BridgeError(422, 'invalid_samples', '시간순 측정값 1~30개가 필요합니다.')
        last = -1
        for sample in samples:
            if not isinstance(sample, dict) or set(sample) - (set(SAMPLE_FIELDS) | {'elapsed_s'}):
                raise BridgeError(422, 'invalid_sample', '경과 시간과 계측값을 확인하세요.')
            stamp = sample.get('elapsed_s')
            if not finite(stamp) or not 0 <= stamp <= 7200 or stamp <= last:
                raise BridgeError(422, 'invalid_time', '경과 시간은 중복 없이 증가해야 합니다.')
            last = stamp
            self._numeric_map({k: v for k, v in sample.items() if k != 'elapsed_s'}, SAMPLE_FIELDS, '계측')
            if body.get('mode') in ('replay', 'hybrid') and sample.get('vibration_rms_g') is not None:
                raise BridgeError(422, 'imu_override_forbidden', '실측 모드의 진동은 센서 또는 저장 기록에서 가져옵니다.')
        selected = body.get('selected_elapsed_s', samples[-1]['elapsed_s'])
        if not finite(selected) or not any(s['elapsed_s'] == selected for s in samples):
            raise BridgeError(422, 'invalid_selected_time', '분석할 측정 시점을 선택하세요.')
        reference = body.get('reference_id')
        if reference is not None and (not isinstance(reference, str) or not any(r['id'] == reference for r in self.operation_records())):
            raise BridgeError(422, 'unknown_reference', '비교할 점검 기록을 확인하세요.')
        if body.get('mode') == 'replay':
            if body.get('recording_id') not in ('random', 'training') or type(body.get('frame_index')) is not int or not 0 <= body['frame_index'] < 48:
                raise BridgeError(422, 'invalid_replay', '실측 기록과 구간을 선택하세요.')
        result = copy.deepcopy(body)
        result['mode'] = body.get('mode', 'scenario')
        result['selected_elapsed_s'] = selected
        result['auxiliary'] = dict(AUX_DEFAULTS, **body.get('auxiliary', {}))
        return result

    def _operation_result(self, body):
        mode = body['mode']
        current = seed_current()
        current.update(id='current-operation', inspected_at=self._now(), label='이번 시험 운전',
                       context=copy.deepcopy(body['context']), samples=copy.deepcopy(body['samples']),
                       confirmed_normal=False, source='scenario')
        observed = copy.deepcopy(current)
        observed['samples'] = [s for s in observed['samples'] if s['elapsed_s'] <= body['selected_elapsed_s']]
        analysis = self.operation_analyzer.analyze(observed, reference_id=body.get('reference_id'))
        latest = copy.deepcopy(observed['samples'][-1])
        vibration = replay = None
        if mode == 'replay':
            record = self.vibration_classifier.load_recording(body['recording_id'])
            frame = record['frames'][body['frame_index']]
            prediction = self.vibration_classifier.classify(frame['rms_g'], frame['envelope_cv'])
            replay = {**copy.deepcopy(frame), 'recording_id': record['id'], 'recording_label': record['title'],
                'recorded_at': record['recorded_at'], 'analyzed_at': self._now(), 'frame_index': body['frame_index'],
                'frame_count': len(record['frames']), 'prediction': prediction,
                'evaluation': {'expected_label': frame['expected_label'], 'correct': frame['expected_label'] == prediction['label']}}
            vibration = {'rms_g': frame['rms_g'], 'envelope_cv': frame['envelope_cv'], 'source': 'recorded_imu',
                         'state': prediction['state'], 'summary': prediction['summary'], 'checks': prediction['checks']}
        elif mode == 'hybrid':
            from hybrid_model import score_vibration
            imu = self.imu_status()
            valid = imu['state'] == 'fresh'
            features = imu.get('features') or {}
            scored = score_vibration(features, self.baseline) if valid and self.baseline else None
            if scored:
                identity = str(self.calibration.get('baseline_id')) + ':' + str(imu.get('window_id'))
                scored = self._persist(scored, self.operation_imu_state, identity, self.monotonic())
                self.operation_imu_state = copy.deepcopy(scored)
            else:
                self.operation_imu_state = {}
            scoreable = bool(scored and scored.get('state') != 'unknown')
            vibration = {'rms_g': features.get('vibration_rms_g') if valid else None,
                'source': imu.get('source') if valid else 'unconnected',
                'state': 'inspect' if scoreable and scored['persistent_change'] else 'normal' if scoreable else 'unavailable',
                'summary': '등록 기준과 다른 진동이 이어집니다.' if scored and scored['persistent_change'] else
                           f"진동 변화 관찰 중 · {scored.get('change_streak', 0)}/3개 구간에서 지속 여부 확인" if scoreable and scored.get('state') == 'change' else
                           '등록한 부착 기준과 비슷합니다.' if scoreable else '최신 센서값과 같은 부착 상태의 기준을 확인하세요.',
                'checks': scored.get('checks', []) if scored else []}
        if vibration is not None:
            channel = next(c for c in analysis['channels'] if c['field'] == 'vibration_rms_g')
            channel.update(current=vibration['rms_g'], previous=None, observed_rate=None, expected_rate=None,
                           rate_delta=None, observed_delta=None, expected_delta=None, intervals=[],
                           state=vibration['state'] if vibration['state'] != 'idle' else 'unavailable',
                           method='recorded_pattern_classifier' if replay else 'installation_baseline',
                           method_label='실측 진동 패턴' if replay else '실측 부착 기준',
                           reason=vibration['summary'], summary=vibration['summary'], checks=vibration['checks'],
                           source=vibration['source'], reference_source=vibration['source'])
            analysis['recommendations'] = [r for r in analysis.get('recommendations', []) if r.get('field') != 'vibration_rms_g']
            if vibration['state'] == 'inspect':
                analysis['recommendations'].append({'field': 'vibration_rms_g', 'title': '진동 변화 확인',
                    'reason': vibration['summary'], 'checks': vibration['checks']})
        rows = telemetry_for(body['context'], latest, body['auxiliary'], mode=mode, vibration=vibration)
        channels = {c['field']: c for c in analysis['channels']}
        for row in rows:
            if row['key'] in channels:
                channel = channels[row['key']]
                row.update(state=channel['state'], method=channel.get('method_label', channel.get('method')),
                           note=channel.get('summary') or channel.get('reason'), analysis=copy.deepcopy(channel))
        fixed_input = {'values': dict(latest, **body['context']), 'sources': {k: 'scenario' for k in (*SAMPLE_FIELDS, *CONTEXT_FIELDS)},
                       'quality': {k: 'ok' if v is not None else 'missing' for k, v in dict(latest, **body['context']).items()}}
        fixed = self._fixed(fixed_input)
        if body['context'].get('engine_rpm') == 0:
            fixed['alerts'] = [a for a in fixed['alerts'] if a['field'] not in ('engine_rpm', 'oil_kgf_cm2')]
        for row in rows:
            if row['key'] not in channels and row['key'] != 'engine_rpm' and row['state'] in ('inspect', 'watch', 'urgent'):
                fixed['alerts'].append({'id': 'aux_' + row['key'], 'field': row['key'], 'title': row['label'] + ' 확인',
                    'severity': 'urgent' if row['state'] == 'urgent' else 'watch', 'source': row['source'],
                    'reason': row.get('note'), 'basis': row.get('basis'), 'checks': row.get('checks', [])})
        for alert in fixed['alerts']:
            for row in rows:
                if row['key'] == alert['field']:
                    row['state'] = 'urgent' if alert['severity'] == 'urgent' else 'inspect'
        states = [c['state'] for c in analysis['channels']]
        missing_active = [r['label'] for r in rows if r['key'] in ('voltage_ll_v', 'battery_v', 'fuel_pct') and r['state'] == 'unavailable']
        state = 'urgent' if any(a['severity'] == 'urgent' for a in fixed['alerts']) else \
                'inspect' if fixed['alerts'] or 'inspect' in states else 'unavailable' if 'unavailable' in states or missing_active else 'normal'
        headline = {'urgent': '보호 참고값에 도달했습니다', 'inspect': '평소 운전 흐름과 다른 변화가 있습니다',
                    'unavailable': '일부 항목은 비교 조건이 필요합니다', 'normal': '같은 시간대의 정상 흐름과 비슷합니다'}[state]
        changed = [c['label'] for c in analysis['channels'] if c['state'] == 'inspect']
        summary = ' · '.join(changed) + ' 변화를 먼저 확인하세요.' if changed else \
                  ' · '.join(a['title'] for a in fixed['alerts']) if fixed['alerts'] else \
                  '시간대와 항목별 특성을 반영해 비교했습니다.' if state == 'normal' else '데이터 출처·운전 조건·측정 구간을 확인하세요.'
        analysis['state'] = 'inspect' if 'inspect' in states else 'unavailable' if 'unavailable' in states else 'normal'
        result = {'schema_version': 'genguard.operation-overview/1',
            'execution': self.execution,
            'equipment': {'id': current['equipment_id'], 'name': '수냉식 디젤 발전기', 'rated_kw': 10},
            'catalog': catalog(), 'telemetry': rows, 'current_run': current, 'observed_run': observed,
            'analysis': analysis, 'operational_status': {'state': state, 'headline': headline, 'summary': summary, 'alerts': fixed['alerts'], 'missing_active': missing_active},
            'records': self.operation_records(), 'reference_id': analysis.get('reference_id'),
            'view': {'mode': mode, 'selected_elapsed_s': body['selected_elapsed_s']},
            'auxiliary': copy.deepcopy(body['auxiliary']), 'vibration_replay': replay,
            'scope': '운전 시계열과 기준은 모의입니다. 실측 진동은 별도 모델로 분석하며 발전기 운전 시계열과 묶어 학습하지 않습니다.'}
        if mode == 'hybrid' and self.linked_simulation_enabled:
            simulation = self.linked_simulator.update(self.imu_status(), self.baseline, self.monotonic())
            result = linked_overview(result, simulation, self.imu_status())
            if simulation['pattern'] == 'idle':
                self.operation_imu_state = {}
        else:
            result['linked_simulation'] = {'enabled': False}
        return result

    def operation_linked_simulation(self, body):
        if not isinstance(body, dict) or set(body) != {'enabled'} or type(body['enabled']) is not bool:
            raise BridgeError(422, 'invalid_linked_simulation', '진동 연동 모의 운전의 켜짐 여부를 확인하세요.')
        if body['enabled'] and self.operation_input['mode'] != 'hybrid':
            raise BridgeError(409, 'live_imu_mode_required', '먼저 IMU 연결 모드를 선택하세요.')
        if body['enabled'] and not self.linked_simulation_enabled:
            self.linked_simulator = VibrationDrivenSimulator()
        self.linked_simulation_enabled = body['enabled']
        return self.operation_overview()

    def operation_overview(self):
        return self._learning_overlay(self._operation_result(self.operation_input))

    def _learning_overlay(self, result):
        reviewed = self.learning_workspace.summary()
        result['review_learning'] = reviewed
        # A reviewed classifier can add an independent inspection reason. It can
        # never erase fixed limits, live-baseline changes, or missing-data flags.
        if (result['view']['mode'] == 'hybrid' and reviewed.get('monitoring')
                and reviewed.get('fresh') and reviewed.get('source_kind') == 'hardware'
                and reviewed.get('result', {}).get('state') == 'attention'):
            reason = reviewed['result'].get('reason', '검토 학습 기준과 다른 진동이 이어집니다.')
            result['operational_status']['alerts'].append({
                'id': 'reviewed_vibration_change', 'field': 'vibration_rms_g',
                'title': '검토 학습 기준 · 진동 점검', 'severity': 'watch',
                'source': 'live_imu', 'reason': reason,
                'checks': ['센서 부착 상태와 동일한 운전 조건 확인', '검토 기록과 비교 후 점검 결과 기록'],
            })
            if result['operational_status']['state'] != 'urgent':
                result['operational_status'].update(state='inspect', headline='검토 학습 기준에서 진동 변화가 확인됩니다.', summary=reason)
        return result

    def operation_evaluate(self, body):
        candidate = self._operation_validate(body)
        result = self._operation_result(candidate)
        self.operation_input = candidate
        return self._learning_overlay(result)

    def operation_save(self, body):
        if not isinstance(body, dict) or set(body) - {'request_id', 'label', 'confirmed_normal'}:
            raise BridgeError(422, 'invalid_save', '기록 저장 항목을 확인하세요.')
        rid = body.get('request_id')
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', rid):
            raise BridgeError(422, 'request_id_required', '고유한 저장 요청 ID가 필요합니다.')
        fingerprint = digest(body)
        previous = self.runtime.db.execute('SELECT fingerprint,result FROM operation_requests WHERE id=?', (rid,)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise BridgeError(409, 'save_conflict', '이미 사용한 저장 요청입니다.')
            result = json.loads(previous['result']); result['duplicate'] = True
            return result
        if self.operation_input['mode'] != 'scenario':
            raise BridgeError(409, 'mixed_source_save_forbidden', '실측 진동과 모의 운전을 하나의 발전기 점검으로 저장하지 않습니다.')
        label = body.get('label', '시험 운전')
        confirmed = body.get('confirmed_normal', False)
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60 or type(confirmed) is not bool:
            raise BridgeError(422, 'invalid_save', '점검 이름과 정상 확인 여부를 확인하세요.')
        result = self.operation_overview()
        if confirmed and result['operational_status']['state'] != 'normal':
            raise BridgeError(422, 'normal_review_rejected', '변화 또는 비교 불가 항목을 먼저 확인하세요.')
        saved = copy.deepcopy(result['observed_run'])
        saved.update(id='operation-' + uuid4().hex, label=label.strip(), confirmed_normal=confirmed,
                     assessment=copy.deepcopy(result['operational_status']))
        result.update(saved_record=saved, duplicate=False, records=result['records'] + [saved])
        with self.runtime.db:
            self.runtime.db.execute('INSERT INTO operation_records VALUES (?,?)', (saved['id'], canonical(saved)))
            self.runtime.db.execute('INSERT INTO operation_requests VALUES (?,?,?)', (rid, fingerprint, canonical(result)))
        return result

    def operation_retrain(self, body):
        if body != {} or self.operation_input['mode'] != 'scenario':
            raise BridgeError(422, 'invalid_retrain', '모의 정상 운전 기록에서 학습을 갱신하세요.')
        candidate = OperationAnalyzer(self.operation_records())
        run = seed_current()
        run.update(inspected_at=self._now(), context=self.operation_input['context'],
                   samples=[s for s in self.operation_input['samples'] if s['elapsed_s'] <= self.operation_input['selected_elapsed_s']])
        candidate.analyze(run, reference_id=self.operation_input.get('reference_id'))
        payload = candidate.export_model()
        target = self.state_dir / 'operation-model.json'
        temp = self.state_dir / 'operation-model.tmp'
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        temp.replace(target)
        self.operation_analyzer = candidate
        return self.operation_overview()
