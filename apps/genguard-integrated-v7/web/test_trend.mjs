import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {validateExperiment,projectChart,visibleMilestones,comparisonText,clockLabel,createTrendUI,evaluationText} from './trend.mjs';

const experiment=()=>({source:'scenario',time_mode:'simulated',rows:[0,60,120,180,240].map((t,i)=>({t_s:t,values:{coolant_c:80+i*2,oil_kgf_cm2:3.2-i*.1},trend:{state:i<2?'collecting':'deteriorating',source:'scenario',lookback_s:120,horizon_s:120,advisory:i>=3,channels:{coolant_c:{current:80+i*2,predicted:84+i*2,lower:83+i*2,upper:85+i*2,unit:'°C',limit:92}}},current_anomaly:{persistent_change:i>=2},fixed_warning:i>=4}))});

test('experiments reject real-data provenance and malformed clocks',()=>{
  const value=experiment();assert.equal(validateExperiment(value),value);
  assert.throws(()=>validateExperiment({...value,source:'live_imu'}));assert.throws(()=>validateExperiment({...value,time_mode:'wall_clock'}));
  const duplicate=experiment();duplicate.rows[2].t_s=60;assert.throws(()=>validateExperiment(duplicate));
  const unsourced=experiment();delete unsourced.rows[2].trend.source;assert.throws(()=>validateExperiment(unsourced));
});

test('chart never reveals future observations, even when response has the entire run',()=>{
  const value=experiment();value.rows[3].values.coolant_c=999;
  const plot=projectChart(value,2,'coolant_c');assert.deepEqual(plot.observed.map(p=>p.t),[0,60,120]);assert.ok(!plot.observed.some(p=>p.value===999));
  assert.equal(plot.prediction[0].t,120);assert.equal(plot.prediction[1].t,240);assert.equal(plot.prediction[1].value,88);assert.equal(plot.band.length,3);
});

test('collecting or missing forecast never invents an extrapolation',()=>{
  const value=experiment();assert.deepEqual(projectChart(value,1,'coolant_c').prediction,[]);
  delete value.rows[2].trend.channels.coolant_c.predicted;assert.deepEqual(projectChart(value,2,'coolant_c').prediction,[]);
  assert.deepEqual(projectChart(value,2,'oil_kgf_cm2').band,[]);assert.throws(()=>projectChart(value,2,'vibration_rms_g'));
});

test('future comparison milestones remain hidden until their playback times',()=>{
  const value=experiment();assert.deepEqual(visibleMilestones(value,2),{current:120,trend:null,fixed:null});
  assert.equal(comparisonText(value,2).complete,false);assert.ok(!comparisonText(value,2).summary.includes('60초'));
});

test('comparison acknowledges when current anomaly wins rather than claiming prediction superiority',()=>{
  const comparison=comparisonText(experiment(),4);assert.equal(comparison.complete,true);
  assert.match(comparison.summary,/현재 이상 탐지가 추세 권고보다 60초 먼저/);assert.match(comparison.summary,/보조 정보/);assert.match(comparison.summary,/고정 경고보다는 60초 앞섰/);
});

test('no warning is represented by a dash rather than zero or guaranteed normal',()=>{
  const value=experiment();for(const row of value.rows){row.trend.advisory=false;row.current_anomaly.persistent_change=false;row.fixed_warning=false;}
  assert.deepEqual(visibleMilestones(value,4),{current:null,trend:null,fixed:null});assert.equal(clockLabel(null),'—');assert.equal(clockLabel(0),'0:00');assert.match(comparisonText(value,4).summary,/발생하지 않았/);
});


const originalDocument=globalThis.document;
afterEach(()=>{globalThis.document=originalDocument;});
class Element {
  constructor(){this.children=[];this.dataset={};this.textContent='';this.value='';}
  addEventListener(){} replaceChildren(...items){this.children=items;} append(...items){this.children.push(...items);}
}
test('stale live trend is visibly historical and missing forecast is not filled',()=>{
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  globalThis.document={createElement:()=>new Element()};const ui=createTrendUI({get});
  const trend={state:'deteriorating',summary:'유압이 지속 하락합니다.',history_count:15,lookback_s:120,horizon_s:120,checks:['윤활계통 점검'],channels:{oil_kgf_cm2:{current:2.7,predicted:2.1,unit:'kgf/cm²'}}};
  ui.renderLive(trend,{stale:true,age_s:20});assert.equal(get('live-trend-card').dataset.state,'unavailable');assert.match(get('live-trend-state').textContent,/이전 추세 기록/);assert.match(get('live-trend-values').children[0].textContent,/이전 추정/);assert.match(get('live-trend-checks').children[0].textContent,/이전 권고/);
  ui.renderLive({...trend,advisory:false});assert.equal(get('live-trend-state').textContent,'악화 방향 관찰');
  ui.renderLive({...trend,advisory:true});assert.equal(get('live-trend-state').textContent,'악화 흐름 · 조기 점검');
  ui.renderLive(null);assert.equal(get('live-trend-values').children.length,0);assert.equal(get('live-trend-checks').children.length,0);assert.equal(get('live-trend-state').textContent,'이력 수집 대기');
});


test('completed comparison displays sampling caveat without leaking full evaluation earlier',()=>{
  const value={...experiment(),comparison:{note:'10초 공통 표본: 실사용 2초 분석보다 최대 16초 늦을 수 있습니다.'},evaluation:{summary:'합성 검증 자료입니다.'}};
  assert.match(evaluationText(value,true),/10초 공통 표본/);assert.match(evaluationText(value,true),/16초/);assert.ok(!evaluationText(value,false).includes('16초'));
});

test('evaluation renders the selected non-ML method and false advisory counts from response',()=>{
  const value={...experiment(),evaluation:{selected_method:{coolant_c:'linear',oil_kgf_cm2:'linear'},mae:{ridge:{coolant_c:1.1958,oil_kgf_cm2:.1101},linear:{coolant_c:1.1712,oil_kgf_cm2:.1096}},advisory_summary:{transient:{runs:20,runs_with_false_advisory:14}},current_anomaly_comparison:{worsening_runs:60,current_earlier_runs:60}}};
  const text=evaluationText(value,true);assert.match(text,/단순 선형 추세/);assert.match(text,/20회 중 14회/);assert.match(text,/60회 중 60회/);assert.match(text,/0.1096/);assert.match(text,/기존 상태 경고와 알림에는 반영하지 않습니다/);
  value.evaluation.advisory_summary.transient.runs_with_false_advisory=3;assert.match(evaluationText(value,true),/20회 중 3회/);assert.ok(!evaluationText(value,false).includes('14회'));
});
