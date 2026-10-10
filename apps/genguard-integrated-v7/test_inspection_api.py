"""End-to-end inspection history tests; synthetic fixtures, no physical board."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inspection_service import InspectionService
from test_api import FakeClock, running_server, request, adapter_template
from adapter.imu_to_api import utc_text


class InspectionAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='genguard-inspection-test-')
        self.clock=FakeClock()
        self.patch=patch('test_api.HybridBridgeService',InspectionService)
        self.patch.start()
        self.server=running_server(self.clock,self.tmp.name)
        self.base=self.server.__enter__()
        status,self.first,_=request(self.base,'/api/inspections/overview')
        self.assertEqual(status,200,self.first)
        self.normal={'mode':'scenario','values':self.first['defaults']}

    def tearDown(self):
        self.server.__exit__(None,None,None)
        self.patch.stop();self.tmp.cleanup()

    def post(self,body,path='/api/inspections/compare',status=200):
        actual,result,_=request(self.base,path,body)
        self.assertEqual(actual,status,result)
        return result

    def test_same_normal_and_individual_compound_changes(self):
        normal=self.post(self.normal)
        self.assertEqual(normal['comparison']['state'],'normal')
        for field,value in [('coolant_c',87),('oil_kgf_cm2',2.6),('vibration_rms_g',.08)]:
            b=copy.deepcopy(self.normal);b['values'][field]=value
            r=self.post(b)
            channels={c['field']:c for c in r['comparison']['channels']}
            self.assertEqual(channels[field]['state'],'inspect')
        b=copy.deepcopy(self.normal);b['values'].update(coolant_c=87,oil_kgf_cm2=2.6,vibration_rms_g=.08)
        self.assertEqual(len(self.post(b)['comparison']['recommendations']),3)

    def test_context_mismatch_and_missing_are_not_normal(self):
        for key,value in [('load_pct',90),('elapsed_s',30),('ambient_c',None)]:
            b=copy.deepcopy(self.normal);b['values'][key]=value
            self.assertEqual(self.post(b)['comparison']['state'],'unavailable')
        b=copy.deepcopy(self.normal);b['values']['oil_kgf_cm2']=None
        r=self.post(b)
        self.assertEqual(r['comparison']['state'],'unavailable')
        self.assertEqual(next(c for c in r['comparison']['channels'] if c['field']=='oil_kgf_cm2')['state'],'unavailable')

    def test_protection_reference_cannot_be_hidden_by_unavailable_comparison(self):
        b=copy.deepcopy(self.normal);b['values'].update(load_pct=90,coolant_c=101)
        r=self.post(b)
        self.assertEqual(r['comparison']['state'],'unavailable')
        self.assertEqual(r['operational_status']['state'],'urgent')
        self.assertTrue(r['operational_status']['alerts'])

    def test_hybrid_without_imu_is_explicitly_unavailable(self):
        b=copy.deepcopy(self.normal);b['mode']='hybrid';del b['values']['vibration_rms_g']
        r=self.post(b)
        ch=next(c for c in r['comparison']['channels'] if c['field']=='vibration_rms_g')
        self.assertEqual(ch['state'],'unavailable')
        self.assertIsNone(ch['current'])
        self.assertEqual(ch['source'],'missing')
        b['values']['vibration_rms_g']=.045
        self.post(b,status=422)

    def test_save_idempotent_and_abnormal_never_enters_training(self):
        b=copy.deepcopy(self.normal);b.update(request_id='save-unique-0001',label='수온 변화 기록',confirmed_normal=False)
        b['values']['coolant_c']=88
        r=self.post(b,'/api/inspections/save')
        saved=r['saved_record']
        self.assertFalse(saved['confirmed_normal'])
        retry=self.post(b,'/api/inspections/save')
        self.assertTrue(retry['duplicate'])
        self.assertEqual(saved,retry['saved_record'])
        changed=copy.deepcopy(b);changed['values']['coolant_c']=89
        self.post(changed,'/api/inspections/save',status=409)
        self.post({},'/api/inspections/retrain')
        after=self.post(self.normal)
        self.assertNotIn(saved['id'],after['comparison']['model']['training_record_ids'])
        self.assertEqual(len(after['records']),len(self.first['records'])+1)

    def test_abnormal_and_unknown_cannot_be_confirmed_normal(self):
        for patch_values in ({'coolant_c':88},{'load_pct':90},{'oil_kgf_cm2':None}):
            b=copy.deepcopy(self.normal);b.update(request_id='cannot-learn-normal-01',confirmed_normal=True)
            b['values'].update(patch_values)
            self.post(b,'/api/inspections/save',status=422)

    def test_explicit_normal_learning_after_record_then_new_measurement(self):
        b=copy.deepcopy(self.normal);b.update(request_id='save-normal-0001',confirmed_normal=True)
        before=self.post(b,'/api/inspections/save')
        identifier=before['saved_record']['id']
        self.assertNotIn(identifier,before['comparison']['model']['training_record_ids'])
        self.clock.advance(2)
        self.post({},'/api/inspections/retrain')
        after=self.post(self.normal)
        self.assertIn(identifier,after['comparison']['model']['training_record_ids'])
        self.assertFalse(after['comparison']['model']['current_used_for_training'])
        self.assertTrue((Path(self.tmp.name)/'inspection-model.json').is_file())

    def test_persistence_survives_service_restart(self):
        b=copy.deepcopy(self.normal);b.update(request_id='persist-normal-0001')
        saved=self.post(b,'/api/inspections/save')['saved_record']
        self.server.__exit__(None,None,None)
        self.server=running_server(self.clock,self.tmp.name);self.base=self.server.__enter__()
        _,after,_=request(self.base,'/api/inspections/overview')
        self.assertIn(saved['id'],[r['id'] for r in after['records']])
        self.assertEqual(len(after['records']),len(self.first['records'])+1)

    def test_validation_auth_and_historical_reference(self):
        self.assertEqual(request(self.base,'/api/inspections/overview',token=None)[0],401)
        self.assertEqual(request(self.base,'/api/inspections/compare',self.normal,token=None)[0],401)
        for b in ({},dict(self.normal,reference_id='missing'),dict(self.normal,foo='bad')):
            self.post(b,status=422)
        self.post({'mode':'scenario','values':{'coolant_c':True}},status=422)
        self.post({'mode':'scenario','values':{'oil_kgf_cm2':-1}},status=422)
        b=copy.deepcopy(self.normal);b['reference_id']=self.first['records'][-1]['id']
        self.assertEqual(self.post(b)['comparison']['reference_id'],b['reference_id'])

    def test_selected_reference_is_preserved_on_refresh(self):
        body=copy.deepcopy(self.normal)
        body['reference_id']=self.first['records'][-2]['id']
        selected=self.post(body)['comparison']['reference_id']
        self.assertEqual(selected,body['reference_id'])
        _,refreshed,_=request(self.base,'/api/inspections/overview')
        self.assertEqual(refreshed['comparison']['reference_id'],selected)

    def test_live_baseline_no_fake_history_and_persistent_sensor_alert(self):
        packet=adapter_template();seq=0
        def send(rms=.045):
            nonlocal seq
            seq+=1;self.clock.advance(2)
            p=copy.deepcopy(packet)
            p.update(window_seq=seq,measured_at=utc_text(self.clock.wall()))
            p['features']['vibration_rms_g']=rms
            return self.post(p,'/api/imu/windows')
        send()
        self.post({'confirmed_normal':True},'/api/imu/calibration/start')
        for _ in range(15):send()
        b=copy.deepcopy(self.normal);b['mode']='hybrid';del b['values']['vibration_rms_g']
        result=self.post(b)
        self.assertEqual(result['imu_analysis']['state'],'within_baseline')
        self.assertEqual(result['comparison']['channels'][2]['state'],'unavailable')
        send(.12);one=self.post(b)
        self.assertFalse(one['imu_analysis']['persistent_change'])
        for _ in range(5):
            _,refresh,_=request(self.base,'/api/inspections/overview')
            self.assertFalse(refresh['imu_analysis']['persistent_change'])
        send(.12);self.post(b)
        send(.12);three=self.post(b)
        self.assertEqual(three['operational_status']['state'],'inspect')
        self.assertTrue(three['imu_analysis']['persistent_change'])
        self.assertTrue(three['operational_status']['alerts'])
        self.assertFalse(three['fixed']['alerts'])
        b['values']['coolant_c']=101
        self.assertEqual(self.post(b)['operational_status']['state'],'urgent')
        self.clock.advance(6)
        b['values']['coolant_c']=80.2
        stale=self.post(b)
        self.assertIsNone(stale['imu_analysis'])
        self.assertFalse(stale['operational_status']['alerts'])

    def test_new_dashboard_and_old_lab_are_both_available(self):
        for path in ('/','/inspection.css','/inspection.mjs','/lab'):
            status,data,headers=request(self.base,path)
            self.assertEqual(status,200,(path,data))

if __name__=='__main__': unittest.main()

class ModelActivationTests(unittest.TestCase):
    def test_failed_export_keeps_previous_model_and_file(self):
        with tempfile.TemporaryDirectory() as state:
            service=InspectionService(state)
            try:
                previous=service.comparator
                artifact=(Path(state)/'inspection-model.json').read_bytes()
                db=service.runtime.db.execute("SELECT payload FROM inspection_metadata WHERE key='model'").fetchone()[0]
                with patch('inspection_service.InspectionComparator.export_model',side_effect=ValueError('invalid candidate')):
                    with self.assertRaises(ValueError):service.inspection_retrain({})
                self.assertIs(service.comparator,previous)
                self.assertEqual((Path(state)/'inspection-model.json').read_bytes(),artifact)
                self.assertEqual(service.runtime.db.execute("SELECT payload FROM inspection_metadata WHERE key='model'").fetchone()[0],db)
            finally:service.close()
