// Display server-computed trends only. Simulated time never enters the live sample API.
const channelLabels = {coolant_c:'냉각수 온도',oil_kgf_cm2:'윤활유 압력'};
const methodLabels = {linear:'단순 선형 추세',ridge:'학습형 Ridge',persistence:'현재값 유지'};
const units = {coolant_c:'°C',oil_kgf_cm2:'kgf/cm²'};
const states = {collecting:'이력 수집 중',stable:'지속 악화 근거 없음',deteriorating:'악화 방향 관찰',recovering:'회복 흐름 관찰',unavailable:'추세 판단 불가',limit_reached:'현재 경계 도달'};
const finite = value => typeof value === 'number' && Number.isFinite(value);
const format = value => finite(value) ? new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(value) : '—';
export const clockLabel = seconds => finite(seconds) ? `${Math.floor(Math.max(0,seconds)/60)}:${String(Math.floor(Math.max(0,seconds)%60)).padStart(2,'0')}` : '—';

export function validateExperiment(data) {
  if (data?.source !== 'scenario' || data.time_mode !== 'simulated' || !Array.isArray(data.rows) || !data.rows.length) throw new Error('모의 추세 실험 응답 형식이 올바르지 않습니다.');
  let previous=-1;
  for (const row of data.rows) {
    if (!finite(row.t_s) || row.t_s < 0 || row.t_s <= previous || !row.values || typeof row.values !== 'object' || row.trend?.source !== 'scenario') throw new Error('시간 또는 데이터 출처가 확인되지 않은 추세 실험입니다.');
    previous=row.t_s;
  }
  return data;
}

export function projectChart(experiment, index, channel) {
  if (!Object.hasOwn(channelLabels,channel)) throw new Error('지원하지 않는 추세 항목입니다.');
  const position=Math.max(0,Math.min(experiment.rows.length-1,Math.trunc(index)));
  const row=experiment.rows[position], trend=row.trend || {}, value=trend.channels?.[channel] || {};
  // Slice first: observations from later rows must never appear at an earlier playback time.
  const observed=experiment.rows.slice(0,position+1).filter(r=>r.t_s >= row.t_s-300 && finite(r.values[channel])).map(r=>({t:r.t_s,value:r.values[channel]}));
  const horizon=finite(trend.horizon_s) ? trend.horizon_s : 120;
  const current=finite(value.current) ? value.current : row.values[channel];
  const prediction=finite(value.predicted) && finite(current) && horizon > 0 && !['collecting','unavailable'].includes(trend.state) ? [{t:row.t_s,value:current},{t:row.t_s+horizon,value:value.predicted}] : [];
  const band=prediction.length && finite(value.lower) && finite(value.upper) && value.lower <= value.upper ? [{t:row.t_s,value:current},{t:row.t_s+horizon,value:value.lower},{t:row.t_s+horizon,value:value.upper}] : [];
  return {position,now:row.t_s,observed,prediction,band,limit:finite(value.limit)?value.limit:null,unit:value.unit || units[channel],label:channelLabels[channel],horizon};
}

export function visibleMilestones(experiment,index) {
  const reached=experiment.rows.slice(0,index+1);
  const first=fn=>reached.find(fn)?.t_s ?? null;
  return {current:first(r=>r.current_anomaly?.persistent_change===true),trend:first(r=>r.trend?.advisory===true),fixed:first(r=>r.fixed_warning===true)};
}

