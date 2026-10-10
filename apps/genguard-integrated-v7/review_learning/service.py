"""Local workflow service. Persistent sessions, explicit reviews, model versioning.
No hardware safety control; synthetic/hardware/public sources are separate domains.
"""
import csv
import hashlib
import io
import json
import math
import os
import socket
import sqlite3
import threading
import time
import uuid
from pathlib import Path
import numpy as np
from .feedback import FeedbackMixin
from .auto_training import AutomaticTrainingMixin
from .model import PROFILES, METRICS, fit_model, score, finite

FEATURE_VERSION = 'axis-demean-vector-rms__100ms-bin-demean-cv-v1'
KINDS = {'synthetic', 'hardware', 'public_dataset'}
ROLES = {'none', 'fit', 'calibration', 'validation'}
MAX_FRAMES = 2000


def pack(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def signature(meta):
    # Exact comparison contract. Change installation/config -> new baseline.
    keys = ('asset_id', 'installation_id', 'profile', 'source_kind', 'dataset_id', 'condition', 'sensor_config')
    return {k: meta.get(k) for k in keys} | {'feature_version': FEATURE_VERSION}


def check_meta(meta):
    result = {}
    for k in ('asset_id', 'installation_id', 'label', 'condition'):
        value = str(meta.get(k, '')).strip()
        if not value or len(value) > 180:
            raise ValueError(f'{k}: 1~180글자가 필요합니다.')
        result[k] = value
    profile = meta.get('profile', 'vibration')
    kind = meta.get('source_kind')
    if profile not in PROFILES or kind not in KINDS:
        raise ValueError('프로필 또는 데이터 출처가 잘못되었습니다.')
    result.update(profile=profile, source_kind=kind,
                  dataset_id=str(meta.get('dataset_id', ''))[:120],
                  sensor_config=str(meta.get('sensor_config', '208Hz;4g;fixed'))[:120])
    if kind == 'public_dataset' and not result['dataset_id']:
        raise ValueError('공개 자료는 dataset_id를 지정해야 합니다.')
    return result


def canonical_frame(frame):
    """Reference scalar API or the existing edge.window/1 input. Never invent units."""
    values, sources, qualities = {}, {}, []
    if 'readings' in frame:
        units = {'vibration_rms_g': 'g', 'vibration_peak_g': 'g', 'envelope_cv': '1',
                 'engine_rpm': 'rpm', 'coolant_c': 'degC', 'oil_bar': 'bar', 'voltage_v': 'V',
                 'current_a': 'A', 'load_pct': '%', 'ambient_c': 'degC', 'humidity_pct': '%RH', 'frequency_hz': 'Hz'}
        for key, r in frame['readings'].items():
            if key not in METRICS:
                continue
            value = r.get('value')
            good = r.get('quality') == 'ok' and r.get('unit') == units[key] and finite(r.get('age_s', 0)) and 0 <= r.get('age_s', 0) <= 5
            values[key] = value if good else None
            sources[key] = r.get('source', 'unknown')
            # Missing optional channels do not invalidate good channels.
    else:
        values = {k: v for k,v in frame.get('values', {}).items() if k in METRICS}
        sources = {k: frame.get('sources', {}).get(k, 'unknown') for k in values}
    for k, v in values.items():
        if v is not None and not finite(v):
            raise ValueError(f'{k}에 NaN/무한값/문자열을 사용할 수 없습니다.')
    seq = frame.get('seq')
    elapsed = frame.get('elapsed_s')
    if type(seq) is not int or seq < 0 or not finite(elapsed) or elapsed < 0:
        raise ValueError('seq 정수와 증가하는 elapsed_s가 필요합니다.')
    duration=frame.get('duration_s',2.0)
    if not finite(duration) or duration<=0 or duration>float(elapsed)+1e-6:
        raise ValueError('분석 창의 시작·종료 시간이 유효하지 않습니다.')
    return {'seq': seq, 'elapsed_s': float(elapsed), 'phase': frame.get('phase', 'running'),
            'quality': frame.get('quality', 'ok'), 'values': values, 'sources': sources,
            'duration_s': frame.get('duration_s', 2.0), 'received_at': time.time()}




class Service(AutomaticTrainingMixin, FeedbackMixin):
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory/'raw').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory/'genguard.sqlite', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, meta TEXT NOT NULL, started REAL NOT NULL,
          ended REAL, count INTEGER NOT NULL DEFAULT 0, review TEXT NOT NULL DEFAULT 'unreviewed',
          role TEXT NOT NULL DEFAULT 'none', review_note TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL DEFAULT '',
          model_id TEXT, stop_reason TEXT, max_ratio REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS frames(session_id TEXT,seq INTEGER,elapsed REAL,data TEXT,result TEXT,
          PRIMARY KEY(session_id,seq));
        CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,data TEXT NOT NULL,active INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,session_id TEXT,body TEXT,author TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT,data TEXT,created REAL);
        ''')
        self.init_feedback()
        self.db.execute("UPDATE sessions SET ended=?,stop_reason='server_interrupted' WHERE ended IS NULL", (time.time(),))
        self.db.commit()
        self.job = {'state': 'idle', 'message': '학습 작업 없음', 'model_id': None}
        self.cancel_train = threading.Event()
        self.capture_stop = threading.Event()
        self.capture_thread = None
        self.live_id = None
        self.latest_raw = []
        self.last_rx = None
        self.streak = 0
        self.capture_error = None
        self.training_thread = None

    def audit(self, action, data):
        self.db.execute('INSERT INTO audit(action,data,created) VALUES(?,?,?)', (action,pack(data),time.time()))

    def session(self, sid):
        row = self.db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise ValueError('기록을 찾을 수 없습니다.')
        return dict(row) | {'meta': json.loads(row['meta'])}

    def dependencies_valid(self, model):
        if model.get('dependencies') and any(self.feedback_fingerprint(sid) != digest for sid, digest in model['dependencies'].items()):
            return False
        ids = sum(list(model['splits'].values()), [])
        for sid in ids:
            r = self.session(sid)
            if r['review'] not in ('normal','segmented') or signature(r['meta']) != model['signature']:
                return False
        return True

    def compatible_model(self, meta):
        for r in self.db.execute('SELECT data FROM models WHERE active=1 ORDER BY rowid DESC'):
            m = json.loads(r[0])
            if m['signature'] == signature(meta) and self.dependencies_valid(m):
                return m
        return None

    def start(self, meta):
        with self.lock:
            if self.live_id:
                raise ValueError('진행 중인 운전을 먼저 종료하세요.')
            meta = check_meta(meta)
            self.cancel_train.set()  # A running monitor always preempts candidate learning.
            sid = 'run-'+uuid.uuid4().hex[:12]
            m = self.compatible_model(meta)
            self.db.execute('INSERT INTO sessions(id,meta,started,model_id) VALUES(?,?,?,?)',
                            (sid,pack(meta),time.time(),m['id'] if m else None))
            fm = self.compatible_feedback(meta)
            self.db.execute('UPDATE sessions SET feedback_model_id=? WHERE id=?', (fm['id'] if fm else None, sid))
            self.feedback_streak = 0
            self.audit('start', {'session_id':sid,'signature':signature(meta)})
            self.db.commit()
            self.live_id, self.streak, self.last_rx, self.latest_raw = sid, 0, None, []
            self.capture_error = None
            return sid

    def stop(self, reason='user_stopped'):
        self.capture_stop.set()
        with self.lock:
            sid = self.live_id
            if sid:
                self.db.execute('UPDATE sessions SET ended=?,stop_reason=? WHERE id=?', (time.time(),reason,sid))
                self.audit('stop', {'session_id':sid,'reason':reason})
                self.db.commit()
            self.live_id = None
            self.streak = 0
            return sid

    def ingest(self, sid, raw):
        f = canonical_frame(raw)
        with self.lock:
            sess = self.session(sid)
            if self.live_id != sid or sess['ended'] is not None:
                raise ValueError('이 운전은 수집 중이 아닙니다.')
            # Prevent source laundering: hardware models may not ingest synthetic controller values.
            allowed = {'synthetic': {'scenario', 'synthetic'},
                       'hardware': {'live_imu','recorded_imu','live_controller','recorded_controller','live_environment'},
                       'public_dataset': {'public_replay'}}[sess['meta']['source_kind']]
            bad = any(f['sources'].get(k) not in allowed for k,v in f['values'].items() if v is not None)
            if bad:
                raise ValueError('입력의 출처가 세션의 출처와 다릅니다. 혼합값을 실측으로 저장하지 않습니다.')
            old = self.db.execute('SELECT data,result FROM frames WHERE session_id=? AND seq=?', (sid,f['seq'])).fetchone()
            if old:
                a, b = json.loads(old[0]), dict(f)
                a.pop('received_at',None); b.pop('received_at',None)
                if a == b:
                    return json.loads(old[1])
                raise ValueError('같은 seq에 서로 다른 데이터가 전달되었습니다.')
            prev = self.db.execute('SELECT seq,elapsed FROM frames WHERE session_id=? ORDER BY seq DESC LIMIT 1',(sid,)).fetchone()
            if prev and (f['seq'] <= prev[0] or f['elapsed_s'] <= prev[1]):
                raise ValueError('시간 또는 순번이 역행했습니다.')
            if prev and (f['seq'] != prev[0]+1 or (f['elapsed_s']-prev[1] > 5 and raw.get('intentional_gap') is not True)):
                f['quality'] = 'invalid'
            result = {'state':'collecting','reason':'검증·활성화된 정상 기준이 없습니다. 기록은 계속 저장합니다.',
                      'score':None,'ratio':None,'threshold':None,'evidence':[], 'model_id':sess['model_id']}
            from .model import values
            try: values(f, PROFILES[sess['meta']['profile']]['context']+PROFILES[sess['meta']['profile']]['targets'])
            except ValueError: f['quality']='invalid'
            if f['quality'] != 'ok':
                result.update(state='unknown',reason='계측 품질 또는 시간 간격 문제입니다. 장비 고장으로 판정하지 않습니다.')
                self.streak = 0
            elif sess['model_id']:
                mr = self.db.execute('SELECT data FROM models WHERE id=?', (sess['model_id'],)).fetchone()
                model = json.loads(mr[0])
                try:
                    if not self.dependencies_valid(model):
                        raise ValueError('정상 기준의 승인 상태가 변경되었습니다. 새 기준을 검토하세요.')
                    result.update(score(model,f))
                    self.streak = self.streak+1 if result['ratio'] > 1 else 0
                    state = 'attention' if self.streak >= 3 else 'observe' if self.streak else 'reference'
                    result.update(state=state,reason={'attention':'평소와 다른 변화가 3개 창 연속으로 지속됩니다.',
                      'observe':'평소 패턴과 차이가 있습니다. 지속 여부를 확인합니다.',
                      'reference':'학습한 운전 조건의 참고 패턴과 유사합니다.'}[state], consecutive_windows=self.streak)
                except ValueError as e:
                    self.streak=0
                    result.update(state='unknown',reason=str(e))
            elif f['phase'] != 'running':
                result.update(state='unknown',reason='안정 운전 전용 기준입니다. 현재 운전 단계는 분석 대상이 아닙니다.')
            self.apply_feedback(sess, f, result)
            self.db.execute('INSERT INTO frames VALUES(?,?,?,?,?)', (sid,f['seq'],f['elapsed_s'],pack(f),pack(result)))
            self.db.execute('UPDATE sessions SET count=count+1,max_ratio=MAX(max_ratio,?) WHERE id=?', (result.get('ratio') or 0,sid))
            self.db.commit()
            self.last_rx = time.monotonic()
            return result

    def list_sessions(self):
        with self.lock:
            rows=self.db.execute('SELECT * FROM sessions ORDER BY started DESC LIMIT 300').fetchall()
            return [dict(r)|{'meta':json.loads(r['meta'])} for r in rows]

    def review(self,sid,status,role,reviewer,note,fault_kind=''):
        if status not in {'normal','abnormal','uncertain','exclude','unreviewed','segmented'} or (role is not None and role not in ROLES):
            raise ValueError('검토 상태/역할이 잘못되었습니다.')
        if not str(reviewer).strip() or not str(note).strip():
            raise ValueError('검토자와 점검 근거를 입력하세요.')
        with self.lock:
            r=self.session(sid)
            if role is None:
                role = r['role']  # A review changes labels, not old split metadata.
            if r['ended'] is None:
                raise ValueError('운전을 종료한 뒤 검토하세요.')
            has_labels = self.db.execute('SELECT 1 FROM segment_labels WHERE session_id=? AND active=1', (sid,)).fetchone()
            if has_labels and status != 'segmented':
                raise ValueError('활성 구간 라벨이 있습니다. 전체 상태를 변경하려면 구간 라벨을 먼저 철회하세요.')
            if status == 'segmented' and not has_labels:
                raise ValueError('구간 라벨을 먼저 등록하세요.')
            self.db.execute('UPDATE sessions SET review=?,role=?,reviewer=?,review_note=?,fault_kind=?,review_version=review_version+1 WHERE id=?',
                            (status,role,str(reviewer)[:80],str(note)[:1500],str(fault_kind)[:120],sid))
            self.audit('review', {'session_id':sid,'status':status,'role':role,'reviewer':reviewer,'note':note})
            self.db.commit()

    def frames(self,sid,limit=600):
        with self.lock:
            self.session(sid)
            rows=self.db.execute('SELECT data,result FROM frames WHERE session_id=? ORDER BY seq DESC LIMIT ?', (sid,limit)).fetchall()
            return [{'frame':json.loads(r[0]),'result':json.loads(r[1])} for r in reversed(rows)]

    def training(self,ids):
        with self.lock:
            if self.live_id or self.job['state'] == 'running':
                raise ValueError('운전·학습 중에는 새 학습을 시작할 수 없습니다.')
            if not isinstance(ids,list) or len(set(ids)) != len(ids) or not 4 <= len(ids) <= 30:
                raise ValueError('서로 다른 기록 4~30개를 선택하세요.')
            records=[self.session(i) for i in ids]
            sig=signature(records[0]['meta'])
            if any(signature(r['meta']) != sig for r in records):
                raise ValueError('장비·설치·입력 프로필·출처·조건·센서 설정이 일치해야 합니다.')
            if any(r['review'] not in ('normal','segmented') or not r['ended'] for r in records):
                raise ValueError('선택된 모든 기록은 종료 및 정상 검토 승인이 필요합니다.')
            splits={k:[r['id'] for r in records if r['role']==k] for k in ('fit','calibration','validation')}
            if len(splits['fit']) < 2 or not splits['calibration'] or not splits['validation'] or sum(map(len,splits.values())) != len(ids):
                raise ValueError('학습 2회 이상, 보정 1회 이상, 검증 1회 이상으로 역할을 지정하세요.')
            dependencies={sid:self.feedback_fingerprint(sid) for sid in ids}
            dataset={k:[] for k in splits}
            for role,sids in splits.items():
                for sid in sids:
                    raw=[{'data':pack(i['frame'])} for i in self._labeled_rows(self.session(sid)) if i['label']=='normal']
                    p=PROFILES[sig['profile']]
                    good=[]
                    from .model import values
                    for item in raw:
                        frame=json.loads(item['data'])
                        try:
                            values(frame,p['context']+p['targets'])
                            good.append(frame)
                        except ValueError:
                            pass
                    if len(good)<30 or len(good)/max(1,len(raw))<.95:
                        raise ValueError(f'{sid}: 유효 안정 운전 창 30개 이상·유효율 95% 이상이 필요합니다.')
                    dataset[role].extend(good)
            self.cancel_train=threading.Event()
            cancel=self.cancel_train
            self.job={'state':'running','message':'정상 기록으로 후보 모델 학습 중','model_id':None,'kind':'normal'}
            def tick(msg):
                if cancel.is_set():
                    raise InterruptedError('가동 감시를 우선합니다. 후보 학습을 취소했습니다.')
                with self.lock:
                    self.job['message']=msg
            def run():
                try:
                    model=fit_model(dataset['fit'],dataset['calibration'],dataset['validation'],sig['profile'],sig,splits,tick)
                    model['dependencies']=dependencies
                    with self.lock:
                        if cancel.is_set() or self.live_id:
                            raise InterruptedError('새 운전이 시작되어 학습 결과 적용을 보류했습니다.')
                        if not self.dependencies_valid(model):
                            raise ValueError('학습 중 정상 검토가 변경되었습니다.')
                        self.db.execute('INSERT INTO models(id,data) VALUES(?,?)',(model['id'],pack(model)))
                        self.audit('trained', {'model_id':model['id'],'passed':model['passed'],'splits':splits})
                        self.db.commit()
                        self.job={'state':'completed' if model['passed'] else 'rejected',
                                  'message':'정상 참고 검증 통과 · 활성화는 별도 승인' if model['passed'] else '정상 검증 실패 · 운영 모델은 유지', 'model_id':model['id'],'kind':'normal'}
                except Exception as e:
                    with self.lock:
                        self.job={'state':'cancelled' if isinstance(e,InterruptedError) else 'failed','message':str(e),'model_id':None,'kind':'normal'}
            self.training_thread=threading.Thread(target=run,daemon=True)
            self.training_thread.start()
            return dict(self.job)

    def models(self):
        with self.lock:
            return [json.loads(r['data'])|{'active':bool(r['active']),'labels_current':self.dependencies_valid(json.loads(r['data']))} for r in self.db.execute('SELECT * FROM models ORDER BY rowid DESC')]

    def activate(self,mid):
        with self.lock:
            if self.live_id or self.job['state']=='running':
                raise ValueError('운전과 학습이 끝난 뒤 다음 운전용 모델을 활성화하세요.')
            r=self.db.execute('SELECT data FROM models WHERE id=?',(mid,)).fetchone()
            if not r:
                raise ValueError('모델 없음')
            m=json.loads(r[0])
            if not m['passed'] or not self.dependencies_valid(m):
                raise ValueError('검증을 통과하고 정상 검토가 유지된 모델만 활성화할 수 있습니다.')
            for existing in self.models():
                if existing['signature']==m['signature']:
                    self.db.execute('UPDATE models SET active=0 WHERE id=?',(existing['id'],))
            self.db.execute('UPDATE models SET active=1 WHERE id=?',(mid,))
            self.audit('activate', {'model_id':mid})
            self.db.commit()

    def snapshot(self):
        with self.lock:
            sid=self.live_id
            if sid is None:
                row=self.db.execute('SELECT id FROM sessions ORDER BY started DESC LIMIT 1').fetchone()
                sid=row[0] if row else None
            sess=self.session(sid) if sid else None
            frames=self.frames(sid,120) if sid else []
            last=frames[-1] if frames else None
            age=(time.monotonic()-self.last_rx) if self.last_rx is not None and self.live_id else None
            result=dict(last['result']) if last else {'state':'collecting','reason':'첫 운전 기록부터 시작하세요.','score':None,'ratio':None,'evidence':[]}
            if self.live_id and (age is None or age>5):
                result.update(state='unknown',reason='수신 대기 또는 5초 이상 수신 중단. 마지막 값을 현재 정상값으로 사용하지 않습니다.')
            if self.capture_error:
                result.update(state='unknown',reason=self.capture_error)
            return {'session':sess,'live':bool(self.live_id),'age_s':age,'latest':last,
                    'result':result,'history':frames,'raw':self.latest_raw[-420:],
                    'job':dict(self.job),'models':self.models(),'sessions':self.list_sessions(),
                    'capture_error':self.capture_error,'report':self.report(sid) if sid else None,
                    'feedback_models':self.feedback_models()}

    def report(self,sid):
        if not sid:
            return None
        with self.lock:
            s=self.session(sid)
            rows=self.frames(sid,2000)
            usable=[r for r in rows if r['result'].get('ratio') is not None]
            worst=max(usable,key=lambda r:r['result']['ratio']) if usable else None
            evidence=worst['result']['evidence'][:3] if worst else []
            flagged=sum(r['result']['state']=='attention' for r in rows)
            feedback_flags=sum(r['result'].get('feedback',{}).get('consecutive_windows',0)>=3 for r in rows)
            reasons=[]
            actions=[]
            if not worst:
                title='정상 기준 등록이 필요합니다'
                body='측정 기록은 저장했습니다. 정상·비정상 검토 → AI 학습 → 성능 확인 → 새 모델 적용 순서로 진행하세요.'
            elif flagged:
                title='평소와 다른 변화 · 점검 검토'
                top=evidence[0]
                body=f"같은 장비·설치·조건의 참고 모델과 비교하여 {top['name']} 등의 변화가 지속되었습니다. 확정 고장 진단이 아니라 우선 확인할 근거입니다."
            else:
                title='학습 참고 범위와 비교한 기록'
                body='이 기록에서는 3개 창 연속 기준 이탈이 확인되지 않았습니다. 장비 전체의 무고장이나 안전을 보증하지 않습니다.'
            if feedback_flags:
                title='검토된 비정상 사례와 유사한 변화 · 점검 검토'
                body='사람이 검토한 라벨로 학습한 별도 분류기가 비정상 사례와 유사한 특징을 반복 확인했습니다. 정상 기준 이탈과 함께 확인하며 고장 부위를 확정하지 않습니다.'
            for e in evidence:
                reasons.append(f"{e['name']}: 기준 예상 {e['expected']:.4g} {e['unit']} → 관측 {e['actual']:.4g} {e['unit']}")
            keys=[e['key'] for e in evidence if e['standardized_deviation'] >= 3]
            if flagged:
                actions.append('센서 부착 위치·입력 품질·운전 부하가 기준과 같은지 확인')
                if any(k.startswith('vibration') or k=='envelope_cv' for k in keys):
                    actions.append('장비 고정 상태 확인 후 같은 조건에서 진동 재측정')
                if 'oil_bar' in keys:
                    actions.append('윤활계통·누유·압력 센서 상태를 정비 담당자가 확인')
                if 'coolant_c' in keys:
                    actions.append('냉각수·냉각팬·라디에이터 관련 점검 검토')
                actions.append('점검 결과를 기록하고 다음 운전과 비교')
            elif worst:
                actions=['정기 점검 절차를 유지하고 다음 운전 이력과 비교']
            else:
                actions=['독립 운전 기록의 상태를 확인한 뒤 AI 학습에서 후보를 만드세요']
            notes=[dict(r) for r in self.db.execute('SELECT * FROM notes WHERE session_id=? ORDER BY created DESC',(sid,))]
            return {'session_id':sid,'label':s['meta']['label'],'source_kind':s['meta']['source_kind'],
                    'title':title,'body':body,'evidence':evidence,'reasons':reasons,'actions':actions,
                    'flagged_windows':flagged,'analysed_windows':len(rows),'model_id':s['model_id'],
                    'worst_elapsed_s':worst['frame']['elapsed_s'] if worst else None,
                    'notes':notes,'review':s['review'],'review_note':s['review_note'],
                    'reviewer':s['reviewer'],'fault_kind':s['fault_kind'],
                    'labels':self.labels(sid),'feedback_flagged_windows':feedback_flags,
                    'feedback_model_id':s['feedback_model_id'],'rul':None,'fault_probability':None,
                    'explanation_method':'계산된 편차 + 검토용 문장 템플릿; 생성형 LLM 미사용',
                    'warning':'설계·기능 검증용. 제조사 보호장치와 전문 정비 판단을 대체하지 않습니다.'}

    def note(self,sid,body,author):
        if not str(body).strip() or not str(author).strip():
            raise ValueError('작성자와 조치 내용을 입력하세요.')
        with self.lock:
            self.session(sid)
            self.db.execute('INSERT INTO notes VALUES(?,?,?,?,?)', ('note-'+uuid.uuid4().hex[:12],sid,str(body)[:2000],str(author)[:80],time.time()))
            self.audit('maintenance_note',{'session_id':sid,'author':author})
            self.db.commit()

    def raw_csv(self,sid):
        path=self.directory/'raw'/f'{sid}.csv'
        with self.lock:
            self.session(sid)
        return path if path.exists() else None

    def close(self):
        self.cancel_train.set()
        self.stop('server_shutdown')
        if self.training_thread:
            self.training_thread.join(timeout=10)
        with self.lock:
            if not self.training_thread or not self.training_thread.is_alive(): self.db.close()
