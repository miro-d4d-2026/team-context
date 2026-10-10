import {initLearningWorkspace} from './learning.mjs';

export const FIELDS = {
  coolant_c:{label:'냉각수 온도',unit:'°C',digits:1,short:'수온'},
  oil_kgf_cm2:{label:'윤활유 압력',unit:'kgf/cm²',digits:2,short:'유압'},
  vibration_rms_g:{label:'진동 RMS',unit:'g',digits:3,short:'진동'},
};
export const LINKED_FIELDS={...FIELDS,engine_rpm:{label:'회전수',unit:'rpm',digits:0,short:'회전수'},voltage_ll_v:{label:'출력 전압',unit:'V',digits:1,short:'전압'},load_pct:{label:'부하율',unit:'%',digits:0,short:'부하'}};
export function linkedActive(data){return data?.view?.mode==='hybrid'&&data?.linked_simulation?.enabled===true;}
export function chartFields(data){return linkedActive(data)?LINKED_FIELDS:FIELDS;}
export function elapsedLabel(seconds){if(!Number.isFinite(seconds))return '—';const t=Math.max(0,Math.floor(seconds));return t<60?`${t}초`:`${Math.floor(t/60)}분 ${t%60}초`;}
const PRIMARY=['coolant_c','oil_kgf_cm2','vibration_rms_g','engine_rpm','voltage_ll_v','frequency_hz','load_pct','current_a','battery_v','fuel_pct','ambient_c','humidity_pct'];
const CONTEXT=['load_pct','ambient_c','engine_rpm'];
const AUXILIARY=['voltage_ll_v','battery_v','fuel_pct','humidity_pct','pf'];
const STATUS={normal:'정상 흐름',inspect:'점검 권고',urgent:'즉시 확인',unavailable:'비교 보류'};
const METHODS={trajectory:'운전 구간별 변화',vibration:'진동 크기·급변',vibration_pattern:'시간 패턴',fixed:'정격·참고 범위',fixed_context:'범위·비교 조건',derived:'계산값',context:'비교 조건',calculation_context:'계산 조건',unconnected:'미연결',display:'표시 전용',manual:'담당자 점검',imu_driven_simulation:'진동 연동 모의'};
export function escapeHtml(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
export function numberText(value,digits=1){return typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('ko-KR',{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';}
export function signedText(value,digits=1){return typeof value==='number'&&Number.isFinite(value)?`${value>0?'+':''}${numberText(value,digits)}`:'—';}
export function visualState(value){return ['urgent','critical','danger'].includes(value)?'urgent':['inspect','watch','warning'].includes(value)?'inspect':['normal','stable','ok'].includes(value)?'normal':'unavailable';}
export function pageFor(path){const key=String(path).split('#').pop().replace(/^\//,'').replace(/\/$/,'');return {overview:'dashboard',records:'records',learning:'learning',reports:'comparison',comparison:'comparison',connect:'connections',connections:'connections'}[key]||'dashboard';}
export function workspaceLearning(data,imu){
  const model=data?.analysis?.model||{}, records=data?.records||[], approved=records.filter(r=>r.confirmed_normal), linked=linkedActive(data);
  const cal=imu?.calibration||{}, ready=cal.state==='ready';
  return {approved,ready,baselineId:ready?cal.baseline_id:null,imuCount:cal.count||0,imuRequired:cal.required||15,
    algorithm:model.algorithm||null,modelId:model.id||null,trainingCount:linked?null:(model.training_record_count??model.training_count??null),
    generatorModelInUse:!linked&&Boolean(model.id)&&(model.training_record_count??model.training_count??0)>0&&!data?.analysis?.comparison_blocker,canRetrain:data?.view?.mode==='scenario',linked};
}

export function sourceLabel(source){return {scenario:'모의 값',synthetic:'모의 값',derived:'계산값',imu_driven_simulation:'진동 연동 모의',recorded_imu:'실측 기록',live_imu:'실측 IMU',unconnected:'미연결',missing:'미수신',manual:'미점검'}[source]||'출처 확인';}
export function telemetrySourceLabel(row,data){return linkedActive(data)&&row.source==='derived'?'모의 계산값':sourceLabel(row.source);}
export function activeSamples(data){const limit=data?.view?.selected_elapsed_s;return (data?.current_run?.samples||[]).filter(s=>Number.isFinite(s.elapsed_s)&&(!Number.isFinite(limit)||s.elapsed_s<=limit));}
export function presetSamples(name='normal'){
  if(!['normal','coolant','oil','vibration','combined'].includes(name))throw new Error('운전 시나리오를 확인하세요.');
  const temps=[50,60,68,73,78],pressures=[4,3.6,3.3,3.2,3.2];
  return temps.map((t,i)=>({elapsed_s:i*300,coolant_c:t+((name==='coolant'||name==='combined')&&i>=2?12:0),oil_kgf_cm2:pressures[i]-((name==='oil'||name==='combined')&&i>=2?.9:0),vibration_rms_g:(name==='vibration'||name==='combined')&&i>=2?.09:.045}));
}
export function presetChartField(name,current='coolant_c'){return {normal:'coolant_c',coolant:'coolant_c',oil:'oil_kgf_cm2',vibration:'vibration_rms_g',combined:'coolant_c'}[name]||current;}
export function presetElapsed(name,current=600){return name==='normal'?300:['coolant','oil','vibration','combined'].includes(name)?600:current;}
export function buildRequest(data,{mode=data?.view?.mode||'scenario',samples=data?.current_run?.samples,context=data?.current_run?.context,auxiliary=data?.auxiliary||{},elapsed=data?.view?.selected_elapsed_s||600,referenceId=data?.reference_id||null,recordingId=data?.vibration_replay?.recording_id||'random',frameIndex=data?.vibration_replay?.frame_index||0}={}){
  if(!['scenario','replay','hybrid'].includes(mode))throw new Error('데이터 모드를 확인하세요.');
  if(!Array.isArray(samples)||samples.length<2)throw new Error('시간별 측정값이 필요합니다.');
  const copied=samples.map((s,i)=>{const out={elapsed_s:s.elapsed_s};if(!Number.isFinite(out.elapsed_s)||(i>0&&out.elapsed_s<=samples[i-1].elapsed_s))throw new Error('운전 시간 순서를 확인하세요.');for(const field of Object.keys(FIELDS)){const v=s[field];if(field==='vibration_rms_g'&&mode!=='scenario'){out[field]=null;continue;}if(typeof v!=='number'||!Number.isFinite(v))throw new Error('시간별 계측 값을 숫자로 입력하세요.');out[field]=v;}return out;});
  const ctx={};for(const key of CONTEXT){if(!Number.isFinite(context?.[key]))throw new Error('운전 조건을 숫자로 입력하세요.');ctx[key]=context[key];}
  if(!copied.some(s=>s.elapsed_s===elapsed))throw new Error('관측한 운전 시점을 선택하세요.');
  const aux={};for(const [key,v]of Object.entries(auxiliary)){if(v!==null&&!Number.isFinite(v))throw new Error('보조 계측 값을 숫자로 입력하세요.');aux[key]=v;}
  if(!Number.isInteger(frameIndex)||frameIndex<0||frameIndex>=48)throw new Error('진동 기록 구간을 확인하세요.');
  return {samples:copied,context:ctx,auxiliary:aux,selected_elapsed_s:elapsed,reference_id:referenceId||null,mode,recording_id:recordingId,frame_index:frameIndex};
}
export function channelFor(data,field){return data?.analysis?.channels?.find(c=>c.field===field)||{field,...FIELDS[field],state:'unavailable',intervals:[]};}
export function selectedInterval(channel){return channel?.worst_interval||channel?.intervals?.at(-1)||channel;}
export function intervalLabel(interval){const start=interval?.start_s??interval?.interval?.start_s,end=interval?.end_s??interval?.interval?.end_s;return Number.isFinite(start)&&Number.isFinite(end)?`${numberText(start/60,0)}–${numberText(end/60,0)}분`:'운전 구간';}
export function channelChange(channel){
  const item=selectedInterval(channel),meta=FIELDS[channel.field];
  if(channel.method==='imu_driven_simulation')return channel.summary||channel.reason||'진동에 맞춰 변화하는 모의 값';
  if(channel.method==='recorded_pattern_classifier'||channel.method==='installation_baseline')return channel.summary||channel.reason||'실측 진동 기준 확인';
  if(channel.field==='vibration_rms_g')return Number.isFinite(item?.change_ratio)?`평소 진동의 ${numberText(item.change_ratio,2)}배`:'같은 부착 조건 비교 필요';
  const delta=item?.observed_delta??item?.current_change,previous=item?.previous_change;
  if(Number.isFinite(delta))return `${intervalLabel(item)} ${signedText(delta,meta.digits)} ${meta.unit}${Number.isFinite(previous)?` · 이전 ${signedText(previous,meta.digits)}`:''}`;
  return '비교 가능한 운전 구간 필요';
}
export function timeSeries(data,field){
  if(field==='vibration_rms_g'&&data?.view?.mode!=='scenario')return {current:[],previous:[],sourceMismatch:true};
  if(linkedActive(data)){const limit=data.linked_simulation.elapsed_s;return {current:(data.linked_simulation.history||[]).filter(s=>Number.isFinite(s.elapsed_s)&&Number.isFinite(s[field])&&s.valid!==false&&(!Number.isFinite(limit)||s.elapsed_s<=limit)).map(s=>({time:s.elapsed_s,value:s[field]})),previous:[],sourceMismatch:false};}
  const limit=data?.view?.selected_elapsed_s;
  const clean=samples=>(samples||[]).filter(s=>Number.isFinite(s.elapsed_s)&&Number.isFinite(s[field])&&(!Number.isFinite(limit)||s.elapsed_s<=limit)).map(s=>({time:s.elapsed_s,value:s[field]}));
  return {current:clean(activeSamples(data)),previous:clean(data?.analysis?.reference_run?.samples),sourceMismatch:false};
}
export function playbackStep(data){const times=(data?.current_run?.samples||[]).map(s=>s.elapsed_s).filter(t=>Number.isFinite(t)&&t>=300).sort((a,b)=>a-b);return times.find(t=>t>data?.view?.selected_elapsed_s)??null;}
export function sparklineSvg(data,field){
  if(linkedActive(data)&&(data.linked_simulation.history||[]).some(p=>p.valid===false||!Number.isFinite(p[field])))return '';
  const series=timeSeries(data,field),all=[...series.current,...series.previous];if(series.sourceMismatch||all.length<2)return '';
  const meta=chartFields(data)[field],lo=Math.min(...all.map(p=>p.value)),span=Math.max(10**-meta.digits,Math.max(...all.map(p=>p.value))-lo),minT=linkedActive(data)?all[0].time:0,maxT=Math.max(minT+1,linkedActive(data)?0:300,...all.map(p=>p.time));
  const points=arr=>arr.map(p=>`${2+(p.time-minT)/(maxT-minT)*64},${22-(p.value-lo)/span*18}`).join(' ');
  const color=visualState(channelFor(data,field).state)==='inspect'?'#b98730':'#258a80';
  return `<svg class="metric-spark" viewBox="0 0 68 26" aria-hidden="true"><polyline points="${points(series.previous)}" fill="none" stroke="#b2c3d0" stroke-width="1.4" stroke-dasharray="3 2"/><polyline points="${points(series.current)}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}
export function linkedChartSvg(data,field){
  const meta=LINKED_FIELDS[field];if(!meta||field==='vibration_rms_g')return '';
  const limit=data?.linked_simulation?.elapsed_s,raw=(data?.linked_simulation?.history||[]).filter(p=>Number.isFinite(p.elapsed_s)&&(!Number.isFinite(limit)||p.elapsed_s<=limit));
  const valid=p=>p.valid!==false&&Number.isFinite(p[field]),points=raw.filter(valid);
  if(!points.length)return '<div class="chart-empty">진동이 수신되면 모의 운전 추이가 쌓입니다.</div>';
  const W=650,H=170,L=49,R=61,T=23,B=33,loT=raw[0].elapsed_s,hiT=Math.max(loT+2,raw.at(-1).elapsed_s),min=Math.min(...points.map(p=>p[field])),max=Math.max(...points.map(p=>p[field])),pad=Math.max((max-min)*.2,10**-meta.digits*3),lo=Math.max(0,min-pad),hi=max+pad;
  const x=t=>L+(t-loT)/(hiT-loT)*(W-L-R),y=v=>T+(hi-v)/(hi-lo)*(H-T-B);let svg=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(meta.label)} 진동 연동 모의 추이"><title>${escapeHtml(meta.label)} · 진동 연동 모의 · 실제 계측 아님</title>`;
  for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;svg+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e7eef4"/><text x="${L-9}" y="${y(v)+4}" text-anchor="end" fill="#8196a8" font-size="11">${numberText(v,meta.digits)}</text>`;}
  const segments=[];let segment=[];for(const p of raw){if(valid(p))segment.push(p);else if(segment.length){segments.push(segment);segment=[];}}if(segment.length)segments.push(segment);
  for(const part of segments){svg+=part.length>1?`<polyline points="${part.map(p=>`${x(p.elapsed_s)},${y(p[field])}`).join(' ')}" fill="none" stroke="#258a80" stroke-width="2.5"/>`:`<circle cx="${x(part[0].elapsed_s)}" cy="${y(part[0][field])}" r="2.5" fill="#258a80"/>`;}
  const last=points.at(-1);svg+=`<circle cx="${x(last.elapsed_s)}" cy="${y(last[field])}" r="4" fill="#258a80"/><text x="${x(last.elapsed_s)+8}" y="${y(last[field])+3}" fill="#258a80" font-size="13">${numberText(last[field],meta.digits)}</text>`;
  for(let i=0;i<4;i++){const t=loT+(hiT-loT)*i/3;svg+=`<text x="${x(t)}" y="${H-8}" fill="#8196a8" font-size="10" text-anchor="middle">${escapeHtml(elapsedLabel(t))}</text>`;}
  for(const p of points)svg+=`<circle data-point-time="${p.elapsed_s}" class="chart-hit" cx="${x(p.elapsed_s)}" cy="${y(p[field])}" r="8" fill="transparent"><title>${elapsedLabel(p.elapsed_s)} · 모의 ${numberText(p[field],meta.digits)} ${escapeHtml(meta.unit)}</title></circle>`;
  return svg+`<text x="${L}" y="12" fill="#8ba0b1" font-size="10">${escapeHtml(meta.unit)} · 모의 값</text></svg>`;
}
export function pointEvidence(data,field,time){const series=timeSeries(data,field),current=series.current.find(p=>p.time===time),previous=series.previous.find(p=>p.time===time),meta=chartFields(data)[field];if(!current)return null;return {time:time/60,current:current.value,previous:previous?.value??null,delta:Number.isFinite(previous?.value)?current.value-previous.value:null,unit:meta.unit};}
export function chartSvg(data,field){
  const meta=FIELDS[field],series=timeSeries(data,field);
  if(series.sourceMismatch)return '<div class="chart-empty">실측 진동은 같은 부착 상태의 진동 모델로 분석합니다.<br>모의 발전기 시간 곡선과 연결하지 않습니다.</div>';
  const all=[...series.current,...series.previous];if(!all.length)return '<div class="chart-empty">비교할 측정 기록이 없습니다.</div>';
  const W=650,H=170,L=49,R=61,T=28,B=33,values=all.map(p=>p.value),span=Math.max(...values)-Math.min(...values),pad=Math.max(span*.20,10**-meta.digits*3),lo=Math.max(0,Math.min(...values)-pad),hi=Math.max(...values)+pad,maxT=Math.max(300,...all.map(p=>p.time));
  const x=t=>L+t/maxT*(W-L-R),y=v=>T+(hi-v)/(hi-lo)*(H-T-B),channel=channelFor(data,field),color=visualState(channel.state)==='inspect'?'#b38942':'#258a80';
  let out=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${meta.label} 운전 시간별 비교"><title>${escapeHtml(meta.label)}: ${series.current.map(p=>`${p.time/60}분 ${numberText(p.value,meta.digits)}${meta.unit}`).join(', ')}</title>`;
  for(let i=0;i<4;i++){const value=lo+(hi-lo)*i/3;out+=`<line x1="${L}" x2="${W-R}" y1="${y(value)}" y2="${y(value)}" stroke="#e7eef4"/><text x="${L-9}" y="${y(value)+4}" text-anchor="end" fill="#8196a8" font-size="11">${numberText(value,meta.digits)}</text>`;}
  const worst=channel.worst_interval?.interval||channel.worst_interval;
  if(worst){const from=Math.max(0,worst.start_s),until=Math.min(maxT,worst.end_s);if(until>from)out+=`<rect x="${x(from)}" y="${T}" width="${x(until)-x(from)}" height="${H-T-B}" fill="#e7be6b" opacity=".14"/>`;}
  const line=(points,stroke,dash='')=>points.length?`<polyline fill="none" stroke="${stroke}" stroke-width="2.5" ${dash?`stroke-dasharray="${dash}"`:''} points="${points.map(p=>`${x(p.time)},${y(p.value)}`).join(' ')}"/>`+points.map(p=>`<circle cx="${x(p.time)}" cy="${y(p.value)}" r="3.2" fill="${stroke}" stroke="white" stroke-width="1.4"/>`).join(''):'';
  out+=line(series.previous,'#a8bbcd','5 4')+line(series.current,color);
  const last=series.current.at(-1),prev=series.previous.at(-1);if(last){out+=`<text x="${x(last.time)+9}" y="${y(last.value)-3}" fill="${color}" font-size="13" font-weight="650">${numberText(last.value,meta.digits)}</text>`;if(prev&&Math.abs(y(last.value)-y(prev.value))>18)out+=`<text x="${x(prev.time)+9}" y="${y(prev.value)+4}" fill="#90a6b9" font-size="11">${numberText(prev.value,meta.digits)}</text>`;}
  if(field!=='vibration_rms_g')for(let i=1;i<series.current.length;i++){const p=series.current[i],before=series.current[i-1],cx=(x(p.time)+x(before.time))/2,cy=Math.max(T+6,(y(p.value)+y(before.value))/2-9);out+=`<text x="${cx}" y="${cy}" fill="${color}" font-size="10" text-anchor="middle">${signedText(p.value-before.value,meta.digits)}${meta.unit==='°C'?'°':''}</text>`;}
  for(const t of [...new Set([0,...all.map(p=>p.time)])].sort((a,b)=>a-b))out+=`<text x="${x(t)}" y="${H-9}" text-anchor="middle" fill="#8196a8" font-size="11">${numberText(t/60,0)}분</text>`;
  for(const p of series.current){const e=pointEvidence(data,field,p.time);out+=`<circle class="chart-hit" data-point-time="${p.time}" cx="${x(p.time)}" cy="${y(p.value)}" r="12" fill="transparent" tabindex="0" role="img" aria-label="${e.time}분 이번 ${numberText(e.current,meta.digits)}${meta.unit}, 이전 ${numberText(e.previous,meta.digits)}${meta.unit}"><title>${e.time}분 · 이번 ${numberText(e.current,meta.digits)} / 이전 ${numberText(e.previous,meta.digits)} ${meta.unit}</title></circle>`;}
  return out+`<text x="${L}" y="12" fill="#8ba0b1" font-size="10">${escapeHtml(meta.unit)}</text></svg>`;
}
export function imuPresentation(imu,receivedAt=Date.now(),now=Date.now()){
  const age=Number.isFinite(imu?.age_s)?imu.age_s+Math.max(0,(now-receivedAt)/1000):null;
  const state=imu?.state==='fresh'&&(age===null||age>5)?'stale':imu?.state||'waiting';
  return {state,age,fresh:state==='fresh',rms:Number.isFinite(imu?.features?.vibration_rms_g)?imu.features.vibration_rms_g:null,calibration:imu?.calibration||{state:'untrained',count:0,required:15}};
}
export function calibrationMessage(calibration){
  if(calibration?.state==='collecting')return '기준 진동과 부착 상태를 유지하세요.';
  if(calibration?.state==='ready')return '이제 진동을 바꾸면 등록한 기준과 비교합니다.';
  if(calibration?.state==='failed'){
    const error=String(calibration.error||'');
    if(/changed from training|unchanged normal vibration/i.test(error))return '수집 중 진동이 달라졌습니다. 같은 기준 진동을 유지한 뒤 다시 등록하세요.';
    if(/minimum|too low|near.zero|rms.*below/i.test(error))return '기준 진동이 너무 작습니다. 아이폰에서 기준 진동을 켜고 다시 등록하세요.';
    return /[가-힣]/.test(error)?error:'센서 입력과 진동 상태를 확인한 뒤 다시 등록하세요.';
  }
  return '센서를 고정하고 아이폰에서 기준 진동을 켜주세요.';
}
export function imuTraceSvg(payload){
  const raw=(payload?.points||[]).filter(p=>Number.isFinite(p.timestamp_s)),points=raw.filter(p=>p.valid!==false&&Number.isFinite(p.rms_g));
  if(!points.length)return '<div class="chart-empty">아이폰의 진동을 켜면 센서의 실제 수신값이 이곳에 쌓입니다.</div>';
  const W=650,H=170,L=49,R=61,T=23,B=33,minT=raw[0].timestamp_s,maxT=Math.max(minT+2,raw.at(-1).timestamp_s),maxV=Math.max(.001,...points.map(p=>p.rms_g),Number.isFinite(payload?.baseline?.rms_g)?payload.baseline.rms_g:0)*1.18;
  const x=t=>L+(t-minT)/(maxT-minT)*(W-L-R),y=v=>T+(1-v/maxV)*(H-T-B);let svg=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="실측 진동 RMS 최근 수신 이력"><title>실측 IMU 진동 크기 추이</title>`;
  for(let i=0;i<4;i++){const v=maxV*i/3;svg+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e7eef4"/><text x="${L-9}" y="${y(v)+4}" text-anchor="end" fill="#8196a8" font-size="11">${numberText(v,3)}</text>`;}
  const segments=[];let segment=[];for(const p of raw){if(p.valid!==false&&Number.isFinite(p.rms_g)){segment.push(p);}else if(segment.length){segments.push(segment);segment=[];}}if(segment.length)segments.push(segment);for(const part of segments)svg+=part.length>1?`<polyline points="${part.map(p=>`${x(p.timestamp_s)},${y(p.rms_g)}`).join(' ')}" fill="none" stroke="#258a80" stroke-width="2.5"/>`:`<circle cx="${x(part[0].timestamp_s)}" cy="${y(part[0].rms_g)}" r="2" fill="#258a80"/>`;if(payload?.baseline?.ready&&Number.isFinite(payload.baseline.rms_g))svg+=`<line x1="${L}" x2="${W-R}" y1="${y(payload.baseline.rms_g)}" y2="${y(payload.baseline.rms_g)}" stroke="#9aaabd" stroke-dasharray="4 4"/><text x="${L+5}" y="${y(payload.baseline.rms_g)-5}" fill="#8297a9" font-size="10">등록 기준 ${numberText(payload.baseline.rms_g,3)}g</text>`;const last=points.at(-1);svg+=`<circle cx="${x(last.timestamp_s)}" cy="${y(last.rms_g)}" r="4" fill="#258a80"/><text x="${x(last.timestamp_s)+8}" y="${y(last.rms_g)+3}" fill="#258a80" font-size="13">${numberText(last.rms_g,3)}</text>`;
  for(let i=0;i<4;i++){const t=minT+(maxT-minT)*i/3;svg+=`<text x="${x(t)}" y="${H-8}" fill="#8196a8" font-size="10" text-anchor="middle">${numberText(t-maxT,0)}초</text>`;}
  return svg+'<text x="49" y="12" fill="#8ba0b1" font-size="10">RMS · g</text></svg>';
}
export function dateLabel(record){const raw=record?.inspected_at;if(!raw)return '날짜 미확인';const date=new Date(raw);return Number.isFinite(date.getTime())?date.toLocaleDateString('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit'}).replace(/\.$/,''):'날짜 미확인';}
export function telemetryNote(row,data){
  if(linkedActive(data)&&['imu_driven_simulation','derived'].includes(row.source)){
    if(data.linked_simulation.phase==='signal_wait')return '갱신 중단 · 마지막 모의 값';
    if(['signal_lost','waiting'].includes(data.linked_simulation.phase))return row.note||'IMU 수신 대기 · 모의 운전 보류';
    const brief={engine_rpm:'시동 후 회전수 안정화',voltage_ll_v:'시동·정지에 맞춘 모의 출력',frequency_hz:'모의 회전수에서 계산',load_pct:'시동 후 부하 연결',current_a:'모의 출력에서 계산',battery_v:'정지 12.6V · 운전 14.0V',fuel_pct:'모의 잔량',ambient_c:'모의 주변 온도',humidity_pct:'모의 환경값'};
    return brief[row.key]||row.note||'실측 진동에 연동한 모의 값';
  }
  if(row.analysis)return channelChange(row.analysis);
  const map={engine_rpm:'정격 1,800 rpm',voltage_ll_v:'선간 전압 · 기준 208 V',frequency_hz:'RPM ÷ 30 · 4극 가정',load_pct:`출력 ${numberText(data.telemetry?.find(r=>r.key==='active_power_kw')?.value,1)} kW`,current_a:'균형 3상 환산',battery_v:'12 V 계통 가정',fuel_pct:'잔량 20% 미만 점검',ambient_c:'운전 비교 조건',humidity_pct:'환경 참고 · 학습 제외'};
  return map[row.key]||row.method||'';
}
export function intervalRows(channel){return (channel.intervals||[]).map(item=>{const meta=FIELDS[channel.field],isVibration=channel.field==='vibration_rms_g';return {time:intervalLabel(item),previous:isVibration?`${numberText(item.previous,3)} g`:`${signedText(item.previous_change,meta.digits)} ${meta.unit}`,current:isVibration?`${numberText(item.current,3)} g`:`${signedText(item.observed_delta??item.current_change,meta.digits)} ${meta.unit}`,expected:isVibration?(Number.isFinite(item.change_ratio)?`평소의 ${numberText(item.change_ratio,2)}배`:'—'):`${signedText(item.expected_rate,channel.field==='oil_kgf_cm2'?3:2)} ${channel.rate_unit||meta.unit+'/분'}`,state:visualState(item.state)};});}
export function staleData(data){const urgent=visualState(data.operational_status?.state)==='urgent';return {...data,linked_simulation:data.linked_simulation?{...data.linked_simulation,phase:'signal_wait',phase_label:'연결 확인',message:'마지막 수신 당시의 모의 값입니다. 새 센서 데이터를 기다립니다.'}:undefined,operational_status:{...data.operational_status,state:urgent?'urgent':'unavailable',headline:urgent?'최근 경보 · 분석 장치 연결 확인':'분석 장치의 연결을 확인하세요',summary:'새 결과를 받지 못했습니다. 아래 값은 마지막 수신 기록입니다.'},telemetry:(data.telemetry||[]).map(r=>({...r,state:visualState(r.state)==='urgent'?'urgent':'unavailable'})),analysis:{...data.analysis,state:'unavailable',recommendations:[],channels:(data.analysis?.channels||[]).map(c=>({...c,state:'unavailable',summary:'갱신이 중단된 이전 분석입니다. 현재 상태를 판단하지 않습니다.'}))}};}

if(typeof document!=='undefined')boot();
function boot(){
  const $=id=>document.getElementById(id);let page=pageFor(location.hash||location.pathname);
  const state={data:null,busy:false,token:'',field:'coolant_c',editorPreset:null,pendingSave:null,lastStatus:null,playing:false,timer:null,speed:1,operationPlaying:false,operationTimer:null,imu:null,imuReceivedAt:0,imuHistory:null,imuBusy:false,lastOperationalState:null,lastCalibrationState:null};
  initLearningWorkspace({request:async(path,body,options={})=>{
    const headers={Accept:'application/json'};
    if(body!==undefined)headers['Content-Type']='application/json';
    if(state.token)headers.Authorization=`Bearer ${state.token}`;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,
        body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',credentials:'omit',signal:controller.signal});
      if(response.ok&&options.raw)return {text:await response.text(),contentType:response.headers.get('Content-Type')};
      const result=await response.json();
      if(!response.ok)throw new Error(result.error?.message||'학습 기록 요청을 처리하지 못했습니다.');
      return result;
    }finally{clearTimeout(timer);}
  }});
  const pages={dashboard:['운전 현황','운전 현황','현재 상태와 학습한 기준을 확인합니다.','MONITORING'],records:['기록 · 정상 검토','운전 기록 검토','정상·비정상과 확인 근거를 남기고 학습할 기록을 선택합니다.','RECORDS'],learning:['학습 센터','ML 학습 센터','검토한 기록으로 학습하고 검증한 모델을 다음 운전에 적용합니다.','LEARNING'],comparison:['분석 보고서','운전별 분석 보고서','비상 운전·예방점검의 분석 결과와 후속 조치를 누적합니다.','REPORT'],connections:['센서 · 입력 연결','센서 · 입력 연결','수신 상태를 확인하고 운전 기록을 시작합니다.','CONNECTION']};
  const pagePaths={dashboard:'/#overview',records:'/#records',learning:'/#learning',comparison:'/#reports',connections:'/#connect'};
  function showPage(path,{push=false}={}){page=pageFor(path);document.body.dataset.page=page;if(push&&location.pathname+location.hash!==pagePaths[page])history.pushState({},'',pagePaths[page]);for(const p of Object.keys(pages))$(`${p}-page`).hidden=p!==page;document.querySelectorAll('[data-page]').forEach(a=>{const active=a.dataset.page===page;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});$('page-title').textContent=pages[page][1];$('breadcrumb').textContent=pages[page][0];$('page-description').textContent=pages[page][2];$('page-eyebrow').textContent=pages[page][3]+' / '+pages[page][0];document.title=`GenGuard · ${pages[page][0]}`;if(state.data){renderWorkspace();pollImu();}}
  showPage(location.hash||location.pathname);window.addEventListener('popstate',()=>{stopPlayback();showPage(location.hash||location.pathname);});window.addEventListener('hashchange',()=>showPage(location.hash||location.pathname));
  function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,4000);}
  async function request(path,body){
    if(state.busy)return null;state.busy=true;state.lastStatus=null;document.body.setAttribute('aria-busy','true');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{const headers={Accept:'application/json'};if(body!==undefined)headers['Content-Type']='application/json';if(state.token)headers.Authorization=`Bearer ${state.token}`;
      const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',credentials:'omit',signal:controller.signal});state.lastStatus=response.status;const result=await response.json();if(!response.ok)throw new Error(response.status===401?'연결 설정에서 인증 토큰을 확인하세요.':result.error?.message||result.message||'입력값을 확인하고 다시 시도하세요.');$('error').hidden=true;$('connection-state').textContent='분석 장치 연결됨';return result;
    }catch(error){const text=error.name==='AbortError'?'분석 응답이 지연되고 있습니다. 다시 시도하세요.':error.message==='Failed to fetch'?'분석 장치에 연결하지 못했습니다.':error.message;$('error').textContent=text;$('error').hidden=false;toast(text);if(state.lastStatus===null||state.lastStatus>=500){stopPlayback();$('connection-state').textContent='분석 장치 연결 확인';if(state.data)render(staleData(state.data),false);}return null;
    }finally{clearTimeout(timer);state.busy=false;document.body.removeAttribute('aria-busy');}
  }
  function render(data,syncEditor=true){
    if(!data?.current_run||!data?.analysis)return;if(state.data?.view.mode!=='hybrid'&&data.view.mode==='hybrid')state.field='vibration_rms_g';state.data=data;const status=data.operational_status||data.analysis,kind=visualState(status.state),mode=data.view.mode,linked=linkedActive(data),simulation=data.linked_simulation;
    $('equipment-id').textContent=data.equipment?.id||'GEN-10K-001';$('status-equipment').textContent=`${data.equipment?.id||'GEN-10K-001'} · ${linked?'연동 '+elapsedLabel(simulation.elapsed_s):'운전 '+numberText(data.view.selected_elapsed_s/60,0)+'분'}`;
    $('status-banner').className=`status-banner ${kind}`;$('status-symbol').textContent=kind==='normal'?'✓':kind==='unavailable'?'○':'!';$('status-chip').textContent=STATUS[kind];$('status-chip').className=`badge ${kind}`;$('status-title').textContent=status.headline||'운전 상태 확인';$('status-summary').textContent=status.summary||'';$('status-summary').title=status.summary||'';
    $('execution-host').textContent=data.execution?.label||'';$('execution-host').hidden=!data.execution?.label;
    $('data-source').textContent=mode==='replay'?'실측 기록 + 모의 값':mode==='hybrid'?'실측 IMU + 모의 값':'모의 운전';$('source-note').textContent=mode==='replay'?'진동은 실측 기록 · 수온·유압은 모의 값':mode==='hybrid'?'진동은 IMU · 수온·유압은 모의 값':'계측 값은 모의 값입니다.';$('footer-source').textContent=mode==='scenario'?'모의 기준 · 장비 보호 설정과 별개':'실측 진동과 모의 운전은 각각 분석';
    document.querySelectorAll('[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===mode)));$('elapsed-select').value=String(data.view.selected_elapsed_s);$('elapsed-select').disabled=linked;$('elapsed-select').closest('label').hidden=linked;$('settings-open').disabled=linked;$('settings-open').title=linked?'진동 연동 모의를 끄면 직접 값을 바꿀 수 있습니다.':'';$('reference-select').disabled=linked;
    $('scenario-controls').hidden=mode!=='scenario';$('dashboard-actions').hidden=false;$('imu-panel').hidden=mode!=='hybrid';document.body.dataset.mode=mode;const currentIndex=activeSamples(data).filter(s=>s.elapsed_s>=300).length;$('operation-progress').innerHTML=(data.current_run.samples||[]).filter(s=>s.elapsed_s>=300).map((s,i)=>`<span class="${i<currentIndex?'passed':''}">${s.elapsed_s/60}분</span>`).join('');if(state.lastOperationalState&&state.lastOperationalState!==kind)$('recent-change').textContent=`최근 변화 · ${linked?'연동 '+elapsedLabel(simulation.elapsed_s):'운전 '+numberText(data.view.selected_elapsed_s/60,0)+'분'} · ${STATUS[kind]}`;state.lastOperationalState=kind;
    const context=data.current_run.context||{},displayContext=linked?{...context,...simulation.values}:context;$('current-context').textContent=`부하 ${numberText(displayContext.load_pct,0)}% · ${numberText(displayContext.engine_rpm,0)} rpm · 주변 ${numberText(displayContext.ambient_c,1)}°C${linked?' · 모의':''}`;
    renderMetrics(data);
    const channels=data.analysis.channels||[];$('comparison-channel-cards').innerHTML=channels.map(c=>`<article class="channel-card ${visualState(c.state)}"><h3>${escapeHtml(c.label)}<span class="badge ${visualState(c.state)}">${STATUS[visualState(c.state)]}</span></h3><strong>${escapeHtml(channelChange(c))}</strong><p>${escapeHtml(c.summary||c.reason||'기록 확인 필요')}</p></article>`).join('');
    const checks=[...(data.analysis.recommendations||[]),...(status.alerts||[]).map(a=>({title:a.title,reason:a.reason,checks:a.checks}))];$('dashboard-checks').innerHTML=checks.length?checks.slice(0,2).map((c,i)=>`<div class="action-item"><span class="action-number">${i+1}</span><div><h3>${escapeHtml(c.title)}</h3><p>${escapeHtml((c.checks||[]).slice(0,2).join(' · ')||c.reason)}</p></div></div>`).join(''):`<div class="all-clear">${kind==='normal'?'새로운 점검 항목이 없습니다':'비교 조건을 확인하세요'}<small>${kind==='normal'?'평소와 같은 운전 흐름입니다.':'측정값이 없거나 조건이 다른 항목은 판단을 보류합니다.'}</small></div>`;
    $('dashboard-change-summary').innerHTML=channels.slice(0,3).map(c=>`<div><span>${escapeHtml(FIELDS[c.field]?.short||c.label)}</span><b>${escapeHtml(channelChange(c))}</b></div>`).join('');
    const records=data.records||[],selected=data.reference_id;$('reference-select').innerHTML='<option value="">가장 가까운 정상 점검</option>'+records.filter(r=>r.confirmed_normal).map(r=>`<option value="${escapeHtml(r.id)}">${escapeHtml(dateLabel(r))} · ${escapeHtml(r.label||'정기 시험 운전')}</option>`).join('');$('reference-select').value=selected||'';
    const conditions=data.analysis.conditions;$('comparison-context').textContent=linked?'진동 연동 모의 추이 · 이전 점검 비교와 학습은 보류':conditions?.matched?'부하·외기온·회전수·시동 온도를 맞춰 비교':'같은 운전 조건의 정상 기록이 필요합니다.';
    $('record-count').textContent=`${records.length}회`;$('normal-count').textContent=`${data.analysis.model?.training_record_count??data.analysis.model?.training_count??0}회`;$('record-current-state').textContent=STATUS[kind];
    $('records-body').innerHTML=[...records].reverse().map(r=>{const latest=(r.samples||[]).at(-1)||{},ctx=r.context||{};return `<tr><td><strong>${escapeHtml(dateLabel(r))}</strong><small>${escapeHtml(r.label||'정기 시험 운전')}</small></td><td>${numberText(ctx.load_pct,0)}% · ${numberText(ctx.engine_rpm,0)} rpm<small>주변 ${numberText(ctx.ambient_c,1)}°C · ${escapeHtml(sourceLabel(r.source))}</small></td><td>${numberText(latest.elapsed_s/60,0)}분</td><td>${numberText(latest.coolant_c,1)} °C</td><td>${numberText(latest.oil_kgf_cm2,2)}</td><td>${numberText(latest.vibration_rms_g,3)} g</td><td><span class="badge ${r.confirmed_normal?'normal':'unavailable'}">${r.confirmed_normal?'정상 확인':'관찰 기록'}</span></td><td>${r.confirmed_normal?`<button class="table-action" type="button" data-reference="${escapeHtml(r.id)}">비교 보기</button>`:'—'}</td></tr>`;}).join('');
    $('catalog-body').innerHTML=(data.telemetry||[]).map(row=>`<tr><td><strong>${escapeHtml(row.label)}</strong><small>${escapeHtml(row.section)}</small></td><td>${row.value===null||row.value===undefined?escapeHtml(row.source_label||'미연결'):typeof row.value==='number'?`${numberText(row.value,row.decimals)} ${escapeHtml(row.unit)}`:escapeHtml(typeof row.value==='boolean'?(row.value?'켜짐':'꺼짐'):row.value)}<small>${escapeHtml(telemetrySourceLabel(row,data))}</small></td><td>${escapeHtml(METHODS[row.method]||row.method||'기록 표시')}<small>${row.learning?'모델 비교':'모델 학습 제외'}</small></td><td class="catalog-note">${escapeHtml(row.note)}</td></tr>`).join('');
    $('save-open').disabled=mode!=='scenario';$('retrain').disabled=mode!=='scenario';$('save-policy').textContent=mode==='scenario'?'정상 확인한 기록만 학습합니다. 선택한 시점까지의 관측값만 저장합니다.':'실측 진동과 모의 운전을 하나의 발전기 점검으로 저장하거나 학습하지 않습니다.';
    $('analysis-detail').innerHTML=`<p><strong>항목별 비교</strong> · 수온은 구간별 상승 속도, 유압은 예열에 따른 변화와 해당 시점의 압력, 진동은 평소 크기와 급격한 변화를 각각 분석합니다.</p><p><strong>정상 기록 ${numberText(data.analysis.model?.training_record_count??data.analysis.model?.training_count,0)}회 학습</strong> · 부하·주변 온도·회전수·시동 온도를 반영합니다. 현재 운전과 아직 관측하지 않은 미래 시점은 학습에서 제외합니다.</p><p>${escapeHtml(data.scope||'')}</p><p>정격·보호 참고값은 별도 규칙으로 확인합니다. 시연 값은 실제 장비의 보호 설정을 대신하지 않습니다.</p>`;
    if(linked)$('analysis-detail').innerHTML='<p><strong>실측 진동</strong>은 같은 부착 상태에서 등록한 정상 기준과 비교합니다.</p><p><strong>수온·유압·회전수·전압·부하율</strong>은 실측 진동에 맞춰 동작하는 모의 운전 값입니다. 실제 발전기 계측이나 고장 예측 결과가 아니며 이전 점검 비교와 정상 학습에는 사용하지 않습니다.</p>';
    renderCharts();renderReplay(data);renderImu();renderLinked();renderWorkspace();if(syncEditor)fillEditor(data.current_run.samples,context,data.auxiliary||{});
  }
  function metricCard(row,data){return `<article title="${escapeHtml(row.note||'')}" data-metric="${escapeHtml(row.key)}" class="telemetry ${['info','unchecked'].includes(row.state)?'info':visualState(row.state)}"><div class="metric-top"><span class="metric-label">${escapeHtml(row.label.replace(' · 중력 제거','').replace(' · 선간','').replace(' · 균형 가정',''))}</span><span class="metric-source">${escapeHtml(telemetrySourceLabel(row,data))}</span></div><div class="metric-number"><div class="metric-value">${numberText(row.value,row.decimals??1)}<small>${escapeHtml(row.unit)}</small></div>${chartFields(data)[row.key]?sparklineSvg(data,row.key):''}</div><p class="metric-note" title="${escapeHtml(telemetryNote(row,data))}">${escapeHtml(telemetryNote(row,data))}</p></article>`;}
  function renderAttached(data){
    const row=data.telemetry?.find(r=>r.key==='vibration_rms_g');if(!row)return;
    const live=data.view.mode==='hybrid',info=imuPresentation(state.imu,state.imuReceivedAt),base=live&&info.calibration.state==='ready'?state.imuHistory?.baseline?.rms_g:null;
    const cv=live?(info.fresh?state.imu?.features?.envelope_cv:null):data.telemetry?.find(r=>r.key==='envelope_cv')?.value;
    const rms=live?{...row,value:info.fresh?info.rms:null}:row;
    const source=live?(state.imu?.source?sourceLabel(state.imu.source):'수신 대기'):data.view.mode==='replay'?'실측 기록':'모의 진동';
    $('attached-source').textContent=source;$('attached-source').className=`badge ${live&&info.fresh?'normal':'info'}`;
    $('attached-metrics').innerHTML=metricCard(rms,data)+`<article class="telemetry info"><div class="metric-top"><span class="metric-label">정상 기준 RMS</span><span class="metric-source">${base!=null?'학습 기준':'기준 미표시'}</span></div><div class="metric-value">${numberText(base,4)}<small>g</small></div><p class="metric-note">${base!=null?'같은 부착 상태의 정상 진동':'실시간 IMU 연결에서 확인'}</p></article><article class="telemetry info"><div class="metric-top"><span class="metric-label">시간 변동성</span><span class="metric-source">${cv!=null?source:'미수신'}</span></div><div class="metric-value">${numberText(cv,3)}<small>CV</small></div><p class="metric-note">진동의 끊김·반복 변화</p></article>`;
    $('attached-note').textContent=live?`중력 제거 · 2초 분석 구간 · ${info.fresh?numberText(state.imu?.sample_rate_hz,0)+' Hz 수집':'센서 수신 확인'} · ${info.calibration.state==='ready'?'정상 기준 유지':'정상 기준 확인 필요'}`:data.view.mode==='replay'?'저장된 아이폰·IMU 실험 기록 · 현재 측정과 구분':'진동 시나리오 값 · 실제 센서 입력은 IMU 연결에서 확인';
  }
  function renderMetrics(data){
    const rows=PRIMARY.map(k=>data.telemetry?.find(row=>row.key===k)).filter(Boolean), major=['engine_rpm','coolant_c','oil_kgf_cm2','voltage_ll_v'];
    renderAttached(data);$('controller-metrics').innerHTML=major.map(k=>rows.find(r=>r.key===k)).filter(Boolean).map(r=>metricCard(r,data)).join('');
    $('auxiliary-metrics').innerHTML=rows.filter(r=>!major.includes(r.key)&&r.key!=='vibration_rms_g').map(r=>metricCard(r,data)).join('');
    $('controller-source').textContent=linkedActive(data)?'진동 연동 모의':'모의 계측';
  }
  function facts(items){return items.map(([name,value])=>`<div><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`).join('');}
  function renderWorkspace(){
    const data=state.data;if(!data)return;const w=workspaceLearning(data,state.imu),live=data.view.mode==='hybrid',info=imuPresentation(state.imu,state.imuReceivedAt);
    $('learning-imu-state').textContent=w.ready?'정상 기준 유지':info.calibration.state==='collecting'?'수집 중':'기준 미등록';$('learning-imu-state').className=`badge ${w.ready?'normal':'unavailable'}`;
    $('learning-imu-facts').innerHTML=facts([['학습 특징','진동 RMS · 시간 변동성'],['수집 구간',`${w.imuCount} / ${w.imuRequired}`],['기준 ID',w.baselineId||'미등록'],['현재 수신',info.fresh?'실측 수신 중':'수신 상태 확인']]);
    $('learning-imu-note').textContent=w.ready?'현재 부착 상태에서 학습한 정상 기준입니다. 센서를 다시 부착하면 기준을 새로 등록하세요.':'정상 진동을 유지한 상태에서 센서 · 입력 연결 메뉴의 기준 등록을 실행하세요.';
    $('learning-operation-facts').innerHTML=facts([['학습 대상','수온 상승 속도 · 유압 변화 · 진동'],['정상 확인 기록',`${w.approved.length}회`],['현재 모델',w.algorithm||'진동 연동 모의에서는 적용 보류'],['이번 비교의 학습 기록',w.trainingCount==null?'적용 보류':`${w.trainingCount}회`]]);
    $('learning-operation-note').textContent=w.canRetrain?'정상으로 확인한 과거 모의 기록으로 다시 학습합니다. 지금 운전 중인 기록과 미래값은 이번 판단의 학습 자료에서 제외합니다.':'모의 운전 모드에서 기록 모델을 학습할 수 있습니다. 실측 진동과 연동 모의 계측을 하나의 정상 발전기 기록으로 학습하지 않습니다.';
    $('learning-records').innerHTML=w.approved.map(r=>`<tr><td><strong>${escapeHtml(dateLabel(r))}</strong><small>${escapeHtml(r.label)}</small></td><td>${numberText(r.context?.load_pct,0)}% · ${numberText(r.context?.engine_rpm,0)} rpm</td><td>${escapeHtml(sourceLabel(r.source))}</td><td>같은 장비 · 같은 운전 조건</td></tr>`).join('')||'<tr><td colspan="4">정상으로 확인한 운전 기록이 없습니다.</td></tr>';
    $('connection-host').textContent=data.execution?.label||'실행 위치 확인';$('pipeline-host').textContent=data.execution?.label||'분석 장치';
    $('connection-facts').innerHTML=facts([['실제 수집','진동 RMS · 시간 변동성'],['입력 출처',state.imu?.source?sourceLabel(state.imu.source):'미수신'],['부착 ID',state.imu?.installation_id||'미수신'],['수신 상태',info.fresh?'최신 센서값 수신 중':info.state==='stale'?'수신 중단 · 연결 확인':'센서값 확인 필요'],['분석 위치',data.execution?.label||'확인 중'],['발전기 제어반','미연결 · 모의 계측 사용']]);
  }
  function renderCharts(){
    if(!state.data)return;const data=state.data,linked=linkedActive(data),fields=chartFields(data);if(!fields[state.field])state.field='coolant_c';
    const channel=channelFor(data,state.field),meta=fields[state.field],liveTrace=data.view.mode==='hybrid'&&state.field==='vibration_rms_g';
    for(const id of ['dashboard-channel-tabs','comparison-tabs'])$(id).innerHTML=Object.entries(fields).map(([field,m])=>`<button type="button" data-chart="${field}" aria-pressed="${field===state.field}">${escapeHtml(m.label)}</button>`).join('');
    const title=liveTrace?'실측 진동 크기 추이':linked?'진동 연동 모의 추이':'이번 운전의 변화';$('dashboard-chart-title').textContent=title;
    const chart=liveTrace?imuTraceSvg(state.imuHistory):linked?linkedChartSvg(data,state.field):chartSvg(data,state.field);
    $('dashboard-chart').innerHTML=chart;$('comparison-chart').innerHTML=chart;renderChartEvidence();
    for(const id of ['dashboard-chart','comparison-chart'])$(id).nextElementSibling.nextElementSibling.hidden=liveTrace||linked;
    $('comparison-chart-title').textContent=liveTrace?'실측 진동 크기 추이 · 최근 수신':linked?`${meta.label} · 진동 연동 모의 추이`:`운전 시간에 따른 ${meta.short} 변화`;
    $('comparison-chart-subtitle').textContent=linked&&!liveTrace?'실측 진동으로 운전 상황을 바꾼 모의 값입니다. 이전 발전기 기록과의 비교는 보류합니다.':channel.summary||channel.reason||'';
    $('interval-body').closest('.table-scroll').hidden=linked;
    $('interval-caption').textContent=state.field==='vibration_rms_g'?'같은 구간의 진동 크기 비교':'같은 운전 구간의 변화량 비교';
    $('interval-body').innerHTML=intervalRows(channel).map(row=>`<tr><td>${row.time}</td><td>${row.previous}</td><td><strong>${row.current}</strong></td><td>${row.expected}</td><td><span class="badge ${row.state}">${STATUS[row.state]}</span></td></tr>`).join('')||'<tr><td colspan="5">비교할 구간이 없습니다. 센서 출처와 측정 조건을 확인하세요.</td></tr>';
    const headers=$('interval-body').closest('table').querySelectorAll('thead th');headers[1].textContent=state.field==='vibration_rms_g'?'이전 진동':'이전 변화';headers[2].textContent=state.field==='vibration_rms_g'?'이번 진동':'이번 변화';headers[3].textContent=state.field==='vibration_rms_g'?'학습 기준 대비':'모델이 학습한 변화율';
  }
  function renderLinked(){
    const data=state.data,sim=data?.linked_simulation,active=linkedActive(data);$('linked-simulation-toggle').checked=active;$('linked-simulation-toggle').disabled=state.busy||data?.view.mode!=='hybrid';
    $('linked-state').hidden=!active;$('linked-phase').textContent=sim?.phase_label||'진동 수신 대기';
    $('linked-phase').className=`badge ${['running','warming','starting'].includes(sim?.phase)?'normal':['irregular','vibration_increase'].includes(sim?.phase)?'inspect':'unavailable'}`;
    $('linked-elapsed').textContent=elapsedLabel(sim?.elapsed_s);$('linked-message').textContent=sim?.message||'아이폰 진동에 맞춰 모의 운전 상태가 바뀝니다.';
    $('linked-note').textContent=active?(sim?.time_scale_note||'수온·유압·전압·회전수는 시연을 위한 모의 값입니다.'):'켜면 진동에 맞춰 시동·가동·냉각을 표현합니다.';
    if(active){$('data-source').textContent='실측 IMU + 진동 연동 모의';$('source-note').textContent='진동은 실측 · 그 외 계측은 진동에 연동한 모의 값';$('footer-source').textContent='시동·가열·냉각은 모의 · 실제 발전기 계측 아님';}
  }
  function renderReplay(data){const replay=data.vibration_replay;$('replay-panel').hidden=data.view.mode!=='replay';if(!replay)return;$('replay-recording').value=replay.recording_id;$('replay-result').textContent=replay.prediction?.title||'진동 분석';$('replay-reason').textContent=replay.prediction?.summary||'';$('replay-position').textContent=`${replay.frame_index+1} / ${replay.frame_count} 구간 · 기록 ${numberText(replay.start_s,1)}–${numberText(replay.end_s,1)}초`;$('replay-previous').disabled=replay.frame_index<=0;$('replay-next').disabled=replay.frame_index>=replay.frame_count-1;}
  function fillEditor(samples,context,aux){const mode=state.data?.view.mode||'scenario';$('sample-inputs').innerHTML=samples.map((s,i)=>`<tr class="${s.elapsed_s>state.data?.view.selected_elapsed_s?'future':s.elapsed_s===state.data?.view.selected_elapsed_s?'current-row':''}"><td>${numberText(s.elapsed_s/60,0)}분${s.elapsed_s>state.data?.view.selected_elapsed_s?' · 이후':''}<input type="hidden" name="time_${i}" value="${s.elapsed_s}"></td>${Object.entries(FIELDS).map(([field,m])=>`<td><input aria-label="${numberText(s.elapsed_s/60,0)}분 ${m.label}" name="${field}_${i}" type="number" step="${field==='coolant_c'?'.1':field==='oil_kgf_cm2'?'.01':'.001'}" value="${Number.isFinite(s[field])?s[field]:''}" min="${field==='coolant_c'?-30:0}" max="${field==='coolant_c'?150:field==='oil_kgf_cm2'?10:16}" ${field==='vibration_rms_g'&&mode!=='scenario'?'disabled':'required'}></td>`).join('')}</tr>`).join('');for(const key of CONTEXT)$('settings-form').elements.namedItem(key).value=context[key];for(const key of AUXILIARY)$('settings-form').elements.namedItem(key).value=aux[key]??({voltage_ll_v:208,battery_v:13.8,fuel_pct:70,humidity_pct:47.7,pf:.8})[key];$('vibration-edit-note').textContent=mode==='scenario'?'선택한 운전 시점 이후의 값은 아직 관측하지 않은 값으로 처리합니다.':'진동은 실측 값만 사용합니다. 모의 시간별 진동은 입력하지 않습니다.';document.querySelector('[data-preset="vibration"]').disabled=mode!=='scenario';}
  async function evaluate(options={}){let body;try{body=buildRequest(state.data,options);}catch(error){toast(error.message);return null;}const result=await request('/api/operation/evaluate',body);if(result)render(result);return result;}
  function stopPlayback(){state.operationPlaying=false;clearTimeout(state.operationTimer);$('operation-play').textContent='▶ 변화 재생';state.playing=false;clearTimeout(state.timer);$('replay-play').textContent='▶ 기록 재생';}
  async function advance(){if(!state.playing||!state.data?.vibration_replay)return;const replay=state.data.vibration_replay;if(replay.frame_index>=replay.frame_count-1){stopPlayback();return;}if(state.busy){state.timer=setTimeout(advance,200);return;}const result=await evaluate({frameIndex:replay.frame_index+1});if(!result){stopPlayback();return;}if(state.playing)state.timer=setTimeout(advance,2000/state.speed);}
  $('settings-open').onclick=()=>{stopPlayback();state.editorPreset=null;$('settings-dialog').showModal();};$('save-open').onclick=()=>$('save-dialog').showModal();$('connection-open').onclick=()=>$('connection-dialog').showModal();document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
  document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=async()=>{stopPlayback();if(!state.data||state.busy)return;const mode=button.dataset.mode,samples=state.data.current_run.samples.map((s,i)=>({...s,vibration_rms_g:mode==='scenario'?(Number.isFinite(s.vibration_rms_g)?s.vibration_rms_g:presetSamples()[i]?.vibration_rms_g??.045):null}));const result=await evaluate({mode,samples});if(result&&mode==='hybrid'){state.field='vibration_rms_g';renderCharts();pollImu();}if(result&&mode==='replay'&&page!=='comparison')toast('실측 진동을 분석합니다. 기록 재생은 분석 보고서에서 실행하세요.');});
  $('elapsed-select').onchange=async()=>{stopPlayback();const previous=state.data?.view.selected_elapsed_s;const result=await evaluate({elapsed:Number($('elapsed-select').value)});if(!result)$('elapsed-select').value=String(previous);};
  $('reference-select').onchange=async()=>{stopPlayback();const result=await evaluate({referenceId:$('reference-select').value||null});if(!result)$('reference-select').value=state.data.reference_id||'';};
  document.body.addEventListener('click',async event=>{const link=event.target.closest('a[href]');if(link&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey&&event.button===0&&['/','/comparison','/records','/#overview','/#records','/#learning','/#reports','/#connect'].includes(link.getAttribute('href'))){event.preventDefault();stopPlayback();showPage(link.getAttribute('href'),{push:true});window.scrollTo(0,0);}const chart=event.target.closest('[data-chart]');if(chart){state.field=chart.dataset.chart;renderCharts();}const ref=event.target.closest('[data-reference]');if(ref){const result=await evaluate({referenceId:ref.dataset.reference});if(result){showPage('/comparison',{push:true});window.scrollTo(0,0);}}const preset=event.target.closest('[data-preset]');if(preset){state.editorPreset=preset.dataset.preset;fillEditor(presetSamples(preset.dataset.preset),{load_pct:62,ambient_c:28.5,engine_rpm:1800},state.data.auxiliary);}});
  $('settings-form').onsubmit=async event=>{event.preventDefault();stopPlayback();const form=event.currentTarget,number=name=>{const value=form.elements.namedItem(name).value;return value.trim()===''?NaN:Number(value);},mode=state.data.view.mode,samples=[...$('sample-inputs').querySelectorAll('tr')].map((_,i)=>({elapsed_s:number(`time_${i}`),...Object.fromEntries(Object.keys(FIELDS).map(field=>[field,field==='vibration_rms_g'&&mode!=='scenario'?null:number(`${field}_${i}`)]))}));const selectedPreset=state.editorPreset;const result=await evaluate({samples,elapsed:presetElapsed(selectedPreset,state.data.view.selected_elapsed_s),context:Object.fromEntries(CONTEXT.map(k=>[k,number(k)])),auxiliary:{...state.data.auxiliary,...Object.fromEntries(AUXILIARY.map(k=>[k,number(k)]))}});if(result){state.field=presetChartField(selectedPreset,state.field);state.editorPreset=null;renderCharts();$('scenario-select').value='custom';$('settings-dialog').close();}};
  $('save-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,payload={label:form.elements.label.value.trim()||'정기 시험 운전',confirmed_normal:form.elements.confirmed_normal.checked},signature=JSON.stringify(payload);if(!state.pendingSave||state.pendingSave.signature!==signature)state.pendingSave={signature,body:{...payload,request_id:crypto.randomUUID()}};const result=await request('/api/operation/save',state.pendingSave.body);if(result){state.pendingSave=null;render(result);$('save-dialog').close();form.reset();toast('선택한 시점까지의 점검을 저장했습니다.');}else if(state.lastStatus>=400&&state.lastStatus<500)state.pendingSave=null;};
  $('retrain').onclick=async()=>{const result=await request('/api/operation/retrain',{});if(result){render(result);toast(`정상 기록 ${result.analysis.model?.training_record_count??result.analysis.model?.training_count??0}회로 비교 기준을 갱신했습니다.`);}};
  $('connection-form').onsubmit=async event=>{event.preventDefault();state.token=event.currentTarget.elements.token.value.trim();const result=await request('/api/operation/overview');if(result){render(result);$('connection-dialog').close();}};
  $('refresh').onclick=async()=>{stopPlayback();const result=await request('/api/operation/overview');if(result)render(result);};
  $('replay-recording').onchange=async()=>{stopPlayback();const result=await evaluate({recordingId:$('replay-recording').value,frameIndex:0});if(!result)$('replay-recording').value=state.data.vibration_replay?.recording_id||'random';};
  $('replay-previous').onclick=()=>{stopPlayback();evaluate({frameIndex:Math.max(0,state.data.vibration_replay.frame_index-1)});};$('replay-next').onclick=()=>{stopPlayback();evaluate({frameIndex:Math.min(47,state.data.vibration_replay.frame_index+1)});};
  $('replay-play').onclick=async()=>{if(state.playing){stopPlayback();return;}if(state.data?.vibration_replay?.frame_index>=47&&!await evaluate({frameIndex:0}))return;state.playing=true;$('replay-play').textContent='Ⅱ 일시 정지';state.timer=setTimeout(advance,2000/state.speed);};$('replay-speed').onchange=()=>{state.speed=Number($('replay-speed').value);};
  function renderChartEvidence(time){
    if(!state.data)return;const data=state.data,linked=linkedActive(data),c=channelFor(data,state.field),meta=chartFields(data)[state.field];if(!meta)return;
    time??=linked?data.linked_simulation.history?.at(-1)?.elapsed_s:data.view?.selected_elapsed_s;
    const e=pointEvidence(data,state.field,time);let html=e?`<span>${e.time}분</span><span>이번 <b>${numberText(e.current,meta.digits)} ${escapeHtml(e.unit)}</b></span><span>이전 <b>${numberText(e.previous,meta.digits)} ${escapeHtml(e.unit)}</b></span><span>차이 <b>${signedText(e.delta,meta.digits)} ${escapeHtml(e.unit)}</b></span>`:`<span>${escapeHtml(c.summary||'실측 센서 데이터를 기다리고 있습니다.')}</span>`;
    if(linked&&state.field!=='vibration_rms_g')html=e?`<span>${elapsedLabel(time)}</span><span>모의 <b>${numberText(e.current,meta.digits)} ${escapeHtml(e.unit)}</b></span><span>실측 진동에 연동 · 실제 계측 아님</span>`:'<span>모의 운전 기록을 기다리고 있습니다.</span>';
    if(data.view.mode==='hybrid'&&state.field==='vibration_rms_g'){const info=imuPresentation(state.imu,state.imuReceivedAt),baseline=state.imuHistory?.baseline;html=`<span>${info.fresh?'현재 실측':'마지막 수신'}</span><span>RMS <b>${numberText(info.rms,4)} g</b></span><span>정상 기준 <b>${numberText(baseline?.ready?baseline.rms_g:null,4)} g</b></span>`;}
    for(const id of ['dashboard-chart-evidence','comparison-chart-evidence'])$(id).innerHTML=html;
  }
  for(const id of ['dashboard-chart','comparison-chart']){const node=$(id);node.addEventListener('pointerover',event=>{const point=event.target.closest('[data-point-time]');if(point)renderChartEvidence(Number(point.dataset.pointTime));});node.addEventListener('focusin',event=>{const point=event.target.closest('[data-point-time]');if(point)renderChartEvidence(Number(point.dataset.pointTime));});node.addEventListener('pointerleave',()=>renderChartEvidence());}
  async function nextOperation(){if(!state.operationPlaying)return;const next=playbackStep(state.data);if(next===null){stopPlayback();return;}if(state.busy){state.operationTimer=setTimeout(nextOperation,200);return;}const result=await evaluate({elapsed:next});if(!result){stopPlayback();return;}if(playbackStep(state.data)===null){stopPlayback();return;}if(state.operationPlaying)state.operationTimer=setTimeout(nextOperation,2000);}
  $('operation-play').onclick=async()=>{if(state.operationPlaying){stopPlayback();return;}stopPlayback();if(playbackStep(state.data)===null&&!await evaluate({elapsed:300}))return;state.operationPlaying=true;$('operation-play').textContent='Ⅱ 일시 정지';state.operationTimer=setTimeout(nextOperation,2000);};
  $('operation-reset').onclick=async()=>{stopPlayback();await evaluate({elapsed:300});};
  $('scenario-select').onchange=async()=>{stopPlayback();const name=$('scenario-select').value;if(name==='custom')return;const result=await evaluate({samples:presetSamples(name),elapsed:presetElapsed(name,state.data.view.selected_elapsed_s)});if(result){state.field=presetChartField(name,state.field);renderCharts();}else $('scenario-select').value='custom';};
  function renderImu(){if(!$('imu-panel'))return;const info=imuPresentation(state.imu,state.imuReceivedAt),cal=info.calibration;if(cal.state!==state.lastCalibrationState){$('imu-calibration-details').open=cal.state!=='ready';state.lastCalibrationState=cal.state;}const names={fresh:'실측 수신 중',stale:'수신 중단',invalid:'센서 품질 확인',waiting:'수신 대기'};$('imu-badge').textContent=info.fresh&&state.imu?.source==='recorded_imu'?'실측 기록 수신':names[info.state]||'수신 확인';if(state.data?.view.mode==='hybrid'&&!linkedActive(state.data)){$('data-source').textContent=state.imu?.source==='recorded_imu'?'실측 기록 수신 + 모의 값':'실측 IMU + 모의 값';$('source-note').textContent=state.imu?.source==='recorded_imu'?'진동은 과거 실측 기록 수신 · 수온·유압은 모의 값':'진동은 IMU · 수온·유압은 모의 값';}$('imu-badge').className=`badge ${info.fresh?'normal':'unavailable'}`;$('imu-rms').innerHTML=`${numberText(info.rms,4)}<small>g</small>`;if(state.data?.view.mode==='hybrid'){const metric=document.querySelector('[data-metric="vibration_rms_g"] .metric-value');if(metric)metric.innerHTML=`${numberText(info.fresh?info.rms:null,3)}<small>g</small>`;}const baseline=state.imuHistory?.baseline?.ready?state.imuHistory.baseline.rms_g:null;$('imu-ratio').textContent=info.fresh&&Number.isFinite(info.rms)&&Number.isFinite(baseline)&&baseline>0?`${numberText(info.rms/baseline,2)}배`:'—';$('imu-received').textContent=state.imu?.measured_at?`센서 창 ${numberText(info.age,1)}초 전 · ${numberText(state.imu.sample_rate_hz,0)} Hz${info.fresh?'':' · 마지막 수신값'}`:'아직 데이터가 없습니다. Arduino 수신기를 실행하세요.';$('imu-calibration-state').textContent={untrained:'기준 미등록',collecting:'정상 기준 수집 중',ready:'정상 기준 등록됨',failed:'기준 수집 실패'}[cal.state]||'기준 확인';$('imu-progress-text').textContent=`${cal.count??0} / ${cal.required??15}`;$('imu-calibration-progress').max=cal.required||15;$('imu-calibration-progress').value=cal.count||0;$('imu-calibrate').disabled=!info.fresh||cal.state==='collecting'||!$('imu-normal-confirmation').checked||state.busy;$('imu-normal-confirmation').disabled=cal.state==='collecting';$('imu-calibration-note').textContent=calibrationMessage(cal);$('imu-calibration-note').title=cal.state==='failed'?String(cal.error||''):'';}
  async function pollImu(){if(state.imuBusy||document.hidden||(!['learning','connections'].includes(page)&&state.data?.view.mode!=='hybrid'))return;state.imuBusy=true;try{const headers=state.token?{Authorization:`Bearer ${state.token}`}:{},controller=new AbortController(),timer=setTimeout(()=>controller.abort(),4500);try{const [sensorResponse,historyResponse]=await Promise.all([fetch('/api/imu',{headers,cache:'no-store',signal:controller.signal}),fetch('/api/imu/history',{headers,cache:'no-store',signal:controller.signal})]);if(!sensorResponse.ok)throw new Error('센서 수신 확인');state.imu=await sensorResponse.json();state.imuReceivedAt=Date.now();if(historyResponse.ok){const history=await historyResponse.json();state.imuHistory=history.stream_id===state.imu.stream_id&&history.installation_id===state.imu.installation_id&&history.source===state.imu.source?history:null;}else state.imuHistory=null;renderImu();renderWorkspace();if(state.data)renderAttached(state.data);if(state.field==='vibration_rms_g')renderCharts();}finally{clearTimeout(timer);}}catch{if(state.imu)state.imu={...state.imu,state:'stale'};renderImu();renderWorkspace();if(state.data)renderAttached(state.data);}finally{state.imuBusy=false;}}
  $('linked-simulation-toggle').onchange=async()=>{stopPlayback();const result=await request('/api/operation/linked-simulation',{enabled:$('linked-simulation-toggle').checked});if(result)render(result);else renderLinked();};
  $('imu-normal-confirmation').onchange=renderImu;$('imu-calibrate').onclick=async()=>{if(!$('imu-normal-confirmation').checked)return;const result=await request('/api/imu/calibration/start',{confirmed_normal:true});if(result){state.imu=result;state.imuReceivedAt=Date.now();renderImu();toast('정상 진동 수집을 시작했습니다. 30초 동안 같은 진동을 유지하세요.');}};
  setInterval(()=>{if(state.data?.view.mode==='hybrid'||['learning','connections'].includes(page)){renderImu();pollImu();}},1000);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlayback();});
  setInterval(async()=>{if(state.data?.view.mode!=='hybrid'||state.busy||document.hidden||document.querySelector('dialog[open]'))return;const result=await request('/api/operation/overview');if(result)render(result,false);},2000);
  request('/api/operation/overview').then(result=>{if(result)render(result);});
}
