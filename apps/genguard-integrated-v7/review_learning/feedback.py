"""Human labels and a second supervised learning lane; normal baseline stays intact."""
import hashlib
import json
import threading
import time
import uuid
import numpy as np
from .label_model import fit_label_model, predict_label, POLICY
from .model import PROFILES, values, finite

LABELS = {'normal', 'abnormal', 'uncertain', 'exclude'}
ROLE_NAMES = ('fit', 'calibration', 'validation')


def _pack(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class FeedbackMixin:
    def init_feedback(self):
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(sessions)')}
        for name, spec in [('review_version', 'INTEGER NOT NULL DEFAULT 0'),
                           ('fault_kind', "TEXT NOT NULL DEFAULT ''"),
                           ('feedback_model_id', 'TEXT')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE sessions ADD COLUMN {name} {spec}')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS segment_labels(
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
          start_s REAL NOT NULL, end_s REAL NOT NULL, start_seq INTEGER NOT NULL, end_seq INTEGER NOT NULL,
          label TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL, fault_kind TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS label_session ON segment_labels(session_id, active);
        CREATE TABLE IF NOT EXISTS feedback_models(id TEXT PRIMARY KEY, data TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
        ''')
        self.db.commit()
        self.feedback_streak = 0

    def labels(self, sid):
        with self.lock:
            session = self.session(sid)
            rows = [dict(r) for r in self.db.execute('SELECT * FROM segment_labels WHERE session_id=? ORDER BY created', (sid,))]
            extent = self.db.execute('SELECT MIN(elapsed), MAX(elapsed), COUNT(*) FROM frames WHERE session_id=?', (sid,)).fetchone()
            return {'session_id': sid, 'review': session['review'], 'role': session['role'],
                    'review_version': session['review_version'], 'labels': rows,
                    'end_s': extent[1] or 0, 'count': extent[2],
                    'range_rule': '지정 시간 범위 안에 완전히 포함된 분석 창만 라벨로 사용. 경계 창은 제외.'}

    def add_label(self, sid, label, start_s, end_s, reviewer, note, fault_kind=''):
        if label not in LABELS:
            raise ValueError('정상/비정상/판단 보류/측정 오류 중 라벨을 선택하세요.')
        if not str(reviewer).strip() or not str(note).strip():
            raise ValueError('사람의 검토자와 확인 근거가 필요합니다.')
        if not finite(start_s) or not finite(end_s) or start_s < 0 or end_s <= start_s:
            raise ValueError('유효한 시작·종료 초를 입력하세요.')
        with self.lock:
            session = self.session(sid)
            if session['ended'] is None:
                raise ValueError('운전을 종료한 뒤 이상 구간을 검토하세요.')
            # Find whole windows only; a boundary cutting through a window is never
            # applied to that window. Original frames/results are not changed.
            rows = self.db.execute('SELECT seq,elapsed,data FROM frames WHERE session_id=? ORDER BY seq', (sid,)).fetchall()
            if not rows or end_s > rows[-1]['elapsed'] + 1e-6:
                raise ValueError('범위가 실제 기록 길이를 벗어났습니다.')
            chosen = []
            previous = 0.0
            for row in rows:
                payload=json.loads(row['data'])
                left = row['elapsed']-payload.get('duration_s',2.0)
                previous = row['elapsed']
                if left >= start_s-1e-6 and row['elapsed'] <= end_s+1e-6:
                    chosen.append(row['seq'])
            if not chosen:
                raise ValueError('선택 범위에 완전히 포함된 분석 창이 없습니다.')
            first, last = chosen[0], chosen[-1]
            existing = self.db.execute('SELECT 1 FROM segment_labels WHERE session_id=? AND active=1 AND start_seq<=? AND end_seq>=?',
                                       (sid, last, first)).fetchone()
            if existing:
                raise ValueError('이미 라벨이 있는 구간과 겹칩니다. 기존 라벨을 철회한 뒤 다시 저장하세요.')
            lid = 'label-'+uuid.uuid4().hex[:12]
            self.db.execute('INSERT INTO segment_labels VALUES(?,?,?,?,?,?,?,?,?,?,1,?)',
                            (lid, sid, start_s, end_s, first, last, label, str(reviewer).strip()[:80],
                             str(note).strip()[:1500], str(fault_kind).strip()[:120], time.time()))
            self.db.execute("UPDATE sessions SET review='segmented',review_version=review_version+1 WHERE id=?", (sid,))
            self.audit('segment_label', {'id': lid, 'session_id': sid, 'label': label,
                                        'start_seq': first, 'end_seq': last, 'reviewer': str(reviewer)[:80],
                                        'note': str(note)[:1500]})
            self.db.commit()
            return {'id': lid, 'start_seq': first, 'end_seq': last, 'windows': len(chosen)}

    def revoke_label(self, lid, reviewer, note):
        if not str(reviewer).strip() or not str(note).strip():
            raise ValueError('철회자와 이유가 필요합니다.')
        with self.lock:
            row = self.db.execute('SELECT * FROM segment_labels WHERE id=? AND active=1', (lid,)).fetchone()
            if not row:
                raise ValueError('활성 라벨을 찾을 수 없습니다.')
            self.db.execute('UPDATE segment_labels SET active=0 WHERE id=?', (lid,))
            self.db.execute('UPDATE sessions SET review_version=review_version+1 WHERE id=?', (row['session_id'],))
            # Do not restore an old whole-session normal label automatically.
            self.audit('revoke_label', {'id': lid, 'session_id': row['session_id'],
                                        'reviewer': str(reviewer)[:80], 'note': str(note)[:1500]})
            self.db.commit()

    def feedback_fingerprint(self, sid):
        s = self.session(sid)
        labels = [dict(r) for r in self.db.execute('SELECT * FROM segment_labels WHERE session_id=? AND active=1 ORDER BY id', (sid,))]
        data = {'review': s['review'], 'role': s['role'], 'reviewer': s['reviewer'], 'note': s['review_note'],
                'version': s['review_version'], 'fault_kind': s['fault_kind'], 'labels': labels,
                'count': s['count'], 'ended': s['ended']}
        return hashlib.sha256(_pack(data).encode()).hexdigest()

    def feedback_dependencies_valid(self, model):
        from .service import signature
        for sid, digest in model['dependencies'].items():
            s = self.session(sid)
            if not s['ended'] or signature(s['meta']) != model['signature'] or self.feedback_fingerprint(sid) != digest:
                return False
        return True

    def feedback_models(self):
        with self.lock:
            return [json.loads(r['data']) | {'active': bool(r['active']),
                        'labels_current': self.feedback_dependencies_valid(json.loads(r['data']))}
                    for r in self.db.execute('SELECT * FROM feedback_models ORDER BY rowid DESC')]

    def compatible_feedback(self, meta):
        from .service import signature
        for model in self.feedback_models():
            if model['active'] and model['labels_current'] and model['signature'] == signature(meta):
                return model
        return None

    def _labeled_rows(self, session):
        """Unlabeled areas of a segmented run stay unlabeled, not implicitly normal."""
        sid = session['id']
        labels = [dict(r) for r in self.db.execute('SELECT * FROM segment_labels WHERE session_id=? AND active=1 ORDER BY start_seq', (sid,))]
        # Deterministic reservoir sampling bounds the in-memory dataset. Read
        # frames from SQLite one at a time, not a whole multi-hour recording.
        pools = {'normal': [], 'abnormal': []}
        counts = {'normal': 0, 'abnormal': 0}
        seed = int(hashlib.sha256(sid.encode()).hexdigest()[:16], 16)
        rng = np.random.default_rng(seed)
        for row in self.db.execute('SELECT seq,data FROM frames WHERE session_id=? ORDER BY seq', (sid,)):
            label = None
            if session['review'] in ('normal', 'abnormal') and not labels:
                label = session['review']
            elif session['review'] == 'segmented':
                for segment in labels:
                    if segment['start_seq'] <= row['seq'] <= segment['end_seq']:
                        label = segment['label']
                        break
            if label in pools:
                counts[label] += 1
                item = {'frame': json.loads(row['data']), 'label': label, 'session_id': sid}
                if len(pools[label]) < 1000:
                    pools[label].append(item)
                else:
                    index = int(rng.integers(0, counts[label]))
                    if index < 1000:
                        pools[label][index] = item
        return sorted(pools['normal'] + pools['abnormal'], key=lambda i: i['frame']['seq'])

    def feedback_training(self, ids):
        from .service import signature
        with self.lock:
            if self.live_id or self.job['state'] == 'running':
                raise ValueError('운전과 다른 학습을 끝낸 뒤 시작하세요.')
            if not isinstance(ids, list) or not 4 <= len(ids) <= 30 or len(set(ids)) != len(ids):
                raise ValueError('분리된 기록 4~30개를 선택하세요. 각 역할에 정상·비정상 사례가 필요합니다.')
            records = [self.session(sid) for sid in ids]
            sig = signature(records[0]['meta'])
            if any(signature(r['meta']) != sig for r in records):
                raise ValueError('장비·설치·프로필·출처·조건·센서 설정이 같은 기록만 학습합니다.')
            if any(not r['ended'] or r['role'] not in ROLE_NAMES or r['review'] not in ('normal', 'abnormal', 'segmented') for r in records):
                raise ValueError('종료된 정상/비정상/구간 검토 기록에 학습·보정·검증 역할을 지정하세요.')
            splits = {role: [r['id'] for r in records if r['role'] == role] for role in ROLE_NAMES}
            dataset = {role: [] for role in ROLE_NAMES}
            coverage = {}
            keys = PROFILES[sig['profile']]['context'] + PROFILES[sig['profile']]['targets']
            dependencies = {r['id']: self.feedback_fingerprint(r['id']) for r in records}
            for role, sids in splits.items():
                total, good = 0, 0
                for sid in sids:
                    for item in self._labeled_rows(self.session(sid)):
                        total += 1
                        try:
                            values(item['frame'], keys)
                        except ValueError:
                            continue
                        dataset[role].append(item)
                        good += 1
                coverage[role] = good/max(1, total)
                if coverage[role] < POLICY['min_coverage']:
                    raise ValueError(f'{role}: 검토 구간의 유효 입력률 95% 이상이 필요합니다. 측정 오류는 비정상이 아닙니다.')
                for name in ('normal', 'abnormal'):
                    part = [r for r in dataset[role] if r['label'] == name]
                    run_count = len({r['session_id'] for r in part})
                    minimum = 2 if role == 'fit' else 1
                    counts={sid:sum(item['session_id']==sid for item in part) for sid in {item['session_id'] for item in part}}
                    if any(count<POLICY['min_windows_per_run_class'] for count in counts.values()):
                        raise ValueError(f'{role}: 각 독립 기록의 {name} 라벨에 유효 창 30개 이상이 필요합니다.')
                    if len(part) < POLICY['min_windows_per_class_split'] or run_count < minimum:
                        raise ValueError(f'{role}: {name} 유효 창 30개 및 독립 기록 {minimum}개 이상이 필요합니다. 라벨은 저장되어 있으니 기록을 더 모으세요.')
            self.cancel_train = threading.Event()
            cancel = self.cancel_train
            self.job = {'state': 'running', 'message': '정상·비정상 검토 라벨 학습 준비', 'model_id': None, 'kind': 'feedback'}
            def tick(message):
                if cancel.is_set():
                    raise InterruptedError('운전 감시가 시작되어 라벨 학습을 취소했습니다.')
                with self.lock:
                    self.job['message'] = message
            def run():
                try:
                    model = fit_label_model(dataset, sig['profile'], sig, splits, dependencies, coverage, tick)
                    with self.lock:
                        if cancel.is_set() or self.live_id:
                            raise InterruptedError('감시 우선. 후보 학습을 취소했습니다.')
                        if not self.feedback_dependencies_valid(model):
                            raise ValueError('학습 중 검토 라벨이 수정되었습니다. 새 라벨로 다시 학습하세요.')
                        self.db.execute('INSERT INTO feedback_models(id,data) VALUES(?,?)', (model['id'], _pack(model)))
                        self.audit('feedback_trained', {'model_id': model['id'], 'passed': model['passed'], 'splits': splits})
                        self.db.commit()
                        self.job = {'state': 'completed' if model['passed'] else 'rejected',
                                    'message': '라벨 분류 검증 통과 · 다음 운전 적용은 별도 승인' if model['passed'] else '라벨 분류 검증 미달 · 기존 모델 유지',
                                    'model_id': model['id'], 'kind': 'feedback'}
                except Exception as exc:
                    with self.lock:
                        self.job = {'state': 'cancelled' if isinstance(exc, InterruptedError) else 'failed',
                                    'message': str(exc), 'model_id': None, 'kind': 'feedback'}
            self.training_thread = threading.Thread(target=run, daemon=True)
            self.training_thread.start()
            return dict(self.job)

    def feedback_activate(self, mid):
        with self.lock:
            if self.live_id or self.job['state'] == 'running':
                raise ValueError('가동/학습 중에는 라벨 모델을 교체할 수 없습니다.')
            r = self.db.execute('SELECT data FROM feedback_models WHERE id=?', (mid,)).fetchone()
            if not r:
                raise ValueError('라벨 모델이 없습니다.')
            model = json.loads(r[0])
            if not model['passed'] or not self.feedback_dependencies_valid(model):
                raise ValueError('검증 통과 및 현재 라벨과 일치하는 후보만 활성화합니다.')
            for old in self.feedback_models():
                if old['signature'] == model['signature']:
                    self.db.execute('UPDATE feedback_models SET active=0 WHERE id=?', (old['id'],))
            self.db.execute('UPDATE feedback_models SET active=1 WHERE id=?', (mid,))
            self.audit('feedback_activated', {'id': mid})
            self.db.commit()

    def apply_feedback(self, session, frame, result, counters=None):
        mid = session.get('feedback_model_id')
        out = {'state': 'unavailable', 'reason': '검토 라벨 모델 미등록', 'score': None, 'model_id': mid}
        if mid:
            try:
                row = self.db.execute('SELECT data FROM feedback_models WHERE id=?', (mid,)).fetchone()
                model = json.loads(row[0])
                if not self.feedback_dependencies_valid(model):
                    raise ValueError('학습에 사용한 라벨이 변경/철회되어 분류를 보류합니다.')
                out = predict_label(model, frame)
            except (ValueError, TypeError) as exc:
                out = {'state': 'unknown', 'reason': str(exc), 'score': None, 'model_id': mid}
        previous = self.feedback_streak if counters is None else counters['feedback']
        streak = previous+1 if out['state'] == 'abnormal_like' else 0
        if counters is None: self.feedback_streak = streak
        else: counters['feedback'] = streak
        out['consecutive_windows'] = streak
        result['feedback'] = out
        # A normal classifier output never suppresses novelty or invalid data.
        # An abnormal label classifier adds a second, independent reason to inspect.
        if out['state'] == 'abnormal_like' and result['state'] != 'unknown':
            if streak >= 3:
                result.update(state='attention', reason='검토된 비정상 사례와 유사한 특징이 3개 창 연속 확인됩니다. 확인이 필요합니다.')
            elif result['state'] != 'attention':
                result.update(state='observe', reason='검토된 비정상 사례 쪽으로 분류됩니다. 지속 여부를 확인합니다.')
        return result
