import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {GenGuardClient} from './client.mjs';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; delete globalThis.location; });
function response(data, status=200) { return {ok:status>=200&&status<300,status,text:async()=>JSON.stringify(data)}; }
function makeClient(options={}) { const client=new GenGuardClient({baseUrl:'http://127.0.0.1:8765',...options});client.sessionId='owned-session';return client; }
const result = (seq=1,extra={}) => ({schema_version:'genguard.bridge.result/2',session_id:'owned-session',seq,status:'normal',...extra});

test('config sends token without a body and preserves source on samples', async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url,options});return response(url.endsWith('/config')?{fields:[]}:result());};
  const client=makeClient({token:'demo-secret'});await client.getConfig();await client.sendSample({oil_kgf_cm2:3.2});
  assert.equal(calls[0].options.headers.Authorization,'Bearer demo-secret');assert.equal(calls[0].options.body,undefined);
  assert.deepEqual(JSON.parse(calls[1].options.body),{phase:'running',quality:{},seq:1,source:'hybrid_demo',values:{oil_kgf_cm2:3.2}});
  assert.equal(client.nextSeq,2);assert.equal(client.pendingSample,null);
});

test('network failure keeps exact pending sequence; reordered keys retry idempotently',async()=>{
  const bodies=[];globalThis.fetch=async(url,options)=>{bodies.push(options.body);if(bodies.length===1)throw Error('dropped after server accepted');return response(result(1,{duplicate:true}));};
  const client=makeClient();await assert.rejects(client.sendSample({coolant_c:80,oil_kgf_cm2:3.2}),{code:'network'});
  assert.equal(client.nextSeq,1);const pending=client.pendingSample;pending.values.coolant_c=200;assert.equal(client.pendingSample.values.coolant_c,80);
  await assert.rejects(client.sendSample({coolant_c:89,oil_kgf_cm2:3.2}),{code:'pending_conflict'});assert.equal(bodies.length,1);
  const retried=await client.sendSample({oil_kgf_cm2:3.2,coolant_c:80});assert.equal(retried.duplicate,true);assert.equal(bodies[0],bodies[1]);assert.equal(client.nextSeq,2);
});

test('timeout preserves payload and retry advances sequence only once',async()=>{
  globalThis.fetch=async(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))));
  const client=makeClient({timeoutMs:10});await assert.rejects(client.sendSample({engine_rpm:1800}),{code:'timeout'});assert.equal(client.nextSeq,1);assert.ok(client.pendingSample);
  globalThis.fetch=async()=>response(result());await client.sendSample({engine_rpm:1800});assert.equal(client.nextSeq,2);
});

test('overlap is blocked, including session close',async()=>{
  let resolve;globalThis.fetch=()=>new Promise(done=>{resolve=done;});const client=makeClient();const first=client.sendSample({engine_rpm:1800});
  await assert.rejects(client.sendSample({engine_rpm:1800}),{code:'busy'});await assert.rejects(client.closeSession(),{code:'busy'});await assert.rejects(client.getConfig(),{code:'busy'});
  resolve(response(result()));await first;assert.equal(client.busy,false);
});

test('ambiguous start retries the same request ID and never takes another session',async()=>{
  const bodies=[];let n=0;globalThis.fetch=async(url,options)=>{bodies.push(options.body);if(n++===0)throw Error('offline');return response({session_id:'mine',next_seq:1});};
  const client=new GenGuardClient({baseUrl:'http://localhost:8765'});await assert.rejects(client.startSession(),{code:'network'});await client.startSession();assert.equal(bodies[0],bodies[1]);assert.equal(client.sessionId,'mine');
  const other=new GenGuardClient({baseUrl:'http://localhost:8765'});globalThis.fetch=async()=>response({error:'active_session'},409);await assert.rejects(other.startSession(),{status:409});assert.equal(other.sessionId,null);
  const noClose=await other.closeSession();assert.equal(noClose.session_id,null);
});

test('wrong sequence and invalid JSON responses do not discard pending sample',async()=>{
  const client=makeClient();globalThis.fetch=async()=>response(result(2));await assert.rejects(client.sendSample({coolant_c:80}),{code:'invalid_response'});assert.equal(client.nextSeq,1);
  globalThis.fetch=async()=>({ok:true,status:200,text:async()=>'<html>wrong port</html>'});await assert.rejects(client.sendSample({coolant_c:80}),{code:'invalid_response'});assert.ok(client.pendingSample);
});

test('close only owned session, clears pending after confirmed close, and permits new session',async()=>{
  const client=makeClient();globalThis.fetch=async()=>{throw Error('offline');};await assert.rejects(client.sendSample({coolant_c:80}));
  const paths=[];globalThis.fetch=async(url)=>{paths.push(url);return response({session_id:'owned-session',closed:true});};await client.closeSession();assert.equal(paths[0],'http://127.0.0.1:8765/api/sessions/owned-session/close');assert.equal(client.pendingSample,null);assert.equal(client.sessionId,null);assert.equal(client.nextSeq,1);
  await assert.rejects(client.sendSample({coolant_c:80}),{code:'no_session'});
});

