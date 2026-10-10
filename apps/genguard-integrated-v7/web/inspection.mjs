const FIELDS = ['coolant_c','oil_kgf_cm2','vibration_rms_g','load_pct','ambient_c','engine_rpm','elapsed_s'];
export const METRICS = {
  coolant_c:{label:'냉각수 온도',unit:'°C',digits:1,icon:'<path d="M9 13.5V5a3 3 0 0 1 6 0v8.5a5 5 0 1 1-6 0Z"/><path d="M12 7v10"/><circle cx="12" cy="18" r="1"/>'},
  oil_kgf_cm2:{label:'윤활유 압력',unit:'kgf/cm²',digits:2,icon:'<path d="M12 2C9 6 5 10 5 14a7 7 0 0 0 14 0c0-4-4-8-7-12Z"/><path d="M8 14a4 4 0 0 0 4 4"/>'},
  vibration_rms_g:{label:'진동 RMS',unit:'g',digits:3,icon:'<path d="m2 12 4 0 2-7 4 15 3-17 3 13 2-4h2"/>'},
};
const REASONS = {
  operating_conditions_mismatch:'운전 조건 차이가 커 비교를 보류합니다.', unconfirmed_reference:'정상 확인된 점검 기록을 선택해 주세요.',
  invalid_operating_context:'운전 단계와 부하·주변 온도·회전수를 확인해 주세요.', invalid_or_stale_current_value:'유효한 최신 측정값을 기다립니다.', invalid_reference_value:'이전 기록의 측정값을 확인해 주세요.', insufficient_confirmed_normal_history:'정상으로 확인한 점검 기록이 더 필요합니다.',
  comparable_history_required:'비교할 정상 기록이 필요합니다.', source_mismatch:'같은 출처의 기록이 필요합니다.', installation_mismatch:'같은 부착 위치의 기록이 필요합니다.', acquisition_mismatch:'같은 측정 설정의 기록이 필요합니다.', acquisition_invalid:'센서의 측정 설정을 확인해 주세요.', outside_learned_conditions:'학습한 운전 조건을 벗어났습니다.', condition_mismatch:'운전 조건 차이가 커 비교를 보류합니다.', context_mismatch:'운전 조건 차이가 커 비교를 보류합니다.', condition_adjusted_change:'운전 조건을 보정해도 차이가 있습니다.', within_learned_history:'정상 기록의 변동 범위 안입니다.', missing_or_invalid:'측정값을 확인해 주세요.', invalid_current:'현재 측정값을 확인해 주세요.', insufficient_history:'정상 점검 기록이 더 필요합니다.', invalid_quality:'유효한 센서값을 기다립니다.', phase_mismatch:'같은 운전 단계의 기록이 필요합니다.', unverified_sensor_operating_context:'실측 운전 조건과 이전 진동 기록이 필요합니다.',
};
export function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
export function numberText(value,digits=1) { return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('ko-KR',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—'; }
export function signedText(value,digits=1) { return typeof value === 'number' && Number.isFinite(value) ? `${value>0?'+':''}${numberText(value,digits)}` : '—'; }
export function sourceLabel(source,quality) {
  if (['scenario','synthetic','mock','demo'].includes(source)) return '모의 값';
  if(source==='recorded_imu') return '실측 기록 재생';
  if(source==='live_imu') return ['stale','missing','invalid'].includes(quality) ? 'IMU 확인 필요' : '실측 IMU';
  if(['controller','measured_controller'].includes(source))return '장비 계측';
  return '미수신';
}
export function visualState(state) { return ['urgent','critical','danger'].includes(state)?'urgent':['inspect','watch','warning','deteriorating'].includes(state)?'inspect':['normal','stable','ok'].includes(state)?'normal':'unavailable'; }
export function buildRequest(values,{mode='scenario',referenceId=null}={}) {
  if (!['scenario','hybrid'].includes(mode)) throw new Error('데이터 모드를 확인해 주세요.');
  const selected={};
  for (const field of FIELDS) {
    if (mode==='hybrid' && field==='vibration_rms_g') continue;
    if (typeof values[field] !== 'number' || !Number.isFinite(values[field])) throw new Error('모든 운전 값을 숫자로 입력해 주세요.');
    selected[field]=values[field];
  }
  return {mode,values:selected,reference_id:referenceId||null};
}
export const VIBRATION_LABELS={idle:'진동 없음',baseline:'기준 진동',increased:'강한 진동',intermittent:'간헐 진동'};
export function buildReplayRequest(values,{recordingId='random',frameIndex=0,referenceId=null}={}) {
  if(!['training','random'].includes(recordingId))throw new Error('재생할 기록을 확인해 주세요.');
  if(!Number.isInteger(frameIndex)||frameIndex<0)throw new Error('재생 구간을 확인해 주세요.');
  const {values:mockValues}=buildRequest(values,{mode:'hybrid',referenceId});
  return {recording_id:recordingId,frame_index:frameIndex,values:mockValues,reference_id:referenceId||null};
}
export function replayClassification(data){
  const replay=data?.vibration_replay,prediction=replay?.prediction;
  if(!prediction||!Object.hasOwn(VIBRATION_LABELS,prediction.label)||['stale','missing','invalid'].includes(data?.current?.quality?.vibration_rms_g))return {state:'unavailable',title:'분석 결과 확인 필요',summary:'기록 분석 결과를 다시 불러와 주세요.',checks:[]};
  return {...prediction,state:prediction.label==='idle'?'idle':visualState(prediction.state)};
}
export function recommendationFallback(data){
  const replay=data?.mode==='replay',prediction=replayClassification(data),okay=visualState(data?.operational_status?.state||data?.comparison?.state)==='normal';
  if(replay&&prediction.state==='idle')return {okay:false,title:'진동원 정지 여부를 확인하세요',text:prediction.summary};
  if(replay&&okay)return {okay:true,title:'기준 진동과 비슷합니다',text:'실측 기록은 학습한 기준 패턴입니다. 수온·유압은 모의 값으로 별도 비교하고 있습니다.'};
  return {okay,title:okay?'새로운 변화가 없습니다':'비교할 조건을 확인해 주세요',text:okay?'이번 점검은 이전 정상 기록과 비슷합니다. 현재 기록을 저장하고 다음 점검에서도 같은 조건으로 확인해 주세요.':'이전 기록과 운전 조건·센서 출처가 맞아야 변화를 판단할 수 있습니다. 값이 없거나 조건이 다른 항목은 정상으로 처리하지 않습니다.'};
}
export function replayHistoryPoints(frames,currentIndex){
  return frames.filter(frame=>frame.frame_index<=currentIndex&&Number.isFinite(frame.rms_g)).sort((a,b)=>a.frame_index-b.frame_index).slice(-8).map(frame=>({id:`replay-${frame.frame_index}`,label:`${numberText(frame.start_s,1)}초`,value:frame.rms_g,current:frame.frame_index===currentIndex}));
}
export function scenarioValues(name,defaults={}) {
  const values={coolant_c:80.2,oil_kgf_cm2:3.19,vibration_rms_g:.046,load_pct:62,ambient_c:28.5,engine_rpm:1800,elapsed_s:600,...defaults};
  if(name==='coolant') values.coolant_c+=7;
  else if(name==='oil') values.oil_kgf_cm2-=.7;
  else if(name==='vibration') values.vibration_rms_g*=1.8;
  else if(name==='combined') { values.coolant_c+=7;values.oil_kgf_cm2-=.7;values.vibration_rms_g*=1.8; }
  else if(name==='load') values.load_pct=90;
  else if(name!=='normal') throw new Error('지원하지 않는 시나리오입니다.');
  return values;
}
export function dateLabel(record,{short=false}={}) {
  const raw=typeof record==='string'?record:record?.inspected_at||record?.created_at||record?.at;
  const date=new Date(raw); if(!raw||!Number.isFinite(date.getTime()))return '날짜 미확인';
  return date.toLocaleDateString('ko-KR',{...(short?{}:{year:'numeric'}),month:'2-digit',day:'2-digit'}).replace(/\.$/,'');
}
export function modelCount(comparison) { return comparison?.model?.training_record_count ?? comparison?.model?.training_record_ids?.length ?? 0; }
export function modelCaption(comparison) { const count=modelCount(comparison);return count>0?`정상 점검 ${count}회 학습`:'비교 기준 확인 필요'; }
export function retrainMessage(comparison) { const count=modelCount(comparison);return count>0?`정상 점검 ${count}회로 학습했습니다.`:'정상 기록 학습을 갱신했습니다. 현재 운전의 비교 조건을 확인하세요.'; }
export function channelFor(overview,field) {
  const fromModel=overview?.comparison?.channels?.find(c=>c.field===field)||{};
  const result={field,...METRICS[field],...fromModel};
  result.current=fromModel.current ?? overview?.current?.values?.[field] ?? null;
  result.source=fromModel.source ?? overview?.current?.sources?.[field] ?? overview?.current?.source;
  result.state=visualState(fromModel.state);
  const fixed=overview?.operational_status?.alerts?.filter(a=>a.field===field)||[];
  if(fixed.some(a=>a.severity==='urgent'))result.state='urgent';
  else if(fixed.length)result.state='inspect';
  result.quality=overview?.current?.quality?.[field];
  if(field==='vibration_rms_g'&&overview?.mode==='replay'){
    const prediction=replayClassification(overview);
    result.replay=prediction;result.state=prediction.state;result.previous=null;result.adjusted_delta=null;
  }

  result.reasonText=REASONS[fromModel.reason] || (result.state==='unavailable'?'비교 조건과 기록을 확인해 주세요.':'');
  if((fromModel.reference_source&&fromModel.reference_source!==result.source)||['source_mismatch','installation_mismatch','acquisition_mismatch','acquisition_invalid','unverified_sensor_operating_context'].includes(fromModel.reason))result.previous=null;
  if(result.replay)result.reasonText=result.replay.summary||'실측 진동 기록의 패턴을 모델로 구분합니다.';
  if(['stale','missing','invalid'].includes(result.quality)) {result.current=null;result.reasonText='유효한 센서값을 기다립니다.';}
  return result;
}
export function historyPoints(overview,field) {
  const current=overview?.current||{};
  const targetSource=current.sources?.[field]||current.source;
  let records=(overview?.records||[]).filter(r=> {
    const source=r.sources?.[field]||r.source;
    if (source!==targetSource) return false;
    if (targetSource!=='scenario' && r.installation_id!==current.installation_id) return false;
    return typeof r.values?.[field]==='number' && Number.isFinite(r.values[field]);
  });
  records.sort((a,b)=>new Date(a.inspected_at||a.created_at)-new Date(b.inspected_at||b.created_at));
  records=records.slice(-8);
  const result=records.map(r=>({id:r.id,label:dateLabel(r,{short:true}),value:r.values[field],current:false,selected:r.id===overview.comparison?.reference_id}));
  if (typeof current.values?.[field]==='number' && Number.isFinite(current.values[field]) && !['stale','invalid','missing'].includes(current.quality?.[field]))result.push({id:'current',label:'현재',value:current.values[field],current:true});
  return result;
}
export function staleOverview(overview){
  const urgent=visualState(overview.operational_status?.state)==='urgent';
  return {...overview,imu:{...overview.imu,state:'stale'},current:{...overview.current,quality:{...overview.current?.quality,vibration_rms_g:'stale'}},
    operational_status:{...overview.operational_status,state:urgent?'urgent':'unavailable',headline:urgent?'최근 경보 · 연결 확인 필요':'분석 결과 갱신이 중단되었습니다',summary:'새 결과를 받지 못해 현재 상태를 확인할 수 없습니다. 분석 장치의 연결을 확인해 주세요.'},
    comparison:{...overview.comparison,state:'unavailable',recommendations:[],channels:(overview.comparison?.channels||[]).map(c=>({...c,state:'unavailable',adjusted_delta:null,reason:'invalid_or_stale_current_value'}))}};
}
export function chartSvg(points,metric,state='normal') {
  if(!points.length)return '<div class="chart-empty">표시할 측정값을 기다립니다.</div>';
  const width=660,height=245,left=46,right=23,top=25,bottom=38;
  const vals=points.map(p=>p.value),span=Math.max(...vals)-Math.min(...vals);
  const pad=Math.max(span*.32,Math.abs(Math.max(...vals))*.04,10**(-metric.digits)*3);
  let min=Math.max(0,Math.min(...vals)-pad),max=Math.max(...vals)+pad;
  if(min===max)max=min+1;
  const x=i=>left+(points.length===1?(width-left-right)/2:i*(width-left-right)/(points.length-1));
  const y=v=>top+(max-v)/(max-min)*(height-top-bottom);
  const color=state==='urgent'?'#c3655e':state==='inspect'?'#b98b3f':'#4f8b8a';
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(metric.label)} 점검 기록 비교"><title>${escapeHtml(points.map(p=>`${p.label} ${numberText(p.value,metric.digits)} ${metric.unit}`).join(', '))}</title>`;
  for(let i=0;i<4;i++){const v=min+(max-min)*i/3;svg+=`<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" stroke="#e6edf3"/><text x="${left-10}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="#95a6b7">${numberText(v,metric.digits)}</text>`;}
  if(points.length>1){const historical=points.filter(p=>!p.current);if(historical.length>1)svg+=`<polyline points="${historical.map((p,i)=>`${x(i)},${y(p.value)}`).join(' ')}" fill="none" stroke="#9bb1c8" stroke-width="2.5"/>`;if(points.at(-1).current){const i=points.length-1;svg+=`<line x1="${x(i-1)}" y1="${y(points[i-1].value)}" x2="${x(i)}" y2="${y(points[i].value)}" stroke="${color}" stroke-width="2.5" stroke-dasharray="5 5"/>`;}}
  points.forEach((p,i)=>{svg+=`<circle cx="${x(i)}" cy="${y(p.value)}" r="${p.current?5:p.selected?4.5:3.5}" fill="${p.current?color:p.selected?'#496d98':'#9bb1c8'}" stroke="white" stroke-width="2"/><text x="${x(i)}" y="${height-12}" text-anchor="middle" font-size="11" font-weight="${p.current?600:400}" fill="${p.current?color:'#91a1b2'}">${escapeHtml(p.label)}</text>`;if(p.current)svg+=`<text x="${x(i)-3}" y="${Math.max(13,y(p.value)-13)}" text-anchor="end" font-size="12" font-weight="600" fill="${color}">${numberText(p.value,metric.digits)}</text>`;});
  return svg+'</svg>';
}

if(typeof document!=='undefined') boot();
function boot(){
  const $=id=>document.getElementById(id);
  const state={data:null,mode:'scenario',referenceId:null,chart:'coolant_c',busy:false,token:'',pendingSave:null,lastStatus:null,catalog:null,replay:{recordingId:'random',frameIndex:0,frameCount:48,playing:false,timer:null,speed:1,frames:[]}};
  const inputs=()=>Object.fromEntries(FIELDS.map(f=>{const raw=$('settings-form').elements.namedItem(f).value;return [f,raw.trim()===''?NaN:Number(raw)];}));
  const setInputs=values=>{for(const field of FIELDS)if(typeof values?.[field]==='number'&&Number.isFinite(values[field]))$('settings-form').elements.namedItem(field).value=values[field];};
  const toast=message=>{$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,4500);};
  async function request(path,body){
    if(state.busy)return null;
    state.busy=true;state.lastStatus=null;document.body.setAttribute('aria-busy','true');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const headers={Accept:'application/json'};if(body!==undefined)headers['Content-Type']='application/json';if(state.token)headers.Authorization=`Bearer ${state.token}`;
      const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',credentials:'omit',signal:controller.signal});
      state.lastStatus=response.status;const data=await response.json();
      if(!response.ok)throw new Error(response.status===401?'연결 설정에서 인증 토큰을 확인해 주세요.':(data.error?.message||data.message||'입력값을 확인하고 다시 시도해 주세요.'));
      $('error-banner').hidden=true;$('connection-label').textContent='분석 장치 연결됨';return data;
    }catch(error){const msg=error.name==='AbortError'?'응답을 기다리는 중입니다. 잠시 후 다시 시도해 주세요.':error.message==='Failed to fetch'?'분석 장치에 연결하지 못했습니다. 연결 상태를 확인해 주세요.':error.message;$('error-banner').textContent=msg;$('error-banner').hidden=false;if(document.querySelector('dialog[open]'))toast(msg);if(state.lastStatus===null||state.lastStatus>=500){$('connection-label').textContent='분석 장치 연결 확인 필요';if(['hybrid','replay'].includes(state.mode)&&state.data){stopReplay();render(staleOverview(state.data),{syncInputs:false});}}return null;}
    finally{clearTimeout(timer);state.busy=false;document.body.removeAttribute('aria-busy');}
  }
  function render(data,{syncInputs=true}={}){
    if(!data?.comparison||!data?.current)return;
    state.data=data;state.mode=data.mode||state.mode;if(syncInputs)setInputs(data.current.values);
    const comparison=data.comparison,status=data.operational_status||comparison,statusState=visualState(status.state);
    $('side-equipment').textContent=data.equipment?.id||'GEN-10K-001';$('equipment-label').textContent=`${data.equipment?.id||'GEN-10K-001'} · ${data.equipment?.rated_kw||10} kW`;
    $('status-banner').className=`status-banner ${statusState}`;$('status-icon').textContent=statusState==='normal'?'✓':statusState==='unavailable'?'○':'!';
    $('status-title').textContent=status.headline||comparison.headline||'점검 기록을 비교합니다';$('status-summary').textContent=status.summary||(statusState==='urgent'&&status.alerts?.length?status.alerts.map(a=>a.title).join(' · '):comparison.summary||'비교 가능한 운전 기록을 확인해 주세요.');
    $('status-chip').textContent={normal:'변화 없음',inspect:'점검 권고',urgent:'즉시 확인',unavailable:'비교 보류'}[statusState];
    $('reference-caption').textContent=state.mode==='replay'?'반복 측정에서 학습한 진동':comparison.reference_at?`${dateLabel(comparison.reference_at)} 점검`:'비교할 기록 필요';$('model-caption').textContent=state.mode==='replay'?'진동 세기 + 시간 패턴 분석':modelCaption(comparison);
    $('mode-badge').textContent=state.mode==='replay'?'실측 기록 + 모의 값':state.mode==='hybrid'?'실측 + 모의 운전':'모의 운전';$('mode-description').textContent=state.mode==='replay'?'진동은 실측 기록 재생 · 수온과 유압은 모의 값입니다.':state.mode==='hybrid'?'진동은 연결된 IMU · 수온과 유압은 모의 값입니다.':'수온·유압·진동은 모의 값입니다.';
    ['scenario','hybrid','replay'].forEach(mode=>{$(`mode-${mode}`).classList.toggle('active',state.mode===mode);$(`mode-${mode}`).setAttribute('aria-pressed',String(state.mode===mode));});
    const vibrationInput=$('settings-form').elements.namedItem('vibration_rms_g');vibrationInput.disabled=state.mode!=='scenario';$('vibration-input-note').textContent=state.mode==='replay'?'선택한 실측 기록의 진동을 사용합니다. 웹 값으로 덮어쓰지 않습니다.':state.mode==='hybrid'?'연결된 IMU가 측정합니다. 웹 값으로 덮어쓰지 않습니다.':'모의 운전에서만 직접 변경합니다.';
    document.querySelector('[data-preset="vibration"]').disabled=state.mode!=='scenario';
    document.querySelector('[data-preset="combined"]').textContent=state.mode!=='scenario'?'수온·유압 변화':'복합 변화';
    $('save-open').disabled=state.mode==='replay';$('retrain-button').disabled=state.mode==='replay';$('history-policy').textContent=state.mode==='replay'?'실측 기록 재생은 새 점검 기록이나 학습 기준으로 저장하지 않습니다.':'정상으로 확인한 기록만 모델 학습에 사용합니다.';
    renderReplay(data);
    $('metric-grid').innerHTML=Object.keys(METRICS).map(field=>metricMarkup(channelFor(data,field))).join('');
    renderConditions(data);renderReferences(data);renderChart();renderRecommendations(data);renderHistory(data);renderDetails(data);
    const imu=data.imu||{};let sensorText=imu.state==='fresh'?'IMU 값을 수신하고 있습니다.':imu.state==='stale'?'IMU 값이 오래되었습니다. 센서 연결을 확인해 주세요.':'실측 IMU가 아직 연결되지 않았습니다.';
    if(state.mode==='replay')sensorText='아이폰에 부착한 IMU의 저장 기록을 재생하고 있습니다.';
    $('sensor-status').innerHTML=`${escapeHtml(sensorText)} <a href="/lab" style="text-decoration:underline">센서 연결·기준 설정 ↗</a>`;
  }
  function metricMarkup(channel){
    const spec=METRICS[channel.field],comparable=channel.adjusted_delta!==null&&channel.adjusted_delta!==undefined;
    const delta=channel.replay?({normal:'기준과 유사',inspect:'점검 권고',idle:'정지 확인',unavailable:'분석 확인 필요'}[channel.replay.state]):comparable?`${signedText(channel.adjusted_delta,spec.digits)} ${spec.unit}`:'비교 보류';
    const previous=channel.replay?'진동 패턴 분석':`이전 ${numberText(channel.previous,spec.digits)} ${spec.unit}`;
    return `<article class="metric ${channel.state}${channel.replay?' replayed':''}"><div class="metric-header"><span class="metric-symbol" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${spec.icon}</svg></span><span>${spec.label}</span><span class="badge source">${escapeHtml(sourceLabel(channel.source,channel.quality))}</span></div><div class="metric-value">${numberText(channel.current,spec.digits)}<small>${spec.unit}</small></div><div class="metric-bottom"><span>${escapeHtml(previous)}</span><span class="metric-delta">${escapeHtml(delta)}</span></div><p class="metric-reason">${escapeHtml(channel.reasonText)}</p></article>`;
  }
  function stopReplay(){
    state.replay.playing=false;clearTimeout(state.replay.timer);state.replay.timer=null;
    $('replay-play').textContent=state.replay.frameIndex>=state.replay.frameCount-1?'↻ 처음부터 재생':'▶ 기록 재생';
  }
  function renderReplay(data){
    const active=state.mode==='replay';$('vibration-replay').hidden=!active;
    if(!active)return;
    const frame=data.vibration_replay;if(!frame)return;
    const r=state.replay;
    if(r.recordingId!==frame.recording_id)r.frames=[];
    r.recordingId=frame.recording_id;r.frameIndex=frame.frame_index;r.frameCount=frame.frame_count;
    const index=r.frames.findIndex(item=>item.frame_index===frame.frame_index);
    if(index<0)r.frames.push(frame);else r.frames[index]=frame;
    $('replay-recording').value=frame.recording_id;
    const recording=state.catalog?.recordings?.find(item=>item.id===frame.recording_id),recordedAt=new Date(frame.recorded_at||recording?.recorded_at||'');
    $('replay-recorded-at').textContent=Number.isFinite(recordedAt.getTime())?`${recordedAt.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'})} KST 측정`:'저장된 실측 기록';
    const prediction=replayClassification(data);
    $('replay-state').className=`badge ${prediction.state==='idle'?'unknown':prediction.state}`;
    $('replay-state').textContent={normal:'정상 기준',inspect:'점검 권고',idle:'정지 확인',unavailable:'분석 확인 필요'}[prediction.state]||'분석 확인 필요';
    $('replay-prediction').textContent=prediction.title;
    $('replay-summary').textContent=prediction.summary;
    $('replay-prediction').parentElement.className=`replay-result ${prediction.state}`;
    $('replay-progress').textContent=`${frame.frame_index+1} / ${frame.frame_count} 구간`;
    $('replay-time').textContent=`기록 ${numberText(frame.start_s,1)}–${numberText(frame.end_s,1)}초`;
    $('replay-progress-bar').max=frame.frame_count;$('replay-progress-bar').value=frame.frame_index+1;
    $('replay-previous').disabled=frame.frame_index===0;$('replay-next').disabled=frame.frame_index>=frame.frame_count-1;
    $('replay-play').textContent=r.playing?'Ⅱ 일시정지':frame.frame_index>=frame.frame_count-1?'↻ 처음부터 재생':'▶ 기록 재생';
    const expected=frame.evaluation?.expected_label,correct=frame.evaluation?.correct;
    const match=typeof correct==='boolean'?` · ${correct?'일치':'불일치'}`:'';
    const truth=Object.hasOwn(VIBRATION_LABELS,expected)?VIBRATION_LABELS[expected]:'확인 필요';
    const predicted=VIBRATION_LABELS[frame.prediction?.label]||'확인 필요';
    const evaluation=recording?.evaluation,amplitude=evaluation?.rms_only,pattern=evaluation?.rms_and_pattern;
    const resultCounts=amplitude&&pattern?`<p><strong>${recording?.role==='held_out_evaluation'?'별도 랜덤 기록 1회 검증':'학습에 사용한 기록의 결과'}</strong><br>세기만 분석 ${numberText(amplitude.correct,0)} / ${numberText(amplitude.count,0)} 구간 · 세기와 시간 패턴 분석 ${numberText(pattern.correct,0)} / ${numberText(pattern.count,0)} 구간</p>`:'';
    $('replay-evaluation-content').innerHTML=`${resultCounts}<p><strong>모델이 판단한 패턴:</strong> ${escapeHtml(predicted)}<br><strong>실험 앱에 기록된 정답:</strong> ${escapeHtml(truth)}${escapeHtml(match)}</p><p>반복 기록으로 학습한 모델을 고정한 뒤 별도 무작위 기록에 적용합니다. 정답은 결과 확인에만 사용합니다.</p><p>각 진동의 안정된 2초 구간을 분석하며 전환·휴식 구간은 제외합니다. 아이폰 실험에서의 패턴 구분으로, 실제 발전기의 고장 진단 결과는 아닙니다. 같은 기록의 48개 창은 서로 연관되어 있으며, 알려진 네 패턴 중 가장 가까운 것을 고르는 모델입니다.</p>`;
  }
  async function loadCatalog(){
    if(state.catalog)return true;
    const catalog=await request('/api/vibration/catalog');if(!catalog)return false;state.catalog=catalog;
    if(Array.isArray(catalog.recordings)&&catalog.recordings.length)$('replay-recording').innerHTML=catalog.recordings.filter(r=>['training','random'].includes(r.id)).map(r=>`<option value="${escapeHtml(r.id)}">${escapeHtml(r.label)}</option>`).join('');
    return true;
  }
  async function replayFrame(index,{close=false}={}){
    if(state.busy)return null;let body;
    try{body=buildReplayRequest(inputs(),{recordingId:state.replay.recordingId,frameIndex:index,referenceId:state.referenceId});}catch(error){toast(error.message);stopReplay();return null;}
    const result=await request('/api/vibration/replay',body);
    if(result){render(result);if(close)$('settings-dialog').close();}else stopReplay();return result;
  }
  function scheduleReplay(){
    clearTimeout(state.replay.timer);if(!state.replay.playing)return;
    state.replay.timer=setTimeout(async()=>{
      if(!state.replay.playing||state.mode!=='replay')return;
      if(state.busy||document.hidden||document.querySelector('dialog[open]')){scheduleReplay();return;}
      if(state.replay.frameIndex>=state.replay.frameCount-1){stopReplay();return;}
      const result=await replayFrame(state.replay.frameIndex+1);
      if(result&&state.replay.playing){if(state.replay.frameIndex>=state.replay.frameCount-1)stopReplay();else scheduleReplay();}
    },2000/state.replay.speed);
  }
  function renderConditions(data){
    const c=data.comparison.conditions||{},current=c.current||data.current.values,previous=c.previous||{};
    const rows=[['load_pct','부하',0,'%'],['ambient_c','주변',1,'°C'],['engine_rpm','회전수',0,'rpm'],['elapsed_s','운전',0,'초']];
    const matches=data.comparison.matches||[];
    $('context-strip').innerHTML='<span class="context-title">운전 조건 <span style="font-weight:400">이전 → 현재</span></span>'+rows.map(([field,label,digits,unit])=>{const match=matches.find(m=>m.field===field);return `<span class="${match?.matched===false?'mismatch':''}">${label}<strong>${numberText(previous[field],digits)} → ${numberText(current[field],digits)} ${unit}</strong>${match?.matched===false?' · 차이 큼':''}</span>`;}).join('');
  }
  function renderReferences(data){
    $('reference-select').innerHTML='<option value="">가장 가까운 정상 점검</option>'+[...(data.records||[])].reverse().map(r=>`<option value="${escapeHtml(r.id)}">${escapeHtml(dateLabel(r))} · ${r.confirmed_normal?'정상 확인':'관찰 기록'}</option>`).join('');
    $('reference-select').value=state.referenceId||'';
  }
  function renderChart(){
    if(!state.data)return;const channel=channelFor(state.data,state.chart),spec=METRICS[state.chart];
    const replaying=state.mode==='replay'&&state.chart==='vibration_rms_g';
    if(replaying){
      $('chart-summary').innerHTML=`<strong>${numberText(channel.current,spec.digits)} <span>${spec.unit}</span></strong><span>${escapeHtml(channel.replay?.title||'실측 진동 기록')}</span>`;
      $('inspection-chart').innerHTML=chartSvg(replayHistoryPoints(state.replay.frames,state.replay.frameIndex),spec,channel.state);
      $('comparison-note').textContent='이번 재생에서 확인한 실측 구간입니다. 전환 구간은 건너뛰며, 발전기의 모의 점검 기록과 비교하지 않습니다.';
    }else{
    $('chart-summary').innerHTML=`<strong>${numberText(channel.current,spec.digits)} <span>${spec.unit}</span></strong><span>${channel.adjusted_delta===null||channel.adjusted_delta===undefined?'조건이 맞는 기록을 기다립니다.':`조건 보정 후 이전 대비 ${signedText(channel.adjusted_delta,spec.digits)} ${spec.unit}`}</span>`;
    $('inspection-chart').innerHTML=chartSvg(historyPoints(state.data,state.chart),spec,channel.state);
    $('comparison-note').textContent='그래프는 보정 전 기록값입니다. 점검 권고는 같은 운전 조건으로 보정해 비교합니다.';
    }
    $('legend-history-label').textContent=replaying?'재생한 측정 구간':'이전 점검';$('legend-current-label').textContent=replaying?'선택 구간':'현재 값';$('chart-legend-note').textContent=replaying?'2초 구간 RMS':'보정 전 기록값';
    document.querySelectorAll('[data-chart]').forEach(el=>{el.classList.toggle('active',el.dataset.chart===state.chart);el.setAttribute('aria-pressed',String(el.dataset.chart===state.chart));});
  }
  function renderRecommendations(data){
    let recommendations=[...(data.comparison.recommendations||[])];
    const alerts=[...(data.operational_status?.alerts||[])].sort((a,b)=>(b.severity==='urgent'?1:0)-(a.severity==='urgent'?1:0));
    recommendations=[...alerts.map(alert=>({title:alert.title,reason:alert.reason||alert.summary||(alert.field==='vibration_rms_g'?'부착 후 등록한 정상 진동 기준과 다른 변화가 이어집니다.':'현재 값이 설정된 보호 참고 범위에 도달했습니다.'),checks:alert.checks})),...recommendations];
    if(recommendations.length)$('recommendations').innerHTML=`<ol class="recommendation-list">${recommendations.map((r,i)=>`<li class="recommendation-item"><span class="step-num">${i+1}</span><div><h3>${escapeHtml(r.title)}</h3><p>${escapeHtml(r.reason)}</p>${Array.isArray(r.checks)&&r.checks.length?`<ul>${r.checks.slice(0,3).map(c=>`<li>${escapeHtml(typeof c==='string'?c:c.label||c.title||'상태 확인')}</li>`).join('')}</ul>`:''}</div></li>`).join('')}</ol>`;
    else {const fallback=recommendationFallback(data);$('recommendations').innerHTML=`<div class="all-clear"><div class="clear-icon">${fallback.okay?'✓':'○'}</div><h3>${escapeHtml(fallback.title)}</h3><p>${escapeHtml(fallback.text)}</p></div>`;}
  }
  function renderHistory(data){
    $('history-body').innerHTML=[...(data.records||[])].reverse().map(r=>{const selected=r.id===data.comparison.reference_id,v=r.values||{};return `<tr class="${selected?'selected':''}"><td><strong>${escapeHtml(dateLabel(r))}</strong><small>${escapeHtml(r.label||'정기 점검')} · ${r.source==='scenario'?'모의 기록':'실측 포함'}</small></td><td>${numberText(v.load_pct,0)}% · ${numberText(v.ambient_c,1)}°C<small>${numberText(v.engine_rpm,0)} rpm · ${numberText((v.elapsed_s||0)/60,0)}분</small></td><td>${numberText(v.coolant_c,1)} °C</td><td>${numberText(v.oil_kgf_cm2,2)}</td><td>${numberText(v.vibration_rms_g,3)} g</td><td><span class="badge ${r.confirmed_normal?'normal':'unknown'}">${r.confirmed_normal?'정상 확인':'관찰 기록'}</span></td><td><button type="button" class="table-action ${selected?'selected':''}" data-reference="${escapeHtml(r.id)}">${selected?'비교 중':'비교'}</button></td></tr>`;}).join('')||'<tr><td colspan="7">아직 저장된 점검 기록이 없습니다.</td></tr>';
    $('history-body').querySelectorAll('[data-reference]').forEach(button=>button.addEventListener('click',async()=>{if(state.busy)return;state.referenceId=button.dataset.reference;await compare();$('comparison').scrollIntoView({behavior:'smooth',block:'start'});}));
  }
  function renderDetails(data){
    const model=data.comparison.model||{},replay=data.mode==='replay';$('analysis-brief').textContent=replay?'실측 진동 패턴 + 모의 상태 비교':modelCaption(data.comparison);
    const modelDescription=replay?'진동은 반복 실험에서 배운 진동 세기와 시간 패턴으로 구분합니다. 수온·유압은 모의 발전기의 정상 기록에서 학습한 모델로 따로 비교합니다. 실측 진동과 모의 발전기 값을 함께 학습하지 않습니다.':'부하·주변 온도·회전수·운전 경과에 따른 수온, 유압, 진동의 기대값을 학습합니다. 현재 값과 정상 기대값, 조건을 보정한 이전 기록의 차이를 함께 확인합니다.';
    $('analysis-detail').innerHTML=`<div class="analysis-grid"><div><h3>${replay?'각 데이터에 맞는 두 가지 분석':'정상 기록에서 학습한 관계'}</h3><p>${escapeHtml(modelDescription)}</p><ul><li>${escapeHtml(modelCaption(data.comparison))}</li><li>모델: ${escapeHtml(model.algorithm||model.model_id||'조건별 정상 패턴 모델')}</li><li>모델의 기준은 제조사 보호 설정이 아닙니다.</li></ul></div><div><h3>현재 데이터의 출처</h3><ul>${Object.keys(METRICS).map(field=>{const ch=channelFor(data,field);return `<li>${METRICS[field].label}: ${escapeHtml(sourceLabel(ch.source,ch.quality))}${ch.state==='unavailable'?' · 비교 보류':''}</li>`;}).join('')}</ul><p>${escapeHtml(data.provenance_note||'모의 운전 데이터는 실제 발전기 측정 기록이 아닙니다.')}</p><p>표시된 차이는 현재 상태의 변화입니다. 고장 원인, 고장 확률, 잔여수명을 의미하지 않습니다.</p></div></div>`;
  }
  async function compare({close=false}={}){if(state.busy)return;if(state.mode==='replay')return replayFrame(state.replay.frameIndex,{close});let body;try{body=buildRequest(inputs(),{mode:state.mode,referenceId:state.referenceId});}catch(e){toast(e.message);return;}const result=await request('/api/inspections/compare',body);if(result){render(result);if(close)$('settings-dialog').close();}return result;}
  async function load(){const data=await request('/api/inspections/overview');if(data){render(data);if(data.mode==='replay'&&await loadCatalog())renderReplay(data);}}
  $('open-settings').addEventListener('click',()=>$('settings-dialog').showModal());$('connection-settings').addEventListener('click',()=>$('connection-dialog').showModal());
  document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
  document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}}));
  // Closing without applying discards draft inputs, so saving always matches the visible result.
  $('settings-dialog').addEventListener('close',()=>setInputs(state.data?.current?.values));
  $('settings-form').addEventListener('submit',event=>{event.preventDefault();compare({close:true});});
  document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',async()=>{if(state.busy)return;setInputs(scenarioValues(button.dataset.preset,state.data?.defaults));await compare({close:true});}));
  $('reference-select').addEventListener('change',async()=>{state.referenceId=$('reference-select').value||null;await compare();});
  document.querySelectorAll('[data-chart]').forEach(button=>button.addEventListener('click',()=>{state.chart=button.dataset.chart;renderChart();}));
  ['scenario','hybrid','replay'].forEach(mode=>$(`mode-${mode}`).addEventListener('click',async()=>{
    if(state.busy||state.mode===mode)return;stopReplay();const previous=state.mode,previousValues={...state.data?.current?.values};
    if(mode==='replay'&&!(await loadCatalog()))return;
    state.mode=mode;
    if(mode==='replay'){setInputs(scenarioValues('normal',state.data?.defaults));state.chart='vibration_rms_g';}
    else if(previous==='replay'&&mode==='scenario')setInputs({...previousValues,vibration_rms_g:state.data?.defaults?.vibration_rms_g??.046});
    const result=await compare();if(!result){state.mode=previous;setInputs(previousValues);}
  }));
  $('replay-recording').addEventListener('change',async()=>{if(state.busy){$('replay-recording').value=state.replay.recordingId;return;}stopReplay();const previous=state.replay.recordingId;state.replay.recordingId=$('replay-recording').value;const result=await replayFrame(0);if(!result){state.replay.recordingId=previous;$('replay-recording').value=previous;}else state.replay.frames=[result.vibration_replay];renderChart();});
  $('replay-previous').addEventListener('click',()=>{if(state.busy)return;stopReplay();replayFrame(Math.max(0,state.replay.frameIndex-1));});
  $('replay-next').addEventListener('click',()=>{if(state.busy)return;stopReplay();replayFrame(Math.min(state.replay.frameCount-1,state.replay.frameIndex+1));});
  $('replay-play').addEventListener('click',async()=>{if(state.replay.playing){stopReplay();return;}if(state.busy)return;if(state.replay.frameIndex>=state.replay.frameCount-1){state.replay.frames=[];if(!(await replayFrame(0)))return;}state.replay.playing=true;$('replay-play').textContent='Ⅱ 일시정지';scheduleReplay();});
  $('replay-speed').addEventListener('change',()=>{state.replay.speed=Number($('replay-speed').value);if(state.replay.playing)scheduleReplay();});
  $('refresh-button').addEventListener('click',()=>compare());
  $('save-open').addEventListener('click',()=>{if(state.mode==='replay')return;$('save-dialog').showModal();if(state.pendingSave)toast('이전 저장 결과를 같은 요청으로 확인합니다.');});
  $('save-form').addEventListener('submit',async event=>{
    event.preventDefault();if(state.busy||state.mode==='replay')return;
    try{if(!state.pendingSave)state.pendingSave={...buildRequest(state.data?.current?.values||inputs(),{mode:state.mode,referenceId:state.referenceId}),request_id:globalThis.crypto?.randomUUID?.()||`inspection-${Date.now()}-${Math.random().toString(16).slice(2)}`,label:$('save-form').elements.label.value.trim()||'정기 시험 운전',confirmed_normal:$('save-form').elements.confirmed_normal.checked};}
    catch(error){toast(error.message);return;}
    const result=await request('/api/inspections/save',state.pendingSave);
    if(result){state.pendingSave=null;render(result);$('save-dialog').close();$('save-form').reset();toast('점검 기록을 저장했습니다.');}
    else if(state.lastStatus>=400&&state.lastStatus<500)state.pendingSave=null;
  });
  $('retrain-button').addEventListener('click',async()=>{if(state.mode==='replay')return;const data=await request('/api/inspections/retrain',{});if(data){render(data);toast(retrainMessage(data.comparison));}});
  $('connection-form').addEventListener('submit',async event=>{event.preventDefault();state.token=$('connection-form').elements.token.value.trim();$('connection-dialog').close();await load();});
  document.querySelectorAll('.sidebar nav a').forEach(a=>a.addEventListener('click',()=>{document.querySelectorAll('.sidebar nav a').forEach(link=>link.classList.remove('active'));a.classList.add('active');}));
  setInterval(()=>{if(state.mode==='hybrid'&&!state.busy&&!document.hidden&&!document.querySelector('dialog[open]'))compare();},2000);
  load();
}
