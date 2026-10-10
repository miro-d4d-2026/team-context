import json
import tempfile
import time
import unittest
from unittest.mock import patch
from learning_workspace import LearningWorkspace
from review_learning.model import PROFILES

class LearningWorkspaceTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.w=LearningWorkspace(self.tmp.name)
 def tearDown(self):self.w.close();self.tmp.cleanup()
 def wait(self):
  t=self.w.service.training_thread
  if t:t.join(10)
  self.assertNotEqual(self.w.service.job['state'],'running');return self.w.service.job
 def seed_review(self,abnormal=False):
  ids=self.w.dispatch('demo',{'action':'seed-abnormal' if abnormal else 'seed-normal'})['ids']
  for sid,role in zip(ids,['fit','fit','calibration','validation']):
   self.w.dispatch('review',{'session_id':sid,'status':'abnormal' if abnormal else 'normal','role':role,'reviewer':'test','note':'독립 합성 실험의 값을 확인'})
  return ids
 def test_first_install(self):
  state=self.w.state();self.assertEqual(state['result']['state'],'collecting');self.assertIsNone(state['result']['score'])
  self.assertEqual(PROFILES['vibration']['targets'],['vibration_rms_g','envelope_cv'])
 def test_full_normal_label_learning_and_restart(self):
  normals=self.seed_review();abnormals=self.seed_review(True)
  self.w.dispatch('train',{'kind':'normal','session_ids':normals});self.assertEqual(self.wait()['state'],'completed')
  normal=self.w.state()['models'][0];self.assertEqual(normal['core_algorithm'],'context-ridge-residual-covariance/1')
  self.w.dispatch('activate',{'kind':'normal','model_id':normal['id']})
  self.w.dispatch('train',{'kind':'feedback','session_ids':normals+abnormals});self.assertEqual(self.wait()['state'],'completed')
  model=self.w.state()['feedback_models'][0];self.assertLessEqual(model['validation']['false_positive_rate'],.1);self.assertGreaterEqual(model['validation']['abnormal_recall'],.8)
  self.w.dispatch('activate',{'kind':'feedback','model_id':model['id']})
  s=self.w.service;sid=s.start(self.w._demo_meta('새 검증 운전'))
  self.assertEqual(s.session(sid)['feedback_model_id'],model['id']);self.assertEqual(s.session(sid)['model_id'],normal['id'])
  with self.assertRaises(ValueError):self.w.dispatch('activate',{'kind':'feedback','model_id':model['id']})
  for f in self.w._demo_frames(sid,875234,'normal',5):r=s.ingest(sid,f)
  self.assertEqual(r['feedback']['state'],'normal_like')
  for f in self.w._demo_frames(sid,999834,'vibration',10):
   f['seq']+=5;f['elapsed_s']+=10;r=s.ingest(sid,f)
  self.assertEqual(r['feedback']['state'],'abnormal_like');self.assertEqual(r['state'],'attention')
  s.stop();self.w.dispatch('note',{'session_id':sid,'author':'operator','body':'고정 상태 확인'})
  old=[r['result'] for r in s.frames(sid)]
  self.w.dispatch('review',{'session_id':normals[0],'status':'uncertain','role':'fit','reviewer':'operator','note':'라벨 재검토'})
  self.assertFalse(self.w.state()['models'][0]['labels_current']);self.assertFalse(self.w.state()['feedback_models'][0]['labels_current'])
  self.assertEqual(old,[r['result'] for r in s.frames(sid)])
  self.w.close();self.w=LearningWorkspace(self.tmp.name)
  self.assertEqual(self.w.session_detail(sid)['report']['notes'][0]['body'],'고정 상태 확인')
 def test_insufficient_roles_and_unreviewed(self):
  ids=self.w.dispatch('demo',{'action':'seed-normal'})['ids']
  with self.assertRaises(ValueError):self.w.dispatch('train',{'session_ids':ids})
  for sid in ids:self.w.dispatch('review',{'session_id':sid,'status':'normal','role':'fit','reviewer':'a','note':'b'})
  with self.assertRaises(ValueError):self.w.dispatch('train',{'session_ids':ids})
 def packet(self,seq=1,rms=.05):
  return {'source':'live_imu','stream_id':'real-stream','installation_id':'fixed','window_seq':seq,'duration_s':2.,'sample_rate_hz':207.93+seq/100,'configured_sample_rate_hz':208,'accel_range_g':4,'features':{'vibration_rms_g':rms,'envelope_cv':.1},'quality':{'valid':True,'flags':[]}}
 def test_actual_packet_contract_and_duplicates(self):
  self.w.ingest_imu(self.packet());sid=self.w.dispatch('start',{})['session_id']
  for i in range(2,6):self.w.ingest_imu(self.packet(i),{}, {'state':'change'})
  self.assertEqual(self.w.service.session(sid)['count'],4)
  self.w.ingest_imu(self.packet(5));self.assertEqual(self.w.service.session(sid)['count'],4)
  self.assertEqual(self.w.summary()['result']['existing_monitor']['state'],'change')
  self.w.dispatch('stop',{});self.w.ingest_imu(self.packet(6));self.assertEqual(self.w.service.session(sid)['count'],4)
 def test_stream_change_stops_capture(self):
  self.w.ingest_imu(self.packet());sid=self.w.dispatch('start',{})['session_id']
  p=self.packet(2);p['stream_id']='other';self.w.ingest_imu(p)
  self.assertIsNone(self.w.service.live_id);self.assertEqual(self.w.service.session(sid)['stop_reason'],'stream_changed')
 def test_start_cancels_training_without_wait(self):
  ids=self.seed_review();threading=__import__('threading');started=threading.Event();release=threading.Event()
  from review_learning.service import fit_model as real_fit
  def slow(*args):started.set();release.wait(2);return real_fit(*args)
  with patch('review_learning.service.fit_model',slow):
   self.w.dispatch('train',{'session_ids':ids});self.assertTrue(started.wait(2))
   self.w.ingest_imu(self.packet());t=time.monotonic();self.w.dispatch('start',{})
   self.assertLess(time.monotonic()-t,.3);release.set();self.assertEqual(self.wait()['state'],'cancelled')
  self.assertFalse(self.w.state()['models'])
 def test_failed_candidate_keeps_old(self):
  ids=self.seed_review();self.w.dispatch('train',{'session_ids':ids});self.wait()
  old=self.w.state()['models'][0];self.w.dispatch('activate',{'model_id':old['id']});second=self.seed_review();s=self.w.service
  with s.lock:
   for row in s.db.execute('SELECT seq,data FROM frames WHERE session_id=?',(second[-1],)).fetchall():
    frame=json.loads(row['data']);frame['values']['vibration_rms_g']*=5;s.db.execute('UPDATE frames SET data=? WHERE session_id=? AND seq=?',(json.dumps(frame),second[-1],row['seq']))
   s.db.commit()
  self.w.dispatch('train',{'session_ids':second});self.assertEqual(self.wait()['state'],'rejected')
  self.assertEqual([m['id'] for m in self.w.state()['models'] if m['active']],[old['id']])

