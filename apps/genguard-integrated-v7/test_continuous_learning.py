"""Numerical learning/inference regressions using isolated synthetic fixtures.
The fixtures emulate live packets for software tests, not hardware performance.
"""
import copy
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from learning_workspace import LearningWorkspace, FEATURE_VERSION
from review_learning.service import pack


class ContinuousLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory()
        w=LearningWorkspace(cls.fixture.name);s=w.service;ids=[]
        meta={'asset_id':'GEN-10K-001','installation_id':'fixed','profile':'vibration',
              'source_kind':'hardware','label':'Software fixture','condition':'same fixture',
              'sensor_config':f'208Hz;4g;{FEATURE_VERSION}'}
        for run in range(4):
            sid=s.start(meta);ids.append(sid)
            for i in range(80):
                rms=.05+.001*math.sin(i+run);cv=.12+.003*math.cos(i+run)
                if i>=40:rms*=3 if i<60 else 1.3;cv+=0 if i<60 else .8
                s.ingest(sid,{'seq':i+1,'elapsed_s':2*(i+1),'values':{'vibration_rms_g':rms,'envelope_cv':cv},
                              'sources':{'vibration_rms_g':'live_imu','envelope_cv':'live_imu'}})
            s.stop();s.add_label(sid,'normal',0,80,'test','software normal fixture')
            s.add_label(sid,'abnormal',80,160,'test','software abnormal fixture')
        s.automatic_training(ids);s.training_thread.join(10)
        if s.job['state']!='completed':raise AssertionError(s.job)
        cls.normal=s.models()[0]['id'];cls.feedback=s.feedback_models()[0]['id']
        s.activate(cls.normal);s.feedback_activate(cls.feedback)
        cls.count=len(s.list_sessions());w.close()
    @classmethod
    def tearDownClass(cls):cls.fixture.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        with sqlite3.connect(Path(self.fixture.name)/'genguard.sqlite') as src, sqlite3.connect(Path(self.tmp.name)/'genguard.sqlite') as dst:src.backup(dst)
        self.w=LearningWorkspace(self.tmp.name)
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def packet(self,seq,rms=.05,cv=.12,**kwargs):
        return {'source':'live_imu','stream_id':'stream-1','installation_id':'fixed','window_seq':seq,
                'duration_s':2.,'configured_sample_rate_hz':208,'accel_range_g':4,
                'features':{'vibration_rms_g':rms,'envelope_cv':cv},'quality':{'valid':True},**kwargs}
    def feed(self,seq,rms=.05,cv=.12,**kwargs):
        return self.w.ingest_imu(self.packet(seq,rms,cv,**kwargs))
    def test_without_recording_normal_strong_intermittent(self):
        for seq in range(1,4):result=self.feed(seq)
        self.assertEqual(result['state'],'reference');self.assertEqual(result['feedback']['state'],'normal_like')
        for seq in range(4,7):result=self.feed(seq,.15)
        self.assertEqual(result['state'],'attention');self.assertEqual(result['feedback']['state'],'abnormal_like')
        for seq in range(7,10):result=self.feed(seq,.065,.92)
        self.assertEqual(result['state'],'attention');self.assertEqual(result['feedback']['state'],'abnormal_like')
        state=self.w.summary();self.assertFalse(state['recording']);self.assertTrue(state['fresh'])
        self.assertEqual((state['model_id'],state['feedback_model_id']),(self.normal,self.feedback))
        self.assertEqual(len(self.w.service.list_sessions()),self.count)
    def test_report_start_stop_does_not_reset_monitor_and_keeps_ids(self):
        self.feed(1,.15);self.feed(2,.15)
        sid=self.w.dispatch('start',{})['session_id']
        result=self.feed(3,.15);self.assertEqual(result['state'],'attention')
        self.assertEqual(self.w.service.frames(sid)[0]['result']['model_id'],self.normal)
        self.assertEqual(self.w.service.session(sid)['feedback_model_id'],self.feedback)
        self.w.dispatch('stop',{});result=self.feed(4,.15)
        self.assertEqual(result['consecutive_windows'],4);self.assertEqual(self.w.service.session(sid)['count'],1)
    def test_duplicate_gap_invalid_stale_and_stream_reset(self):
        self.feed(1,.15);self.feed(2,.15)
        self.feed(2,.15);self.assertEqual(self.w.monitor.counters['normal'],2)
        result=self.feed(4,.15);self.assertEqual(result['state'],'unknown')
        self.assertEqual(self.feed(5,.15)['consecutive_windows'],1)
        result=self.feed(6,.15,quality={'valid':False});self.assertEqual(result['state'],'unknown')
        self.assertEqual(self.feed(7,.15)['consecutive_windows'],1)
        self.assertEqual(self.feed(8,.15,stream_id='new')['consecutive_windows'],1)
        now=self.w.monitor.last_rx
        with patch('review_learning.monitor.time.monotonic',return_value=now+10):
            self.assertFalse(self.w.summary()['fresh']);result=self.feed(9,.15,stream_id='new')
            self.assertEqual(result['consecutive_windows'],1)
    def test_idle_not_fault_then_restart_streak(self):
        for seq in range(1,4):self.feed(seq,.15)
        result=self.w.ingest_imu(self.packet(4,.001),baseline_analysis={'pattern':'idle'})
        self.assertEqual(result['state'],'idle');self.assertIsNone(result['ratio'])
        self.assertEqual(self.feed(5,.15)['consecutive_windows'],1)
    def test_restart_keeps_models_but_old_analysis_not_fresh(self):
        self.feed(1,.15);self.feed(2,.15);self.w.close();self.w=LearningWorkspace(self.tmp.name)
        self.assertFalse(self.w.summary()['fresh']);self.assertEqual(self.w.summary()['result']['state'],'unknown')
        result=self.feed(3,.15);self.assertEqual(result['model_id'],self.normal);self.assertEqual(result['consecutive_windows'],1)
    def test_wrong_install_config_source_do_not_use_model(self):
        for packet in [self.packet(1,installation_id='other'),self.packet(2,accel_range_g=8),self.packet(3,source='scenario')]:
            result=self.w.ingest_imu(packet);self.assertEqual(result['state'],'unknown');self.assertIsNone(result['score'])
    def test_ambiguous_condition_does_not_choose_newest(self):
        model=self.w.service.models()[0];model.update(id='other-condition',dependencies={},splits={})
        model['signature']['condition']='other load'
        self.w.service.db.execute('INSERT INTO models VALUES(?,?,1)',(model['id'],pack(model)));self.w.service.db.commit()
        result=self.feed(1);self.assertEqual(result['state'],'unknown');self.assertIn('여러 개',result['reason'])
    def test_model_change_resets_streak_without_retraining(self):
        self.feed(1,.15);self.feed(2,.15)
        model=self.w.service.models()[0];model['id']='replacement'
        self.w.service.db.execute('UPDATE models SET active=0')
        self.w.service.db.execute('INSERT INTO models VALUES(?,?,1)',(model['id'],pack(model)));self.w.service.db.commit()
        result=self.feed(3,.15);self.assertEqual(result['model_id'],'replacement');self.assertEqual(result['consecutive_windows'],1)
    def test_missing_models_and_nonfinite_values_do_not_claim_normal(self):
        packet=self.packet(1);packet['features']['vibration_rms_g']=float('nan')
        self.assertEqual(self.w.ingest_imu(packet)['state'],'unknown')
        self.w.service.db.execute('UPDATE models SET active=0');self.w.service.db.execute('UPDATE feedback_models SET active=0');self.w.service.db.commit()
        result=self.feed(2);self.assertEqual(result['state'],'unknown');self.assertIn('없습니다',result['reason'])

    def test_report_idle_window_preserves_phase_and_live_result(self):
        self.feed(1)
        sid=self.w.dispatch('start',{})['session_id']
        self.w.ingest_imu(self.packet(2,.001),baseline_analysis={'pattern':'idle'})
        saved=self.w.service.frames(sid)[0]
        self.assertEqual(saved['frame']['phase'],'stopped')
        self.assertEqual(saved['result']['state'],'idle')
        self.assertIsNone(saved['result']['ratio'])

    def test_recorded_packet_not_live_and_failure_invalidates_previous(self):
        self.feed(1)
        self.feed(2,source='recorded_imu')
        self.assertEqual(self.w.summary()['source'],'recorded_imu')
        self.assertFalse(self.w.summary()['fresh'])
        self.w.monitor.invalidate('inference unavailable','failed-window')
        self.assertFalse(self.w.summary()['fresh'])
        self.assertEqual(self.w.summary()['window_id'],'failed-window')
        self.assertIsNone(self.w.summary()['result']['score'])

    def test_operation_persists_same_window_ml_without_recording_or_polling(self):
        from operation_service import OperationService
        from test_api import FakeClock, adapter_template
        from adapter.imu_to_api import utc_text
        with tempfile.TemporaryDirectory() as directory:
            clock=FakeClock();service=OperationService(directory,clock=clock.wall,monotonic=clock.monotonic)
            service.learning_workspace.close();service.learning_workspace=self.w
            try:
                service.operation_input['mode']='hybrid'
                packet=adapter_template();packet.update(installation_id='fixed',configured_sample_rate_hz=208,accel_range_g=4)
                packet['features'].update(vibration_rms_g=.15,envelope_cv=.12)
                for seq in range(1,4):
                    clock.advance(2);packet.update(window_seq=seq,measured_at=utc_text(clock.wall()))
                    accepted=service.receive_imu(copy.deepcopy(packet))
                saved=json.loads(service.runtime.db.execute("SELECT payload FROM operation_metadata WHERE key='latest_edge_analysis'").fetchone()[0])
                self.assertEqual(saved['window_id'],accepted['id'])
                self.assertEqual(saved['review_learning']['window_id'],accepted['id'])
                self.assertEqual(saved['review_learning']['result']['state'],'attention')
                self.assertEqual(saved['state'],'inspect')
                self.assertFalse(saved['review_learning']['recording'])
                self.assertEqual(saved['review_learning']['model_id'],self.normal)
                with patch.object(self.w,'ingest_imu',side_effect=ValueError('forced test failure')):
                    clock.advance(2);packet.update(window_seq=4,measured_at=utc_text(clock.wall()));accepted=service.receive_imu(packet)
                failed=service.imu_status()['last_analysis']
                self.assertEqual(failed['review_learning']['window_id'],accepted['id'])
                self.assertEqual(failed['review_learning']['result']['state'],'unknown')
            finally:
                service.learning_workspace=None;service.close()


if __name__=='__main__':unittest.main()
