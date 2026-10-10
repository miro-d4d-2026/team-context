"""Explicit review workspace layered over, never replacing, the live monitor.

Raw acquisition remains in the existing adapter. Only accepted two-second feature
packets are copied while a user explicitly records a run. Hardware and DEMO models
have separate signatures and cannot silently learn from one another.
"""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import threading
import time
import uuid
from pathlib import Path
from review_learning.service import Service, pack, canonical_frame, signature
from review_learning.model import PROFILES, METRICS
from review_learning.label_model import POLICY

FEATURE_VERSION='axis-demean-vector-rms__100ms-bin-demean-cv-v1'
ROOT=Path(__file__).resolve().parent


class LearningWorkspace:
    def __init__(self,directory,root=None):
        self.root=Path(root or ROOT)
        self.service=Service(directory)
        self.directory=Path(directory)
        self._sensor=None
        self._sensor_time=None
        self._stream=None
        self._seq=0
        self._elapsed_origin=None;self._window_origin=None
        self._last_window=None
        self._capture_stream=None
        self._demo_thread=None
        self._demo_scenario='normal'
        self._closed=threading.Event()
        self._capture_error=None
        self._last_existing_analysis=None
        # On restart, interrupted jobs are explicitly recorded, never resumed as success.
        with self.service.lock:
            row=self.service.db.execute("SELECT data FROM audit WHERE action='workspace_job' ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                old=json.loads(row[0]);self.service.job=old
                if old.get('state')=='running':
                    self.service.job={**old,'state':'cancelled','message':'서버 재시작으로 이전 학습을 취소했습니다.'}
        self._last_job=None

    def _persist_job(self):
        job=dict(self.service.job)
        if job!=self._last_job:
            self.service.audit('workspace_job',job)
            self.service.db.commit();self._last_job=job

    def state(self):
        s=self.service
        with s.lock:
            self._persist_job()
            snap=s.snapshot()
            snap['history']=snap['history'][-30:]
            snap.pop('raw',None)
            snap['profiles']=copy.deepcopy(PROFILES)
            snap['policy']={'normal':{'fit_sessions':2,'calibration_sessions':1,'validation_sessions':1,'min_windows_per_session':30,'min_coverage':.95},'feedback':copy.deepcopy(POLICY)}
            snap['input']={'available':bool(self._sensor and self._sensor_time and time.monotonic()-self._sensor_time<=5),
              'age_s':time.monotonic()-self._sensor_time if self._sensor_time else None,
              'stream_id':self._stream,'features':['vibration_rms_g','envelope_cv'],
              'source':'live_imu','existing_monitor_preserved':True}
            snap['capture_error']=self._capture_error
            snap['source_notice']='실측 진동과 모의 제어반은 별도 출처로 학습합니다. 아이폰 진동은 실제 발전기 결함 라벨이 아닙니다.'
            return snap

    def summary(self):
        s=self.service
        with s.lock:
            sid=s.live_id
            latest=s.frames(sid,1)[-1] if sid and self.service.session(sid)['count'] else None
            age=time.monotonic()-s.last_rx if s.last_rx else None
            result=copy.deepcopy(latest['result']) if latest else {'state':'collecting','score':None,'reason':'검토 학습용 운전 기록 대기'}
            if sid and (age is None or age>5):
                result.update(state='unknown',reason='센서 수신 대기 또는 중단',score=None,ratio=None)
            return {'live':bool(sid),'session_id':sid,'source_kind':s.session(sid)['meta']['source_kind'] if sid else None,
              'fresh':bool(sid and age is not None and age<=5),'age_s':age,'result':result}

    def session_detail(self,sid):
        s=self.service
        with s.lock:
            session=s.session(sid)
            audit=[]
            for row in s.db.execute('SELECT action,data,created FROM audit ORDER BY id DESC'):
                data=json.loads(row['data'])
                if data.get('session_id')==sid:audit.append({'action':row['action'],'data':data,'created':row['created']})
            raw=self.directory/'raw'/f'{sid}.jsonl'
            if not raw.exists(): raw=self.directory/'raw'/f'{sid}.source.json'
            return {'session':session,'frames':s.frames(sid,2000),'labels':s.labels(sid),'report':s.report(sid),
              'audit':audit,'raw_packets':{'available':raw.exists(),'file':raw.name if raw.exists() else None,
               'meaning':'기존 수신기에서 받은 원본 특징 패킷. 원시 가속도 원본은 기존 수집 파일에 보존.'},
              'export_scope':'최근 2000개 분석 창; 전체 패킷은 원본 JSONL 파일 보존'}

    def raw_export(self,sid):
        with self.service.lock:
            self.service.session(sid)
            path=self.directory/'raw'/f'{sid}.jsonl'
            content_type='application/x-ndjson; charset=utf-8'
            if not path.exists():
                path=self.directory/'raw'/f'{sid}.source.json'
                content_type='application/json; charset=utf-8'
            if not path.exists():raise ValueError('별도 원본 파일이 없습니다. 분석 창 내보내기를 사용하세요.')
            return {'bytes':path.read_bytes(),'content_type':content_type,'filename':path.name}

    def dispatch(self,action,body):
        if not isinstance(body,dict):raise ValueError('JSON 객체가 필요합니다.')
        s=self.service
        if action=='start':
            return {'session_id':self._start_hardware(body)}
        if action=='stop':
            return {'session_id':s.stop('user_stopped')}
        if action=='review':
            s.review(body.get('session_id'),body.get('status'),body.get('role'),body.get('reviewer',''),body.get('note',''),body.get('fault_kind',''))
            return {'ok':True}
        if action=='label':
            return s.add_label(body.get('session_id'),body.get('label'),body.get('start_s'),body.get('end_s'),body.get('reviewer',''),body.get('note',''),body.get('fault_kind',''))
        if action=='revoke-label':
            s.revoke_label(body.get('label_id'),body.get('reviewer',''),body.get('note',''));return {'ok':True}
        if action=='training-plan':
            return s.automatic_plan(body.get('session_ids'))
        if action=='train':
            kind=body.get('kind','normal')
            if kind not in ('auto','normal','feedback'):raise ValueError('학습 종류가 잘못되었습니다.')
            out=({'auto':s.automatic_training,'feedback':s.feedback_training,'normal':s.training}[kind])(body.get('session_ids'))
            with s.lock:self._persist_job()
            return out
        if action=='activate':
            kind=body.get('kind','normal')
            if kind not in ('normal','feedback'):raise ValueError('모델 종류가 잘못되었습니다.')
            (s.feedback_activate if kind=='feedback' else s.activate)(body.get('model_id'));return {'ok':True}
        if action=='note':
            s.note(body.get('session_id'),body.get('body',''),body.get('author',''));return {'ok':True}
        if action=='demo':return self._demo(body)
        if action=='import-recording':return self._import_recording(body.get('recording'))
        raise ValueError('지원하지 않는 작업입니다.')

    def _start_hardware(self,body):
        with self.service.lock:
            if not self._sensor or not self._sensor_time or time.monotonic()-self._sensor_time>5:
                raise ValueError('현재 실측 IMU 수신이 필요합니다. 센서·입력 연결을 확인하세요.')
            packet=self._sensor
            q=packet.get('quality')
            if (isinstance(q,dict) and q.get('valid') is not True) or (isinstance(q,str) and q!='ok'):
                raise ValueError('유효한 IMU 창을 수신한 뒤 수집을 시작하세요.')
            if packet.get('source')!='live_imu':raise ValueError('실시간 IMU 출처만 새 운전 수집에 사용합니다.')
            meta={'asset_id':packet.get('asset_id','GEN-10K-001'),
              'installation_id':packet['installation_id'],'profile':'vibration','source_kind':'hardware',
              'label':body.get('label') or '실측 진동 운전 기록',
              'condition':body.get('condition') or '아이폰·IMU 고정 실험',
              'sensor_config':f"{packet.get('configured_sample_rate_hz',packet.get('sample_rate_hz'))}Hz;{packet.get('accel_range_g')}g;{FEATURE_VERSION}"}
            sid=self.service.start(meta)
            self._capture_stream=self._stream;self._seq=0;self._elapsed_origin=None;self._window_origin=None;self._last_window=None;self._capture_error=None
            return sid

    def ingest_imu(self,body,result=None,baseline_analysis=None):
        """Called only after the existing ingest API accepted the hardware packet."""
        if not isinstance(body,dict):return None
        packet=copy.deepcopy(body)
        with self.service.lock:
            self._sensor=packet;self._sensor_time=time.monotonic();self._stream=packet.get('stream_id')
            self._last_existing_analysis=copy.deepcopy(baseline_analysis)
            sid=self.service.live_id
            if not sid or self.service.session(sid)['meta']['source_kind']!='hardware':return None
            window=packet.get('window_id',(packet.get('stream_id'),packet.get('window_seq')))
            if window==self._last_window:return None
            previous_window=self._last_window
            self._last_window=window
            if self._capture_stream!=self._stream:
                self._capture_error='수신 스트림이 변경되어 기록을 종료했습니다. 새 설치·기준을 확인하세요.'
                self.service.stop('stream_changed');return None
            meta=self.service.session(sid)['meta']
            actual_config=f"{packet.get('configured_sample_rate_hz',packet.get('sample_rate_hz'))}Hz;{packet.get('accel_range_g')}g;{FEATURE_VERSION}"
            if packet.get('installation_id')!=meta['installation_id'] or actual_config!=meta['sensor_config']:
                self._capture_error='센서 설치 또는 측정 설정이 변경되어 기록을 종료했습니다.'
                self.service.stop('sensor_changed');return None
            self._seq+=1
            duration=packet.get('duration_s',2.)
            window_seq=packet.get('window_seq')
            if isinstance(window_seq,int):
                if self._window_origin is None:self._window_origin=window_seq
                elapsed=(window_seq-self._window_origin+1)*duration
            else:
                elapsed=packet.get('elapsed_s',self._seq*duration)
                if self._elapsed_origin is None:self._elapsed_origin=elapsed-duration
                elapsed-=self._elapsed_origin
            quality=packet.get('quality','ok')
            if isinstance(quality,dict):quality=quality.get('quality','ok' if quality.get('valid',True) else 'invalid')
            if isinstance(previous_window,tuple) and isinstance(window,tuple) and isinstance(window[1],int) and isinstance(previous_window[1],int) and window[1]!=previous_window[1]+1:quality='invalid'
            frame={'seq':self._seq,'elapsed_s':elapsed,'duration_s':duration,'phase':'running','quality':quality,
              'values':{k:packet.get('features',{}).get(k) for k in PROFILES['vibration']['targets']},
              'sources':{k:packet.get('source','unknown') for k in PROFILES['vibration']['targets']}}
            out=self.service.ingest(sid,frame)
            # Keep the existing monitor's evidence immutable and separate.
            out['existing_monitor']=copy.deepcopy(baseline_analysis)
            self.service.db.execute('UPDATE frames SET result=? WHERE session_id=? AND seq=?',(pack(out),sid,self._seq))
            self.service.db.commit()
            with (self.directory/'raw'/f'{sid}.jsonl').open('a',encoding='utf-8') as fh:fh.write(pack(packet)+'\n')
            return out

    def _import_recording(self,key):
        if key not in ('training','random'):raise ValueError('기존 training 또는 random 기록만 가져올 수 있습니다.')
        path=self.root/'replay_artifacts'/f'{key}.json';payload=path.read_bytes();digest=hashlib.sha256(payload).hexdigest()
        original=json.loads(payload);s=self.service
        with s.lock:
            prior=s.db.execute("SELECT data FROM audit WHERE action='import_recording' ORDER BY id").fetchall()
            for entry in prior:
                old=json.loads(entry['data'])
                if old.get('original_run_id')==original['run_id']:
                    return {'session_id':old['session_id'],'already_imported':True}
            sid=s.start({'asset_id':'IPHONE-IMU-RECORDED','installation_id':'legacy-phone-mount-20261010',
              'label':original['title'],'condition':'2026-10-10 기존 아이폰 고정 실험',
              'profile':'vibration','source_kind':'hardware','dataset_id':'iphone-imu-fixed-cohort-20261010',
              'sensor_config':'legacy-raw-config-unverified;'+FEATURE_VERSION})
            try:
                for index,row in enumerate(original['frames']):
                    frame={'seq':index+1,'elapsed_s':row['end_s'],'duration_s':row['end_s']-row['start_s'],
                      'phase':'running','quality':'ok','intentional_gap':True,
                      'values':{'vibration_rms_g':row['rms_g'],'envelope_cv':row['envelope_cv']},
                      'sources':{'vibration_rms_g':'recorded_imu','envelope_cv':'recorded_imu'}}
                    s.ingest(sid,frame)
                (self.directory/'raw'/f'{sid}.source.json').write_bytes(payload)
                s.audit('import_recording',{'session_id':sid,'original_run_id':original['run_id'],'sha256':digest,
                  'source':'recorded_imu','label_policy':'자극 버튼의 이름은 보존하되 사람의 정상·비정상 검토로 자동 변환하지 않음'})
                s.db.commit();s.stop('recorded_import')
            except Exception:s.stop('import_error');raise
            return {'session_id':sid,'original_run_id':original['run_id'],'count':len(original['frames']),
              'notice':'기존 1개 녹화 = 1개 독립 기록. 버튼 정답을 사람 검토로 자동 승인하지 않습니다.'}

    def _demo_frames(self,sid,seed,scenario,count=300):
        # Existing repository's pinned generator fixture is the source, not a new
        # reference-app generator or a modification of physical sensor values.
        import sys
        runtime=str(self.root/'model_bundle/runtime')
        if runtime not in sys.path:sys.path.insert(0,runtime)
        from genguard.demo import frames
        nominal=json.loads((self.root/'model_bundle/rules/profile.json').read_text())['header']['voltage_ll_v']
        for frame in frames(sid,seed,count=count):
            frame['readings']['voltage_v']['value'] += nominal-240.0
            if scenario=='vibration':frame['readings']['vibration_rms_g']['value']*=2.2
            elif scenario=='intermittent':frame['readings']['envelope_cv']['value']+=.6
            elif scenario=='pressure':frame['readings']['oil_bar']['value']-=.7
            elif scenario=='combined':
                frame['readings']['oil_bar']['value']-=.6
                frame['readings']['coolant_c']['value']+=8
            elif scenario=='missing':frame['readings']['vibration_rms_g']['value']=None
            yield frame

    def _demo_meta(self,label,profile='fusion'):
        return {'asset_id':'GEN-DEMO-001','installation_id':'synthetic-fixture-v1','label':label,
          'condition':'기존 GenGuard 합성 안정 운전 30~70% 부하','profile':profile,
          'source_kind':'synthetic','sensor_config':'existing-edge-demo-features-v1'}

    def _demo(self,body):
        action=body.get('action');s=self.service
        if action=='scenario':
            scenario=body.get('scenario','normal')
            if scenario not in ('normal','vibration','intermittent','pressure','combined','missing'):raise ValueError('지원하지 않는 합성 조건입니다.')
            if not s.live_id or s.session(s.live_id)['meta']['source_kind']!='synthetic':raise ValueError('먼저 합성 시연 운전을 시작하세요.')
            self._demo_scenario=scenario;return {'scenario':scenario}
        if action not in ('seed-normal','seed-abnormal','start'):raise ValueError('지원하지 않는 시연 작업입니다.')
        with s.lock:
            if s.live_id:raise ValueError('진행 중인 수집을 먼저 종료하세요.')
            if action!='start' and s.job.get('state')=='running':raise ValueError('후보 학습 완료 후 합성 기록을 만드세요.')
            if action in ('seed-normal','seed-abnormal'):
                created=[];abnormal=action=='seed-abnormal'
                batch=uuid.uuid4().hex[:8]
                for i in range(4):
                    sid=s.start(self._demo_meta(f"{'비정상 자극' if abnormal else '기준'} 합성 기록 {i+1} · {batch}"))
                    seed=int(uuid.uuid4().hex[:8],16)
                    for frame in self._demo_frames(sid,seed,'vibration' if abnormal else 'normal'):s.ingest(sid,frame)
                    s.stop('synthetic_fixture');created.append(sid)
                return {'ids':created,'notice':'기존 합성 생성기의 독립 난수 기록. 실제 발전기 측정이나 사람 검토 승인이 아닙니다.'}
            self._demo_scenario=body.get('scenario','normal')
            sid=s.start(self._demo_meta('새 합성 운전 · 사용자 시연'))
        def run():
            try:
                for frame in self._demo_frames(sid,int(uuid.uuid4().hex[:8],16),'normal',300):
                    if self._closed.is_set() or s.live_id!=sid:break
                    # Inject scenario into values, never into model feature inputs.
                    mode=self._demo_scenario
                    if mode=='vibration':frame['readings']['vibration_rms_g']['value']*=2.2
                    elif mode=='intermittent':frame['readings']['envelope_cv']['value']+=.6
                    elif mode=='pressure':frame['readings']['oil_bar']['value']-=.7
                    elif mode=='combined':
                        frame['readings']['oil_bar']['value']-=.6;frame['readings']['coolant_c']['value']+=8
                    elif mode=='missing':frame['readings']['vibration_rms_g']['value']=None
                    s.ingest(sid,frame)
                    self._closed.wait(1)
            finally:
                if s.live_id==sid:s.stop('synthetic_finished')
        self._demo_thread=threading.Thread(target=run,daemon=True);self._demo_thread.start()
        return {'session_id':sid}

    def close(self):
        self._closed.set()
        if self._demo_thread:self._demo_thread.join(timeout=3)
        self.service.close()