if __name__=='__main__':unittest.main()

class VibrationOnlyLifecycleTest(unittest.TestCase):
 def test_vibration_only_independent_normal_training(self):
  import math
  with tempfile.TemporaryDirectory() as directory:
   w=LearningWorkspace(directory);s=w.service;ids=[]
   try:
    for run,role in enumerate(['fit','fit','calibration','validation']):
     meta={'asset_id':'IMU-only','installation_id':'fixture','label':'인공 테스트','condition':'fixed','profile':'vibration','source_kind':'synthetic','sensor_config':'test2s'}
     sid=s.start(meta);ids.append(sid)
     for i in range(40):s.ingest(sid,{'seq':i+1,'elapsed_s':(i+1)*2.,'values':{'vibration_rms_g':.05+.001*math.sin(i+run),'envelope_cv':.12+.003*math.cos(i+run)},'sources':{'vibration_rms_g':'scenario','envelope_cv':'scenario'}})
     s.stop();s.review(sid,'normal',role,'test','synthetic fixture')
    s.training(ids);s.training_thread.join(5)
    self.assertEqual(s.job['state'],'completed');model=s.models()[0]
    self.assertEqual(model['context'],[]);self.assertNotIn('vibration_peak_g',model['targets'])
    s.activate(model['id']);sid=s.start(meta)
    for i in range(4):r=s.ingest(sid,{'seq':i+1,'elapsed_s':(i+1)*2.,'values':{'vibration_rms_g':.2,'envelope_cv':.1},'sources':{'vibration_rms_g':'scenario','envelope_cv':'scenario'}})
    self.assertEqual(r['state'],'attention')
   finally:w.close()
