import test from 'node:test';
import assert from 'node:assert/strict';
import {METRICS,escapeHtml,numberText,signedText,sourceLabel,visualState,buildRequest,scenarioValues,dateLabel,modelCount,modelCaption,retrainMessage,channelFor,historyPoints,chartSvg,staleOverview,buildReplayRequest,replayClassification,replayHistoryPoints,recommendationFallback} from './inspection.mjs';

const values=scenarioValues('normal');
function overview(overrides={}) {
  return {
    current:{id:'current',source:'scenario',inspected_at:'2026-10-11T10:00:00+09:00',values:{...values},sources:Object.fromEntries(Object.keys(values).map(f=>[f,'scenario'])),quality:{}},
    records:[{id:'previous',source:'scenario',inspected_at:'2026-10-01T10:00:00+09:00',values:{...values},confirmed_normal:true}],
    comparison:{state:'normal',reference_id:'previous',channels:[{field:'coolant_c',current:80.2,previous:80,adjusted_delta:.1,state:'normal',source:'scenario',reason:'within_learned_history'}],model:{training_record_count:8}},
    operational_status:{state:'normal',alerts:[]},...overrides,
  };
}

test('hybrid requests never send a browser-controlled vibration value',()=>{
  const request=buildRequest({...values,vibration_rms_g:15},{mode:'hybrid',referenceId:'past-1'});
  assert.equal(request.mode,'hybrid');assert.equal(request.reference_id,'past-1');
  assert.equal('vibration_rms_g' in request.values,false);assert.equal(request.values.coolant_c,values.coolant_c);
});
test('scenario requests explicitly identify all mock inputs and do not mutate input',()=>{
  const before={...values};const request=buildRequest(before);
  assert.equal(request.mode,'scenario');assert.equal(request.reference_id,null);assert.equal(request.values.vibration_rms_g,.046);assert.deepEqual(before,values);
});
test('empty, nonfinite, and unsupported modes cannot be submitted',()=>{
  for(const value of [null,undefined,NaN,Infinity,'80'])assert.throws(()=>buildRequest({...values,coolant_c:value}),/숫자/);
  assert.throws(()=>buildRequest(values,{mode:'real'}),/모드/);
});
test('presets change data only, and preserve comparison context for fault examples',()=>{
  assert.equal(scenarioValues('coolant').coolant_c,87.2);
  assert.equal(scenarioValues('oil').load_pct,62);
  const combined=scenarioValues('combined');assert.ok(combined.coolant_c>values.coolant_c);assert.ok(combined.oil_kgf_cm2<values.oil_kgf_cm2);assert.ok(combined.vibration_rms_g>values.vibration_rms_g);
  assert.equal('state' in combined,false);assert.equal('advisory' in combined,false);
});
test('different-load preset keeps measured values so API must determine comparability',()=>{
  const changed=scenarioValues('load');assert.equal(changed.load_pct,90);assert.equal(changed.coolant_c,values.coolant_c);
});
test('sources distinguish mock, real, replay and unknown without claiming all values live',()=>{
  assert.equal(sourceLabel('scenario'),'모의 값');assert.equal(sourceLabel('live_imu'),'실측 IMU');
  assert.equal(sourceLabel('recorded_imu'),'실측 기록 재생');assert.equal(sourceLabel('live_imu','stale'),'IMU 확인 필요');assert.equal(sourceLabel('unrecognized'),'미수신');
});
test('missing model result does not appear normal',()=>{
  const channel=channelFor(overview(),'oil_kgf_cm2');assert.equal(channel.state,'unavailable');assert.equal(channel.current,3.19);assert.match(channel.reasonText,/기록/);
});
test('fixed urgent protection is visible even if statistical comparison is normal',()=>{
  const data=overview({operational_status:{state:'urgent',alerts:[{field:'coolant_c',severity:'urgent'}]}});
  assert.equal(channelFor(data,'coolant_c').state,'urgent');assert.equal(visualState('urgent'),'urgent');
});
test('stale actual measurements are withheld from numeric cards',()=>{
  const data=overview();data.current.quality.coolant_c='stale';
  const channel=channelFor(data,'coolant_c');assert.equal(channel.current,null);assert.match(channel.reasonText,/유효한/);
});
test('real-vibration context is never described as learned from mock load',()=>{
  const data=overview();data.comparison.channels=[{field:'vibration_rms_g',state:'unavailable',reason:'unverified_sensor_operating_context',source:'live_imu'}];
  assert.match(channelFor(data,'vibration_rms_g').reasonText,/실측 운전 조건/);
});
test('plot refuses to connect synthetic history to real IMU current',()=>{
  const data=overview();data.current.sources.vibration_rms_g='live_imu';data.current.installation_id='A';
  const points=historyPoints(data,'vibration_rms_g');assert.equal(points.length,1);assert.equal(points[0].current,true);
});
test('plot only joins real records from same source and installation',()=>{
  const data=overview();data.current.sources.vibration_rms_g='live_imu';data.current.installation_id='A';
  data.records=[{id:'other-mount',source:'live_imu',installation_id:'B',inspected_at:'2026-10-01',values}, {id:'same-mount',source:'live_imu',installation_id:'A',inspected_at:'2026-10-02',values}];
  assert.deepEqual(historyPoints(data,'vibration_rms_g').map(p=>p.id),['same-mount','current']);
});
test('plots have separate units and accessible equivalent data',()=>{
  const svg=chartSvg(historyPoints(overview(),'coolant_c'),METRICS.coolant_c,'inspect');
  assert.match(svg,/role="img"/);assert.match(svg,/<title>/);assert.match(svg,/°C/);assert.doesNotMatch(svg,/kgf/);
});
test('untrusted labels cannot inject markup through chart or history formatting',()=>{
  assert.equal(escapeHtml('<img src=x onerror="bad">'),'&lt;img src=x onerror=&quot;bad&quot;&gt;');
  const svg=chartSvg([{label:'<script>bad</script>',value:80,current:true}],METRICS.coolant_c);
  assert.doesNotMatch(svg,/<script>/);assert.match(svg,/&lt;script&gt;/);
});
test('null formatting does not masquerade as zero and signed changes remain clear',()=>{
  assert.equal(numberText(null),'—');assert.equal(numberText(NaN),'—');assert.equal(signedText(7,1),'+7.0');assert.equal(signedText(-.7,2),'-0.70');assert.equal(numberText(0,3),'0.000');
});
test('learned inspection count is taken from model metadata',()=>{
  assert.equal(modelCount(overview().comparison),8);assert.equal(modelCount({model:{training_record_ids:['a','b']}}),2);assert.equal(modelCount({}),0);
});
test('invalid dates are disclosed instead of invented',()=>assert.equal(dateLabel({inspected_at:'bad'}),'날짜 미확인'));
test('lost device connection removes current-normal claim and hides stale IMU',()=>{
  const stale=staleOverview(overview());assert.equal(stale.operational_status.state,'unavailable');assert.equal(stale.imu.state,'stale');assert.equal(channelFor(stale,'vibration_rms_g').current,null);assert.equal(stale.comparison.channels[0].adjusted_delta,null);
});
test('lost device connection preserves prior urgent alert as explicitly historical',()=>{
  const data=overview({operational_status:{state:'urgent',alerts:[{field:'coolant_c',severity:'urgent'}]}});
  const stale=staleOverview(data);assert.equal(stale.operational_status.state,'urgent');assert.match(stale.operational_status.headline,/최근 경보/);assert.equal(stale.operational_status.alerts.length,1);
});
test('incompatible reference values are not presented as the previous real vibration',()=>{
  const data=overview();data.comparison.channels=[{field:'vibration_rms_g',current:.04,previous:.047,source:'live_imu',reference_source:'scenario',state:'unavailable',reason:'source_mismatch'}];
  assert.equal(channelFor(data,'vibration_rms_g').previous,null);
});
test('unmatched context does not claim that zero historical records were trained',()=>{
  const unavailable={model:{training_record_count:0}};
  assert.equal(modelCaption(unavailable),'비교 기준 확인 필요');assert.doesNotMatch(retrainMessage(unavailable),/0회/);assert.match(retrainMessage(unavailable),/비교 조건/);
  assert.equal(modelCaption(overview().comparison),'정상 점검 8회 학습');
});
test('explicit mismatch and unconfirmed-reference reasons are readable',()=>{
  for(const [reason,expected] of [['operating_conditions_mismatch',/운전 조건 차이/],['unconfirmed_reference',/정상 확인된/]]){
    const data=overview();data.comparison.channels=[{field:'coolant_c',state:'unavailable',reason}];assert.match(channelFor(data,'coolant_c').reasonText,expected);
  }
});

