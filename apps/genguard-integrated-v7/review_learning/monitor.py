"""Continuous live inference, separate from report capture and stored sessions."""
import copy
import json
import time
from .model import PROFILES


class LiveMonitor:
    def __init__(self, service):
        self.service = service
        self.counters = {'normal': 0, 'feedback': 0}
        self.last_window = self.last_rx = self.identity = None
        self.last_seq = None
        self.payload = None
        service.db.execute('CREATE TABLE IF NOT EXISTS live_monitor (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        row = service.db.execute('SELECT payload FROM live_monitor WHERE id=1').fetchone()
        if row:
            self.payload = json.loads(row[0])
        service.db.commit()

    def resolve(self, actual):
        """A condition is inferred only if exactly one active cohort matches.

        Packet identities cannot identify an operator's load/condition. Two
        otherwise compatible conditions must never silently choose the newest.
        """
        from .service import pack
        groups = {}
        for table, kind, check in [('models', 'normal', self.service.dependencies_valid),
                                  ('feedback_models', 'feedback', self.service.feedback_dependencies_valid)]:
            for row in self.service.db.execute(f'SELECT data FROM {table} WHERE active=1'):
                model = json.loads(row[0]); sig = model['signature']
                if (not model.get('passed') or not check(model)
                        or sig.get('profile') != 'vibration' or sig.get('source_kind') != 'hardware'
                        or any(sig.get(k) != v for k, v in actual.items())):
                    continue
                key = pack({k: sig[k] for k in sorted(sig)})
                group = groups.setdefault(key, {'signature': sig, 'normal': [], 'feedback': []})
                group[kind].append(model['id'])
        if not groups:
            return None, '현재 센서·설치와 일치하는 활성 학습 모델이 없습니다.'
        if len(groups) != 1 or any(len(g[k]) > 1 for g in groups.values() for k in ('normal', 'feedback')):
            return None, '일치하는 운전 조건의 모델이 여러 개입니다. 학습 센터에서 사용할 기준을 확인하세요.'
        group = next(iter(groups.values()))
        return {'meta': group['signature'], 'model_id': next(iter(group['normal']), None),
                'feedback_model_id': next(iter(group['feedback']), None)}, None

    def ingest(self, packet, actual, baseline_analysis=None):
        from .service import canonical_frame, pack
        now = time.monotonic()
        window = (packet.get('stream_id'), packet.get('window_seq'), packet.get('window_id'))
        if window == self.last_window:
            return copy.deepcopy(self.payload['result']) if self.payload else None
        cohort, reason = self.resolve(actual)
        model_key = (cohort['model_id'], cohort['feedback_model_id'], pack(cohort['meta'])) if cohort else None
        identity = (packet.get('stream_id'), pack(actual), model_key)
        seq = packet.get('window_seq')
        reset = identity != self.identity or self.last_rx is None or now-self.last_rx > 5
        gap = (not reset and type(seq) is int and type(self.last_seq) is int and seq != self.last_seq+1)
        if reset or gap:
            self.counters = {'normal': 0, 'feedback': 0}
        quality = packet.get('quality', 'invalid')
        if isinstance(quality, dict):
            quality = 'ok' if quality.get('valid') is True else 'invalid'
        if gap or packet.get('source') != 'live_imu': quality = 'invalid'
        pattern = (baseline_analysis or {}).get('pattern')
        phase = 'stopped' if pattern == 'idle' else 'running'
        duration = packet.get('duration_s', 2.)
        result = {'state': 'unknown', 'reason': reason, 'score': None, 'ratio': None,
                  'threshold': None, 'evidence': [], 'model_id': None,
                  'feedback': {'state': 'unavailable', 'model_id': None, 'score': None}}
        frame = {'seq': seq if type(seq) is int and seq >= 0 else 0, 'elapsed_s': duration,
                 'duration_s': duration, 'phase': phase, 'quality': quality,
                 'values': {k: packet.get('features', {}).get(k) for k in PROFILES['vibration']['targets']},
                 'sources': {k: packet.get('source') for k in PROFILES['vibration']['targets']}}
        try:
            frame = canonical_frame(frame)
            if cohort:
                result = self.service.analyze_frame(cohort, frame, self.counters)
            else:
                self.counters = {'normal': 0, 'feedback': 0}
            if quality != 'ok':
                result.update(state='unknown', reason='계측 품질 또는 시간 간격 문제입니다. 새 연속 값을 기다립니다.', score=None, ratio=None)
                self.counters = {'normal': 0, 'feedback': 0}
            elif phase == 'stopped':
                self.counters = {'normal': 0, 'feedback': 0}
                result.update(state='idle', reason='진동 정지 상태입니다. 운전 중 정상 기준과의 비교를 대기합니다.', score=None, ratio=None)
        except ValueError as exc:
            self.counters = {'normal': 0, 'feedback': 0}
            result.update(state='unknown', reason=str(exc), score=None, ratio=None)
        result['existing_monitor'] = copy.deepcopy(baseline_analysis)
        result['window_id'] = packet.get('window_id') or f"{packet.get('stream_id')}:{seq}"
        result['feedback_model_id'] = cohort['feedback_model_id'] if cohort else None
        self.last_window, self.last_rx, self.last_seq, self.identity = window, now, seq, identity
        self.payload = {'monitoring': True, 'source_kind': 'hardware', 'source': packet.get('source'),
                        'signature': copy.deepcopy(cohort['meta']) if cohort else None,
                        'condition': cohort['meta'].get('condition') if cohort else None,
                        'model_id': cohort['model_id'] if cohort else None,
                        'feedback_model_id': cohort['feedback_model_id'] if cohort else None,
                        'window_id': result['window_id'], 'stream_id': packet.get('stream_id'),
                        'analyzed_at': time.time(), 'result': result}
        self.service.db.execute('INSERT OR REPLACE INTO live_monitor VALUES(1,?)', (pack(self.payload),))
        self.service.db.commit()
        return copy.deepcopy(result)

    def invalidate(self, reason, window_id=None):
        """An inference/storage failure cannot recycle the previous judgment."""
        from .service import pack
        self.counters = {'normal': 0, 'feedback': 0}
        self.last_rx = None
        self.payload = {'monitoring': True, 'source_kind': 'hardware', 'source': 'live_imu',
                        'model_id': None, 'feedback_model_id': None, 'window_id': window_id,
                        'error': reason, 'result': {'state': 'unknown', 'reason': reason,
                        'score': None, 'ratio': None, 'model_id': None}}
        self.service.db.execute('INSERT OR REPLACE INTO live_monitor VALUES(1,?)', (pack(self.payload),))
        self.service.db.commit()

    def summary(self):
        out = copy.deepcopy(self.payload) if self.payload else {
            'monitoring': True, 'source_kind': 'hardware', 'source': 'live_imu',
            'signature': None, 'condition': None, 'model_id': None, 'feedback_model_id': None,
            'result': {'state': 'unknown', 'score': None, 'ratio': None, 'reason': '실측 IMU 입력을 기다립니다.'}}
        age = time.monotonic()-self.last_rx if self.last_rx is not None else None
        out.update(fresh=age is not None and age <= 5 and out.get('source')=='live_imu' and not out.get('error'), age_s=age)
        if not out['fresh']:
            self.counters = {'normal': 0, 'feedback': 0}
            out['result'].update(state='unknown', score=None, ratio=None, reason=out.get('error') or '새 센서 수신을 기다립니다. 마지막 판단을 현재 상태로 사용하지 않습니다.')
            feedback = out['result'].get('feedback')
            if feedback:
                feedback.update(state='unknown', score=None, consecutive_windows=0)
        return out
