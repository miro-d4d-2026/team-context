import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the actual presentation functions with a minimal DOM; no network or model mock is executed.
class Element {
  constructor() { this.children=[]; this.dataset={}; this.textContent=''; this.value=''; this.checked=false; }
  replaceChildren(...items) { this.children=items; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  querySelectorAll() { return []; }
  addEventListener() {}
}
function screen() {
  let now=1800000000000;
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const context=vm.createContext({
    document:{getElementById:get,querySelector:get,createElement:()=>new Element()},
    location:{origin:'http://127.0.0.1:9879'},
    Date:class extends Date {static now(){return now;}},
    Intl,GenGuardClient:class {},createTrendUI:()=>({updateConnection(){},renderLive(){}}),setInterval:()=>0,clearTimeout:()=>{},
  });
  const source=readFileSync(new URL('./demo.mjs',import.meta.url),'utf8').replace("import {GenGuardClient} from './client.mjs';",'').replace("import {createTrendUI} from './trend.mjs';",'');
  vm.runInContext(source,context);
  vm.runInContext("active=true;config={model_id:'test'};",context);
  return {get,context,advance:seconds=>{now+=seconds*1000;},run:code=>vm.runInContext(code,context)};
}
const imu=()=>({state:'fresh',source:'live_imu',stream_id:'sensor-1',installation_id:'fixed-mount',window_id:'sensor-1:15',age_s:0,features:{vibration_rms_g:.05,envelope_cv:.1},calibration:{state:'ready',count:15,required:15,baseline_id:'fixture-baseline'}});
const result=(extra={})=>({status:'normal',seq:1,elapsed_s:30,analysis_age_s:0,duplicate:false,imu:imu(),ai:{state:'within_baseline',summary:'기준 내',components:{},current_comparison:{evidence:[]},checks:[]},fixed:{alerts:[]},notification_events:[],...extra});
function render(s,value) {s.context.response=value;s.run('renderResult(response)');}

test('cached normal response stays old after a fresh IMU poll',()=>{
  const s=screen();render(s,result({duplicate:true,analysis_age_s:30}));
  assert.equal(s.get('status-card').dataset.status,'unknown');assert.equal(s.get('sample-age').textContent,'마지막 응답 30초 전');
  assert.equal(s.get('.imu-card').dataset.state,'stale');
  s.context.currentImu=imu();s.run('renderImu(currentImu);renderAge();');
  assert.equal(s.get('.imu-card').dataset.state,'fresh');assert.equal(s.get('status-card').dataset.status,'unknown');
  assert.match(s.get('status-detail').textContent,/이전 기록/);
});

test('old urgent result is historical, while a fresh urgent result remains urgent',()=>{
  const s=screen();render(s,result({status:'urgent',duplicate:true,analysis_age_s:12}));
  assert.equal(s.get('status-card').dataset.status,'unknown');
  render(s,result({status:'urgent',imu:{...imu(),state:'stale',age_s:9}}));
  assert.equal(s.get('status-card').dataset.status,'urgent');
});

test('sensor age and analysis age are added, never reset on receipt',()=>{
  const s=screen();render(s,result({analysis_age_s:2,imu:{...imu(),age_s:4}}));
  assert.equal(s.get('.imu-card').dataset.state,'stale');assert.equal(s.get('status-card').dataset.status,'unknown');
  assert.match(s.get('imu-detail').textContent,/6초 전/);
});

test('fresh normal result expires after five seconds',()=>{
  const s=screen();render(s,result());assert.equal(s.get('status-card').dataset.status,'normal');
  s.advance(6);s.run('renderAge();');assert.equal(s.get('status-card').dataset.status,'unknown');
});

test('duplicate response without an age fails closed',()=>{
  const s=screen();const value=result({duplicate:true});delete value.analysis_age_s;render(s,value);
  assert.equal(s.get('status-card').dataset.status,'unknown');
});


test('server status detail takes precedence over normal current-model summary',()=>{
  const s=screen();render(s,result({status:'watch',status_detail:'현재 유압이 고정 점검 기준을 벗어났습니다.',ai:{state:'within_baseline',summary:'현재 값은 학습 기준 내입니다.',components:{},current_comparison:{evidence:[]},checks:[]}}));
  assert.equal(s.get('status-card').dataset.status,'watch');assert.equal(s.get('status-detail').textContent,'현재 유압이 고정 점검 기준을 벗어났습니다.');
});