export function comparisonText(experiment,index) {
  const milestones=visibleMilestones(experiment,index);
  if (index < experiment.rows.length-1) return {milestones,summary:'현재까지의 발생 시점만 표시합니다. 마지막 시점에서 전체 비교를 확인하세요.',complete:false};
  let summary;
  if (milestones.trend === null && milestones.fixed === null) summary='이 시나리오에서는 추세 점검 권고와 고정 경고가 발생하지 않았습니다.';
  else if (milestones.trend === null) summary='추세 점검 권고 없이 고정 경고가 발생했습니다. 이 시나리오에서 추세 기능은 사전 경고를 제공하지 못했습니다.';
  else if (milestones.current === null) summary='추세 점검 권고가 발생했습니다. 기존 모델의 지속 이상은 관측되지 않았으므로 각 판단 근거를 함께 확인하세요.';
  else if (milestones.current < milestones.trend) summary=`현재 이상 탐지가 추세 권고보다 ${format(milestones.trend-milestones.current)}초 먼저 반응했습니다. 추세는 진행 방향을 설명하는 보조 정보입니다.`;
  else if (milestones.current > milestones.trend) summary=`이 모의 시나리오에서는 추세 권고가 현재 이상 탐지보다 ${format(milestones.current-milestones.trend)}초 먼저 나왔습니다. 실제 장비 성능을 의미하지 않습니다.`;
  else summary='현재 이상 탐지와 추세 권고가 같은 시점에 나왔습니다. 추세 기능의 추가 시간 이득은 없습니다.';
  if (milestones.trend !== null && milestones.fixed !== null) {
    const lead=milestones.fixed-milestones.trend;
    summary+=lead > 0 ? ` 고정 경고보다는 ${format(lead)}초 앞섰습니다.` : lead === 0 ? ' 고정 경고와도 같은 시점입니다.' : ` 고정 경고보다 ${format(-lead)}초 늦었습니다.`;
  }
  return {milestones,summary,complete:true};
}

export function evaluationText(experiment,complete) {
  if(!complete)return '뒤의 관측값과 전체 비교 결과는 마지막 시점에서 공개합니다.';
  const evaluation=experiment.evaluation || {};
  const sections=[];
  if(typeof evaluation.summary==='string')sections.push(evaluation.summary);
  const details=(value)=>finite(value)?new Intl.NumberFormat('ko-KR',{maximumFractionDigits:4}).format(value):'—';
  const selected=evaluation.selected_method || {};
  const selectedEntries=Object.entries(selected).filter(([key])=>Object.hasOwn(channelLabels,key));
  if(selectedEntries.length)sections.push(`선택된 추정 방법: ${selectedEntries.map(([key,value])=>`${channelLabels[key]} — ${methodLabels[value] || value}`).join(', ')}.`);
  for(const key of ['coolant_c','oil_kgf_cm2']){
    const mae=evaluation.mae;
    if(finite(mae?.ridge?.[key]) && finite(mae?.linear?.[key]))sections.push(`${channelLabels[key]} 미래값 평균 절대 오차: 학습형 Ridge ${details(mae.ridge[key])}, 단순 선형 추세 ${details(mae.linear[key])} ${units[key]}. 작을수록 좋습니다.`);
  }
  const transient=evaluation.advisory_summary?.transient;
  if(finite(transient?.runs) && finite(transient?.runs_with_false_advisory))sections.push(`일시적 변화 모의 운전 ${transient.runs}회 중 ${transient.runs_with_false_advisory}회에서 사전 점검 오경보가 발생했습니다.`);
  const baseline=evaluation.current_anomaly_comparison;
  if(finite(baseline?.worsening_runs) && finite(baseline?.current_earlier_runs))sections.push(`악화 모의 운전 ${baseline.worsening_runs}회 중 ${baseline.current_earlier_runs}회에서 기존 이상 탐지가 추세보다 먼저 반응했습니다.`);
  if(selectedEntries.length || transient)sections.push('추세 기능이 기존 상태 감지보다 우수하다는 근거가 충분하지 않아 실험용 보조로 분리했습니다. 기존 상태 경고와 알림에는 반영하지 않습니다.');
  const summary=sections.join('\n');
  const note=typeof experiment.comparison?.note==='string' ? experiment.comparison.note : '';
  return `${summary} ${experiment.scope || '모의 시나리오의 기능 비교이며 실제 고장 예측 검증이 아닙니다.'} ${note}`.trim();
}

