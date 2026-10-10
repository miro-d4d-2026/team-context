"""Inspection history API. The device owns sensor provenance and saved baselines."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import re
from uuid import uuid4

from bridge_service import BridgeError, canonical, digest, finite
from trend_service import TrendBridgeService
from inspection_model import InspectionComparator
from inspection_fixtures import seed_records, seed_current

FIELDS = {'coolant_c':(-30,150), 'oil_kgf_cm2':(0,10), 'vibration_rms_g':(0,16),
          'load_pct':(0,120), 'ambient_c':(-30,70), 'engine_rpm':(0,4000), 'elapsed_s':(0,7200)}


class InspectionService(TrendBridgeService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtime.db.executescript('''
          CREATE TABLE IF NOT EXISTS inspection_records (
            id TEXT PRIMARY KEY, inspected_at TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS inspection_requests (
            request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS inspection_metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        if not self.runtime.db.execute("SELECT 1 FROM inspection_metadata WHERE key='initialized'").fetchone():
            with self.runtime.db:
                for record in seed_records():
                    self.runtime.db.execute('INSERT INTO inspection_records VALUES (?,?,?)',
                        (record['id'], record['inspected_at'], canonical(record)))
                self.runtime.db.execute("INSERT INTO inspection_metadata VALUES ('initialized','true')")
        self.comparator = InspectionComparator(self.records())
        self.preview = seed_current()
        self.preview['values']['coolant_c'] += 7
        self.preview['id'] = 'current-preview'
        self.preview['inspected_at'] = self._now()
        self.preview['confirmed_normal'] = False
        self.preview_mode = 'scenario'
        self.preview_reference = None
        self.inspection_imu_state = {}
        self.preview['quality'] = {k:'ok' for k in FIELDS}
        self._persist_model()

    def _now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()

    def records(self):
        return [json.loads(r['payload']) for r in self.runtime.db.execute(
            'SELECT payload FROM inspection_records ORDER BY inspected_at,id')]

    def _persist_model(self, comparator=None):
        candidate=comparator or self.comparator
        payload=candidate.export_model(self.preview)
        encoded=json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        target=self.state_dir/'inspection-model.json'
        temp=self.state_dir/'inspection-model.tmp'
        old=target.read_bytes() if target.exists() else None
        try:
            temp.write_text(encoded)
            with self.runtime.db:
                self.runtime.db.execute("INSERT OR REPLACE INTO inspection_metadata VALUES ('model',?)",(canonical(payload),))
                temp.replace(target)
        except Exception:
            if old is not None: target.write_bytes(old)
            elif target.exists(): target.unlink()
            if temp.exists(): temp.unlink()
            raise

    def _input(self, body, *, saving=False):
        allowed = {'mode','values','reference_id'} | ({'request_id','label','confirmed_normal'} if saving else set())
        if not isinstance(body,dict) or set(body)-allowed or body.get('mode') not in ('scenario','hybrid') or not isinstance(body.get('values'),dict):
            raise BridgeError(422,'invalid_inspection','운전 값과 모드를 확인하세요.')
        if set(body['values'])-set(FIELDS):
            raise BridgeError(422,'invalid_fields','지원하지 않는 계측 항목입니다.')
        mode=body['mode']
        if mode=='hybrid' and 'vibration_rms_g' in body['values']:
            raise BridgeError(422,'imu_override_forbidden','진동은 연결된 IMU에서 가져옵니다.')
        for key,value in body['values'].items():
            if value is not None and (not finite(value) or not FIELDS[key][0]<=value<=FIELDS[key][1]):
                raise BridgeError(422,'invalid_value',f'{key}의 값과 단위를 확인하세요.')
        reference=body.get('reference_id')
        if reference is not None and (not isinstance(reference,str) or not any(r['id']==reference for r in self.records())):
            raise BridgeError(422,'unknown_reference','비교할 점검 기록을 다시 선택하세요.')
        current=seed_current()
        current.update(id='current-preview', inspected_at=self._now(), label='이번 점검', confirmed_normal=False,
                       source='scenario' if mode=='scenario' else 'hybrid')
        current.pop('samples',None)
        current['values']={k:body['values'].get(k) for k in FIELDS}
        current['sources']={k:'scenario' for k in FIELDS}
        current['quality']={k:'ok' if v is not None else 'missing' for k,v in current['values'].items()}
        current['sample_duration_s']=2
        if mode=='hybrid':
            imu=self.imu_status()
            current['values']['vibration_rms_g']=(imu.get('features') or {}).get('vibration_rms_g')
            current['sources']['vibration_rms_g']=imu.get('source') or 'missing'
            current['quality']['vibration_rms_g']='ok' if imu['state']=='fresh' else imu['state']
            current['installation_id']=imu.get('installation_id')
            current['acquisition']={'sample_rate_hz':imu.get('configured_sample_rate_hz'), 'window_s':2,
                                    'dc_removed':True,'feature_version':'genguard.imu.window/1',
                                    'configured_sample_rate_hz':imu.get('configured_sample_rate_hz'),
                                    'accel_range_g':imu.get('accel_range_g')}
            current['imu_window_id']=imu.get('window_id')
        return current,mode,reference

    def _fixed(self,current):
        # Point-in-time reference checks; existing /samples holds/persistence stay intact.
        values=current['values']; alerts=[]
        for rule in self.rule_profile['rules']:
            field=rule.get('field')
            if field not in ('coolant_c','oil_kgf_cm2','engine_rpm') or current.get('quality',{}).get(field)!='ok': continue
            value=values.get(field)
            if value is None: continue
            op,limit=rule.get('operator'),rule.get('threshold')
            matched=(op=='gte' and value>=limit) or (op=='lte' and value<=limit) or (op=='outside' and (value<limit[0] or value>limit[1]))
            if matched:
                alerts.append({'id':rule['id'],'title':rule['title'],'severity':rule['severity'],
                    'field':field,'value':value,'basis':rule['basis'],'checks':rule['checks'],
                    'source':current['sources'][field],'persistence_confirmed':False})
        return {'state':'urgent' if any(a['severity']=='urgent' for a in alerts) else 'inspect' if alerts else 'normal',
                'alerts':alerts,'scope':'현재값 참고 확인',
                'note':'단일 측정의 참고값 확인입니다. 기존 보호기나 연속 운전 경보를 대체하지 않습니다.'}

    def _overview(self,current,mode,reference=None):
        comparison=self.comparator.compare(current,reference_id=reference)
        fixed=self._fixed(current)
        state='urgent' if fixed['state']=='urgent' else 'inspect' if fixed['alerts'] or comparison['state']=='inspect' else comparison['state']
        operational={'state':state,'headline': '보호 참고값 도달 · 즉시 확인' if state=='urgent' else comparison['headline'], 'alerts':copy.deepcopy(fixed['alerts'])}
        if fixed['alerts'] and comparison['state']!='inspect' and state!='urgent': operational['headline']='계측값 범위 확인이 필요합니다'
        imu=self.imu_status()
        # Original installation baseline still works before enough past live inspections exist.
        from hybrid_model import score_vibration
        imu_analysis=score_vibration(imu['features'],self.baseline) if mode=='hybrid' and imu['state']=='fresh' and self.baseline else None
        if imu_analysis:
            identity=str(self.calibration.get('baseline_id'))+':'+str(imu.get('window_id'))
            imu_analysis=self._persist(imu_analysis,self.inspection_imu_state,identity,self.monotonic())
            self.inspection_imu_state=copy.deepcopy(imu_analysis)
            imu_analysis['source']=imu.get('source')
            if imu_analysis['persistent_change']:
                alert={'id':'inspection_imu_baseline_change','title':'부착 센서의 진동이 등록 기준과 달라졌습니다',
                       'severity':'watch','field':'vibration_rms_g','source':imu.get('source'),
                       'checks':imu_analysis.get('checks',[]),'basis':{'kind':'installation_baseline'}}
                operational['alerts'].append(alert)
                if state!='urgent':
                    operational.update(state='inspect',headline='진동 변화 확인이 필요합니다',
                        summary='현재 부착 상태의 정상 기준과 다른 진동이 이어집니다. 이전 발전기 점검과의 비교는 별도입니다.')
        else:
            self.inspection_imu_state={}
        records=[]
        for record in self.records():
            summary={k:v for k,v in record.items() if k!='samples'}
            summary['sample_count']=len(record.get('samples',[])) or 1
            records.append(summary)
        return {'schema_version':'genguard.inspection-overview/1',
                'equipment':{'id':current['equipment_id'],'name':'수냉식 디젤 발전기','rated_kw':10},
                'records':records,'current':copy.deepcopy(current),'comparison':comparison,
                'operational_status':operational,'fixed':fixed,'imu':imu,'imu_analysis':imu_analysis,'mode':mode,
                'defaults':seed_current()['values'],
                'provenance_note':'수온·유압은 모의 입력입니다. 진동은 선택한 모드의 출처를 표시합니다.',
                'model_scope':'정상 점검 기록으로 학습한 조건별 기대값과 잔차 비교. 고장 원인 확정·잔여수명 추정이 아닙니다.'}

    def inspection_overview(self):
        # Refresh live input through the server, never resurrect a stale cached measurement.
        if self.preview_mode=='hybrid':
            values={k:v for k,v in self.preview['values'].items() if k!='vibration_rms_g'}
            current,_,reference=self._input({'mode':'hybrid','values':values,'reference_id':self.preview_reference})
        else:
            current,reference=copy.deepcopy(self.preview),self.preview_reference
            current['inspected_at']=self._now()
        return self._overview(current,self.preview_mode,reference)

    def inspection_compare(self,body):
        current,mode,reference=self._input(body)
        result=self._overview(current,mode,reference)
        self.preview,self.preview_mode=copy.deepcopy(current),mode
        self.preview_reference=reference
        return result

    def inspection_save(self,body):
        if not isinstance(body,dict) or not isinstance(body.get('request_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',body['request_id']):
            raise BridgeError(422,'request_id_required','저장 요청 ID가 필요합니다.')
        fingerprint=digest(body)
        previous=self.runtime.db.execute('SELECT fingerprint,result FROM inspection_requests WHERE request_id=?',(body['request_id'],)).fetchone()
        if previous:
            if previous['fingerprint']!=fingerprint: raise BridgeError(409,'save_conflict','같은 저장 요청의 값이 변경되었습니다.')
            result=json.loads(previous['result']); result['duplicate']=True
            return result
        current,mode,reference=self._input(body,saving=True)
        if type(body.get('confirmed_normal',False)) is not bool:
            raise BridgeError(422,'invalid_confirmation','정상 확인 여부를 확인하세요.')
        label=body.get('label','정기 점검')
        if not isinstance(label,str) or not 1<=len(label.strip())<=60:
            raise BridgeError(422,'invalid_label','점검 이름은 1~60자입니다.')
        before=self._overview(current,mode,reference)
        confirmed=body.get('confirmed_normal',False)
        if confirmed and (before['comparison']['state']!='normal' or before['fixed']['alerts']):
            raise BridgeError(422,'normal_review_rejected','이상 또는 비교 불가 항목이 있습니다. 관찰 기록으로 저장하고 먼저 점검하세요.')
        if mode=='hybrid' and current.get('imu_window_id'):
            if any(r.get('imu_window_id')==current['imu_window_id'] for r in self.records()):
                raise BridgeError(409,'duplicate_imu_record','같은 IMU 측정 창은 이미 저장되어 있습니다. 새 측정 이후 저장하세요.')
        current.update(id='inspection-'+uuid4().hex,label=label.strip(),confirmed_normal=confirmed,
                       assessment={'state':before['operational_status']['state'],'summary':before['comparison']['summary'],
                                   'reference_id':before['comparison'].get('reference_id')})
        # Save the comparison before this record can participate in any model training.
        result=dict(before,saved_record=copy.deepcopy(current),duplicate=False)
        result['records']=before['records']+[dict(current,sample_count=1)]
        with self.runtime.db:
            self.runtime.db.execute('INSERT INTO inspection_records VALUES (?,?,?)',(current['id'],current['inspected_at'],canonical(current)))
            self.runtime.db.execute('INSERT INTO inspection_requests VALUES (?,?,?)',(body['request_id'],fingerprint,canonical(result)))
        self.preview,self.preview_mode=copy.deepcopy(current),mode
        self.preview_reference=reference
        return result

    def inspection_retrain(self,body):
        if body!={}: raise BridgeError(422,'invalid_retrain','정상 확인된 기록만 다시 학습할 수 있습니다.')
        candidate=InspectionComparator(self.records())
        self._persist_model(candidate)
        self.comparator=candidate
        return self.inspection_overview()