test('authentication errors include actionable help; token not in URL',async()=>{
  let called;globalThis.fetch=async(url)=>{called=url;return response({error:'unauthorized'},401);};const client=makeClient({token:'secret'});
  await assert.rejects(client.getConfig(),error=>error.status===401&&error.message.includes('토큰'));assert.ok(!called.includes('secret'));
});

test('mixed HTTPS/HTTP is rejected before network and inputs reject nonfinite values',async()=>{
  let calls=0;globalThis.fetch=async()=>{calls++;return response({});};globalThis.location={protocol:'https:'};const client=makeClient();await assert.rejects(client.getConfig(),{code:'mixed_content'});assert.equal(calls,0);
  delete globalThis.location;await assert.rejects(client.sendSample({coolant_c:NaN}),{code:'invalid_input'});assert.equal(calls,0);assert.equal(client.pendingSample,null);
});

test('quality and phase are part of pending identity; missing null stays null',async()=>{
  let body;globalThis.fetch=async(url,options)=>{body=JSON.parse(options.body);throw Error('offline');};const client=makeClient();await assert.rejects(client.sendSample({oil_kgf_cm2:null},{phase:'running',quality:{oil_kgf_cm2:{quality:'missing'}}}));assert.equal(body.values.oil_kgf_cm2,null);
  await assert.rejects(client.sendSample({oil_kgf_cm2:null},{phase:'warming_up',quality:{oil_kgf_cm2:{quality:'missing'}}}),{code:'pending_conflict'});
});


test('web cannot override measured vibration values or their quality', async()=>{
  let calls=0;globalThis.fetch=async()=>{calls++;return response(result());};const client=makeClient();
  await assert.rejects(client.sendSample({vibration_rms_g:0.04}),{code:'sensor_override'});
  await assert.rejects(client.sendSample({envelope_cv:0.12}),{code:'sensor_override'});
  await assert.rejects(client.sendSample({coolant_c:80},{quality:{vibration_rms_g:{quality:'ok'}}}),{code:'sensor_override'});
  assert.equal(calls,0);assert.equal(client.pendingSample,null);
});

test('IMU status preserves live versus recorded source and no values are posted', async()=>{
  let options;globalThis.fetch=async(url,request)=>{options=request;assert.ok(url.endsWith('/api/imu'));return response({state:'fresh',source:'recorded_imu',features:{vibration_rms_g:0.05,envelope_cv:0.14},calibration:{state:'ready'}});};
  const result=await makeClient().getImu();assert.equal(result.source,'recorded_imu');assert.equal(options.method,'GET');assert.equal(options.body,undefined);
});

test('calibration requires explicit normal confirmation and has an exact payload', async()=>{
  const client=makeClient();let calls=0;globalThis.fetch=async(url,options)=>{calls++;assert.ok(url.endsWith('/api/imu/calibration/start'));assert.deepEqual(JSON.parse(options.body),{confirmed_normal:true});return response({calibration:{state:'collecting',count:0,required:15}});};
  assert.throws(()=>client.startCalibration(),{code:'normal_confirmation_required'});await client.startCalibration({confirmedNormal:true});assert.equal(calls,1);
  client._pending={seq:1};assert.throws(()=>client.startCalibration({confirmedNormal:true}),{code:'pending_conflict'});assert.equal(calls,1);
});

test('legacy scenario response is rejected instead of mislabeling as hybrid', async()=>{
  globalThis.fetch=async()=>response(result(1,{schema_version:'genguard.bridge.result/1'}));
  const client=makeClient();await assert.rejects(client.sendSample({coolant_c:80}),{code:'invalid_response'});assert.equal(client.nextSeq,1);assert.ok(client.pendingSample);
});

test('trend experiments use isolated authenticated routes without consuming live sample sequence',async()=>{
  const calls=[];globalThis.fetch=async(url,options)=>{calls.push({url,options});return response(url.endsWith('/catalog')?{scenarios:[]}:{source:'scenario',time_mode:'simulated',rows:[]});};
  const client=makeClient({token:'trend-token'});await client.getTrendCatalog();await client.runTrendExperiment('coolant_rise');
  assert.equal(calls[0].url,'http://127.0.0.1:8765/api/trend/catalog');assert.equal(calls[0].options.method,'GET');
  assert.equal(calls[1].url,'http://127.0.0.1:8765/api/trend/experiment');assert.deepEqual(JSON.parse(calls[1].options.body),{scenario_id:'coolant_rise'});
  assert.equal(calls[1].options.headers.Authorization,'Bearer trend-token');assert.equal(client.nextSeq,1);assert.equal(client.pendingSample,null);
});

test('scenario identifiers cannot supply arbitrary routes or payloads',()=>{
  const client=makeClient();for(const id of ['../samples','x/y','',null,42,{scenario_id:'rise'},'bad id'])assert.throws(()=>client.runTrendExperiment(id),{code:'invalid_scenario'});
});