function drawChart(element,plot) {
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 940 300');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${plot.label}: 현재 시점까지 모의 관측값과 조건부 미래 추정. 미래 실제 관측은 표시하지 않습니다.`);
  const add=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));if(text!==undefined)n.textContent=text;svg.append(n);return n;};
  const values=[...plot.observed,...plot.prediction,...plot.band].map(p=>p.value);if(finite(plot.limit))values.push(plot.limit);
  const lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1,pad=Math.max((hi-lo)*.18,plot.unit==='°C'?1:.1);
  const minY=lo-pad,maxY=hi+pad,minX=plot.observed[0]?.t ?? 0,maxX=Math.max(plot.now+plot.horizon,minX+120);
  const x=t=>68+((t-minX)/(maxX-minX))*848,y=v=>247-((v-minY)/(maxY-minY))*208;
  add('rect',{x:x(plot.now),y:28,width:Math.max(0,x(maxX)-x(plot.now)),height:220,fill:'#f5f8fb'});
  for(let i=0;i<5;i++){const v=minY+(maxY-minY)*i/4;add('line',{x1:68,y1:y(v),x2:916,y2:y(v),stroke:'#e5eeec'});add('text',{x:57,y:y(v)+4,'text-anchor':'end',fill:'#68817f','font-size':12},format(v));}
  for(let i=0;i<5;i++){const t=minX+(maxX-minX)*i/4;add('text',{x:x(t),y:275,'text-anchor':'middle',fill:'#68817f','font-size':12},clockLabel(t));}
  add('text',{x:68,y:16,fill:'#537674','font-size':12},`${plot.label} (${plot.unit})`);
  add('text',{x:916,y:294,'text-anchor':'end',fill:'#68817f','font-size':12},'모의 운전 시간');
  if(finite(plot.limit)){add('line',{x1:68,y1:y(plot.limit),x2:916,y2:y(plot.limit),stroke:'#bd7068','stroke-dasharray':'6 5','stroke-width':1.5});add('text',{x:911,y:y(plot.limit)-6,'text-anchor':'end',fill:'#a4635c','font-size':11},`시연 경계 ${format(plot.limit)}`);}
  const points=list=>list.map(p=>`${x(p.t)},${y(p.value)}`).join(' ');
  if(plot.band.length)add('polygon',{points:points(plot.band),fill:'#94adca',opacity:.22});
  if(plot.observed.length)add('polyline',{points:points(plot.observed),fill:'none',stroke:'#287b70','stroke-width':3,'stroke-linejoin':'round'});
  if(plot.prediction.length)add('polyline',{points:points(plot.prediction),fill:'none',stroke:'#6587b2','stroke-width':2.5,'stroke-dasharray':'7 5'});
  add('line',{x1:x(plot.now),y1:28,x2:x(plot.now),y2:248,stroke:'#a8bdbc','stroke-dasharray':'3 4'});
  add('text',{x:Math.min(x(plot.now)+5,840),y:25,fill:'#537674','font-size':11},'현재 시점');
  element.replaceChildren(svg);
}