function replayOverview(label='baseline',state='normal'){
  const data=overview({mode:'replay'});
  data.current.sources.vibration_rms_g='recorded_imu';data.current.values.vibration_rms_g=.039;
  data.comparison.state='unavailable';data.comparison.channels.push({field:'vibration_rms_g',state:'unavailable',source:'recorded_imu',previous:.045,adjusted_delta:.001,reason:'source_mismatch'});
  data.vibration_replay={recording_id:'random',frame_index:0,frame_count:48,prediction:{label,state,title:label==='idle'?'진동 없음':'기준 진동',summary:'서버의 실측 패턴 분석',checks:[]},evaluation:{expected_label:'increased',correct:false}};
  return data;
}
test('replay request includes only recording selection and mock generator values',()=>{
  const request=buildReplayRequest({...values,vibration_rms_g:12,expected_label:'baseline',prediction:'normal'},{recordingId:'random',frameIndex:3,referenceId:'previous'});
  assert.deepEqual(Object.keys(request).sort(),['frame_index','recording_id','reference_id','values']);
  assert.equal('vibration_rms_g' in request.values,false);assert.equal('expected_label' in request.values,false);assert.equal('prediction' in request.values,false);
  assert.equal(request.frame_index,3);assert.equal(request.values.oil_kgf_cm2,3.19);
});
test('replay selection rejects arbitrary files and noninteger frame selection',()=>{
  assert.throws(()=>buildReplayRequest(values,{recordingId:'../secret'}));
  for(const frameIndex of [-1,NaN,Infinity,1.5,'0'])assert.throws(()=>buildReplayRequest(values,{frameIndex}));
});
test('replay displayed prediction is independent of the hidden experiment label',()=>{
  const data=replayOverview();assert.equal(data.vibration_replay.evaluation.expected_label,'increased');
  assert.equal(replayClassification(data).label,'baseline');assert.equal(channelFor(data,'vibration_rms_g').state,'normal');
  assert.equal(channelFor(data,'vibration_rms_g').previous,null);assert.equal(channelFor(data,'vibration_rms_g').adjusted_delta,null);
});
test('idle playback stays neutral rather than becoming a generator fault',()=>{
  const data=replayOverview('idle','idle');data.operational_status.state='unavailable';
  assert.equal(channelFor(data,'vibration_rms_g').state,'idle');assert.equal(recommendationFallback(data).okay,false);assert.match(recommendationFallback(data).title,/정지 여부/);
});
test('normal replay is not contradicted by unavailable generator vibration comparison',()=>{
  const data=replayOverview();assert.equal(data.comparison.state,'unavailable');assert.equal(recommendationFallback(data).okay,true);assert.match(recommendationFallback(data).text,/모의 값/);
});
test('stale replay does not keep a valid normal classification',()=>{
  const data=staleOverview(replayOverview());assert.equal(replayClassification(data).state,'unavailable');assert.equal(channelFor(data,'vibration_rms_g').current,null);
});
test('replay graph shows only inspected windows up to the selected point, never mock history',()=>{
  const frames=[{frame_index:2,start_s:14.5,rms_g:.04},{frame_index:0,start_s:10.5,rms_g:.03},{frame_index:1,start_s:12.5,rms_g:.035},{frame_index:3,start_s:25.5,rms_g:.2}];
  const points=replayHistoryPoints(frames,2);
  assert.deepEqual(points.map(p=>p.value),[.03,.035,.04]);assert.equal(points.at(-1).current,true);assert.equal(frames[0].frame_index,2);
  assert.deepEqual(replayHistoryPoints([],0),[]);
});