export function createTrendUI({get,changed=()=>{}}) {
  let client=null,catalog=null,experiment=null,index=0,playing=false,playingTimer=null,loading=false,lastTick=0,virtualTime=0;
  const fail=error=>{get('trend-error').hidden=false;get('trend-error').textContent=error.message || String(error);};
  function updateConnection(active,busy=false){
    const unavailable=!active || !client;
    get('trend-load').disabled=unavailable || busy || loading || !catalog?.scenarios?.length;
    get('trend-scenario').disabled=unavailable || busy || loading;
    get('trend-play').disabled=!experiment || loading;
  }
  function pause(){playing=false;clearTimeout(playingTimer);playingTimer=null;get('trend-play').textContent='재생';}
  function render(){
    if(!experiment)return;
    const row=experiment.rows[index],trend=row.trend || {},current=row.current_anomaly || {};
    const speed=Number(get('trend-speed').value) || 10;
    get('trend-position').value=String(index);get('trend-clock').textContent=`모의 운전 ${clockLabel(row.t_s)} / ${clockLabel(experiment.rows.at(-1).t_s)}`;
    get('trend-clock-detail').textContent=`${speed}배 재생 · 기기의 실제 운전 시간과 별개`;
    get('trend-current-state').textContent=current.persistent_change ? '지속 이상 · 점검 필요' : ['change','change_observed'].includes(current.state) ? '변화 관찰 · 확인 중' : current.state==='within_baseline' ? '학습 기준 내' : '판단 대기 / 불가';
    get('trend-current').dataset.alert=String(current.persistent_change===true);
    get('trend-current-score').textContent=`점수 ${format(current.score)} / 기준 ${format(current.threshold)}`;
    get('trend-outlook-state').textContent=trend.advisory===true ? '악화 흐름 · 조기 점검' : states[trend.state] || '추세 판단 불가';get('trend-outlook').dataset.alert=String(trend.advisory===true);
    get('trend-outlook-summary').textContent=trend.summary || trend.reason || '이력 확인 중';
    get('trend-fixed-state').textContent=row.fixed_warning ? '고정 경고 발생' : '고정 경고 없음';get('trend-fixed').dataset.alert=String(row.fixed_warning===true);
    const plot=projectChart(experiment,index,get('trend-channel').value);drawChart(get('trend-chart'),plot);
    const channel=trend.channels?.[get('trend-channel').value] || {};
    get('trend-assumption').textContent=plot.prediction.length ? `현재 ${format(channel.current)} ${plot.unit} → ${format(plot.horizon)}초 뒤 ${format(channel.predicted)} ${plot.unit}. 추정 방법: ${methodLabels[channel.method] || channel.method || '방법 미제공'}. 최근 변화가 이어지고 운전 조건이 유지된다는 가정입니다. ${plot.band.length ? '음영은 검증 잔차로 정한 경험적 오차 범위이며 미래 보장이 아닙니다.' : '현재 추정에는 검증 오차 범위가 제공되지 않았습니다.'}` : trend.state==='limit_reached' ? '현재 시연용 경계에 도달했습니다. 미래 예상보다 현재 경고와 점검 근거를 먼저 확인하세요.' : trend.state==='unavailable' ? '유효한 추세 판단을 제공할 수 없습니다. 운전 조건과 데이터 품질을 확인하세요.' : `유효한 이력을 기다립니다. 최소 ${format(trend.lookback_s ?? catalog?.policy?.lookback_s ?? 120)}초를 관측한 뒤 미래 추정을 표시합니다.`;
    get('trend-experiment-checks').replaceChildren();for(const check of trend.checks || []){const li=document.createElement('li');li.textContent=check;get('trend-experiment-checks').append(li);}
    const comparison=comparisonText(experiment,index);get('trend-comparison-summary').textContent=comparison.summary;
    get('trend-milestones').textContent=`현재 이상 ${clockLabel(comparison.milestones.current)} · 추세 권고 ${clockLabel(comparison.milestones.trend)} · 고정 경고 ${clockLabel(comparison.milestones.fixed)}`;
    get('trend-evaluation').textContent=evaluationText(experiment,comparison.complete);
  }
  function tick(){
    if(!playing || !experiment)return;
    const now=performance.now(),speed=Number(get('trend-speed').value) || 10;
    virtualTime+=(now-lastTick)*speed/1000;lastTick=now;
    while(index+1<experiment.rows.length && experiment.rows[index+1].t_s<=virtualTime)index++;
    render();if(index>=experiment.rows.length-1){pause();return;}playingTimer=setTimeout(tick,100);
  }
  get('trend-play').addEventListener('click',()=>{if(playing){pause();return;}if(!experiment)return;if(index===experiment.rows.length-1)index=0;virtualTime=experiment.rows[index].t_s;lastTick=performance.now();playing=true;get('trend-play').textContent='일시 정지';tick();});
  get('trend-position').addEventListener('input',()=>{pause();index=Math.max(0,Math.min(experiment.rows.length-1,Number(get('trend-position').value)));render();});
  get('trend-channel').addEventListener('change',render);get('trend-speed').addEventListener('change',()=>{lastTick=performance.now();render();});
  get('trend-scenario').addEventListener('change',()=>{const scenario=catalog?.scenarios?.find(s=>s.id===get('trend-scenario').value);get('trend-scenario-note').textContent=scenario?.description || '실험은 실측 데이터와 분리되어 있습니다.';});
  get('trend-load').addEventListener('click',async()=>{
    if(!client || loading || client.busy)return;
    pause();loading=true;get('trend-error').hidden=true;updateConnection(true,true);changed();
    try{experiment=validateExperiment(await client.runTrendExperiment(get('trend-scenario').value));index=0;get('trend-position').max=String(experiment.rows.length-1);get('trend-experiment').hidden=false;get('trend-scenario-note').textContent=`${experiment.scenario?.label || '모의 운전'} · ${experiment.scenario?.description || ''}`;render();}
    catch(error){fail(error);}finally{loading=false;updateConnection(Boolean(client));changed();}
  });
  return {
    updateConnection,
    async connect(nextClient){
      pause();client=nextClient;catalog=null;loading=true;get('trend-error').hidden=true;
      try{const next=await client.getTrendCatalog();if(!Array.isArray(next.scenarios))throw Error('추세 실험 목록을 확인할 수 없습니다.');catalog=next;get('trend-scenario').replaceChildren();for(const item of catalog.scenarios){const option=document.createElement('option');option.value=item.id;option.textContent=item.label;get('trend-scenario').append(option);}get('trend-scenario-note').textContent=catalog.scenarios[0]?.description || '등록된 시나리오가 없습니다.';}
      catch(error){fail(error);}finally{loading=false;updateConnection(true);changed();}
    },
    disconnect(){pause();client=null;updateConnection(false);},
    renderLive(trend,{stale=false,age_s=null}={}){
      const historical=Boolean(trend)&&stale;
      get('live-trend-card').dataset.state=historical?'unavailable':trend?.state || 'collecting';
      get('live-trend-state').textContent=historical?'이전 추세 기록 · 새 분석 대기':trend?.advisory===true ? '악화 흐름 · 조기 점검' : states[trend?.state] || '이력 수집 대기';
      get('live-trend-summary').textContent=trend?.summary || '수온·유압을 실제 시간에 따라 누적합니다. 한 번 바꾼 값만으로 미래를 판단하지 않습니다.';
      get('live-trend-detail').textContent=historical ? `마지막 분석 ${format(age_s)}초 전. 아래 내용은 현재 판단으로 사용하지 않습니다.` : `유효 기록 ${trend?.history_count ?? 0}개 · 최소 ${trend?.lookback_s ?? 120}초 이력 · ${trend?.horizon_s ?? 120}초 앞의 조건부 추정. IMU 진동 수명 예측은 포함하지 않습니다.`;
      get('live-trend-values').replaceChildren();
      for(const [key,value] of Object.entries(trend?.channels || {})){
        if(!Object.hasOwn(channelLabels,key) || !finite(value.predicted))continue;
        const item=document.createElement('p');item.textContent=`${historical?'이전 추정 · ':''}${channelLabels[key]} ${format(value.current)} → ${format(value.predicted)} ${value.unit || units[key]} · ${methodLabels[value.method] || value.method || '방법 미제공'} (같은 흐름 유지 가정)`;get('live-trend-values').append(item);
      }
      get('live-trend-checks').replaceChildren();for(const check of trend?.checks || []){const li=document.createElement('li');li.textContent=historical?`이전 권고: ${check}`:check;get('live-trend-checks').append(li);}
    }
  };
}
