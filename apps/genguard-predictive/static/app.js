"use strict";

const $ = (id) => document.getElementById(id);
const stateNames = {normal:"정상 운전", inspection:"점검 필요", urgent:"긴급 확인", collecting:"분석 준비 중", unknown:"판정 불가"};
const statusDescriptions = {
  normal:"현재 관측에서 유의한 이상 징후가 확인되지 않았습니다.",
  inspection:"평소와 다른 운전 추이가 감지되었습니다. 점검 근거를 확인하세요.",
  urgent:"현재 모의 계측값이 시연 긴급 기준에 도달했습니다. 판정 근거와 추이를 확인하세요.",
  collecting:"판정에 필요한 유효한 운전 이력을 수집하고 있습니다.",
  unknown:"유효한 계측과 운전조건이 확인되기 전에는 상태를 판정하지 않습니다."
};
const fieldInfo = {
  coolant_c:{label:"냉각수 온도",unit:"°C",digits:1,icon:"♧",limits:[{value:92,label:"점검",level:"inspection"},{value:100,label:"긴급",level:"urgent"}]},
  oil_pressure_kgf_cm2:{label:"윤활유압",unit:"kgf/cm²",digits:2,icon:"◈",limits:[{value:2,label:"점검",level:"inspection"},{value:.6,label:"긴급",level:"urgent"}]},
  voltage_v:{label:"출력 전압",unit:"V",digits:1,icon:"ϟ",limits:[{value:197.6,label:"하한",level:"inspection"},{value:218.4,label:"상한",level:"inspection"}]},
  frequency_hz:{label:"출력 주파수",unit:"Hz",digits:2,icon:"∿",limits:[]},
  engine_rpm:{label:"엔진 회전수",unit:"rpm",digits:0,icon:"↻",limits:[]},
  load_pct:{label:"부하율",unit:"%",digits:1,icon:"▥",limits:[]},
  battery_v:{label:"배터리 전압",unit:"V",digits:2,icon:"▭",limits:[]},
  fuel_pct:{label:"연료 잔량",unit:"%",digits:1,icon:"◒",limits:[]},
  fuel_pressure_bar:{label:"연료 압력",unit:"bar",digits:2,limits:[]},
  current_a:{label:"A상 전류",unit:"A",digits:1,limits:[]},
  current_b:{label:"B상 전류",unit:"A",digits:1,limits:[]},
  current_c:{label:"C상 전류",unit:"A",digits:1,limits:[]},
  ambient_c:{label:"외기온",unit:"°C",digits:1,limits:[]},
  current_imbalance_pct:{label:"전류 불평형",unit:"%",digits:1,limits:[]},
  imbalance_pct:{label:"전류 불평형",unit:"%",digits:1,limits:[]}
};
const scenarioNames = {normal:"정상 운전",load_transient:"일시적 부하 변동",cooling_decline:"냉각 성능 저하",oil_decline:"윤활유압 저하",voltage_drift:"출력 전압 변화",imbalance:"상별 전류 불평형",charging_decline:"충전 성능 저하",fuel_decline:"연료 공급 저하",recovery:"이상 후 회복"};
let currentState = null;
let activeView = "dashboard";
let connected = false;
let requestBusy = false;
let evaluationLoaded = false;
let evaluationBusy = false;
let evaluationData = null;
let scenarioSignature = "";
let chartPoints = [];
let chartGeometry = null;
let toastTimer;

function escapeHTML(value){return String(value ?? "").replace(/[&<>"']/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function number(value){if(value===null||value===undefined||value==="")return null;const n=Number(value);return Number.isFinite(n)?n:null;}
function fmt(value,digits=1){const n=number(value);return n===null?"—":n.toLocaleString("ko-KR",{minimumFractionDigits:digits,maximumFractionDigits:digits});}
function timeText(seconds){const n=number(seconds);if(n===null)return "--:--";if(n>1e9)return new Date(n*1000).toLocaleTimeString("ko-KR",{hour12:false});const s=Math.max(0,Math.floor(n));return (s>=3600?String(Math.floor(s/3600)).padStart(2,"0")+":":"")+String(Math.floor(s/60)%60).padStart(2,"0")+":"+String(s%60).padStart(2,"0");}
function durationText(seconds){const n=number(seconds);if(n===null)return "미확정";if(n<=0)return "현재";if(n<60)return `${Math.ceil(n)}초`;return `${Math.floor(n/60)}분${Math.round(n%60)?` ${Math.round(n%60)}초`:""}`;}
function historyProgress(data){const observed=number(data.analysis?.history_seconds),required=number(data.analysis?.required_history_seconds)??number(data.report_summary?.history_required_s)??180;return observed===null?`— / ${fmt(required,0)}초`:`${fmt(Math.min(Math.max(observed,0),required),0)} / ${fmt(required,0)}초`;}
function infoFor(field){return fieldInfo[field]||{label:field,unit:"",digits:2,limits:[]};}
function formatField(field,value){const info=infoFor(field);return `${fmt(value,info.digits)}${info.unit?" "+info.unit:""}`;}
function setText(id,text){$(id).textContent=text;}
function showToast(message){setText("toast",message);$("toast").classList.remove("hidden");clearTimeout(toastTimer);toastTimer=setTimeout(()=>$("toast").classList.add("hidden"),4500);}

async function api(path,body){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),4500);
  try{
    const res=await fetch(path,{method:body?"POST":"GET",headers:body?{"Content-Type":"application/json"}:{},body:body?JSON.stringify(body):undefined,cache:"no-store",signal:controller.signal});
    let data;
    try{data=await res.json();}catch{throw new Error(`서버 응답 형식 오류 (${res.status})`);}
    if(!res.ok||data?.error)throw new Error(data?.error||data?.message||`요청 실패 (${res.status})`);
    return data;
  }finally{clearTimeout(timer);}
}

function setConnection(ok,message=""){
  connected=ok;
  $("connection").classList.toggle("disconnected",!ok);
  $("connection").innerHTML=`<i></i>${ok?"서버 연결됨":"수신 중단"}`;
  $("connection-error").classList.toggle("hidden",ok);
  if(!ok){
    setText("connection-error",`계측 서버에 연결할 수 없습니다. 현재 상태는 판정 불가입니다. ${message} 마지막으로 수신한 그래프와 수치는 과거 기록입니다.`);
    renderStatus("unknown");
    setText("data-source","수신 중단");
    setText("status-description","데이터 연결이 끊겼습니다. 마지막 수신값으로 정상 여부를 판단하지 않습니다.");
    setText("chart-footnote","수신 중단 · 마지막 기록 표시");
    setText("next-title","예측 갱신 중단");
    setText("next-description","유효한 계측 연결이 복구되면 예측을 갱신합니다.");
    document.querySelectorAll(".metric-meta").forEach(el=>el.textContent="마지막 수신값 · 갱신 중단");
  }
  $("play").disabled=!ok||requestBusy;
}

function renderStatus(kind){
  const valid=Object.hasOwn(stateNames,kind)?kind:"unknown";
  $("status-card").className=`status-card state-${valid}`;
  setText("status-title",stateNames[valid]);
  setText("status-description",statusDescriptions[valid]);
  setText("status-symbol",({normal:"✓",inspection:"!",urgent:"!",collecting:"◌",unknown:"–"})[valid]);
}

function renderControls(data){
  const stream=data.mode==="stream";
  setText("mode-title",stream?"실시간 계측":"시연 재생");
  setText("mode-description",stream?"외부 계측 입력 · 수신 시각 확인":"모의 계측 데이터 · 실제 장비와 분리");
  setText("data-source",stream?"실시간":"모의 계측");
  const profile=data.profile||{};
  setText("model-id",number(profile.rated_kw)!==null?`${fmt(profile.rated_kw,0)} kW · ${fmt(profile.voltage_ll_v,0)} V / ${fmt(profile.rated_hz,0)} Hz`:"장비 프로필 미확정");
  $("model-id").title=data.model_id||"모델 식별자 미수신";
  const scenarios=Array.isArray(data.scenarios)&&data.scenarios.length?data.scenarios:Object.entries(scenarioNames).map(([id,label])=>({id,label}));
  const signature=JSON.stringify(scenarios);
  if(signature!==scenarioSignature){
    $("scenario").innerHTML=scenarios.map(s=>`<option value="${escapeHTML(s.id)}">${escapeHTML(s.label||scenarioNames[s.id]||s.id)}</option>`).join("");scenarioSignature=signature;
  }
  if(data.scenario&&document.activeElement!==$("scenario"))$("scenario").value=data.scenario;
  if(number(data.speed)!==null&&document.activeElement!==$("speed"))$("speed").value=String(data.speed);
  $("play").innerHTML=data.playing?"<span>Ⅱ</span> 일시정지":"<span>▶</span> 재생";
  $("play").disabled=stream||requestBusy||!connected;
  $("reset").disabled=requestBusy;
  $("scenario").disabled=requestBusy;
  $("speed").disabled=stream||requestBusy;
  setText("replay-time",stream?`수신 ${timeText(data.latest?.timestamp_s)}`:`${timeText(data.elapsed_s)} / ${timeText(data.duration_s)}`);
  const progress=number(data.duration_s)>0?Math.min(100,Math.max(0,Number(data.elapsed_s||0)/data.duration_s*100)):0;
  $("replay-progress-fill").style.width=`${progress}%`;
}

function renderMetrics(data){
  const primary=["coolant_c","oil_pressure_kgf_cm2","voltage_v","frequency_hz","engine_rpm","load_pct"];
  const secondary=["current_a","current_b","current_c","battery_v","fuel_pct","fuel_pressure_bar","ambient_c"];
  const latest=data.latest||{};
  const alerts=Array.isArray(data.analysis?.alerts)?data.analysis.alerts:[];
  const metricCard=field=>{
    const info=infoFor(field),alert=alerts.filter(a=>a.field===field).sort((a,b)=>(a.level==="urgent"?0:1)-(b.level==="urgent"?0:1))[0];
    const value=latest[field];
    let meta=number(value)===null?"유효 계측값 없음":"최근 수신값";
    if(field==="voltage_v"&&number(data.profile?.voltage_ll_v)!==null)meta=`프로필 기준 ${fmt(data.profile.voltage_ll_v,0)} V`;
    if(field==="frequency_hz"&&number(data.profile?.rated_hz)!==null)meta=`프로필 기준 ${fmt(data.profile.rated_hz,0)} Hz`;
    if(field==="load_pct"&&number(data.profile?.rated_kw)!==null)meta=`기준 출력 ${fmt(data.profile.rated_kw,0)} kW`;
    return `<article class="metric-card ${alert?`flag-${escapeHTML(alert.level)}`:""}" data-field="${field}"><div class="metric-label"><span>${info.label}</span><span class="metric-icon" aria-hidden="true">${info.icon||"·"}</span></div><div class="metric-value">${fmt(value,info.digits)}<small>${info.unit}</small></div><div class="metric-meta">${escapeHTML(meta)}</div></article>`;
  };
  $("metrics").innerHTML=`<div class="metrics-primary">${primary.map(metricCard).join("")}</div><div class="metrics-secondary">${secondary.map(metricCard).join("")}</div>`;
}

function renderForecastSummary(data){
  const a=data.analysis||{};
  const crossings=(Array.isArray(a.crossings)?a.crossings:[]).filter(c=>number(c.eta_s)!==null).sort((x,y)=>Number(x.eta_s)-Number(y.eta_s));
  const currentAlerts=(Array.isArray(a.alerts)?a.alerts:[]).filter(alert=>(alert.source==="fixed_rule"||alert.source==="demo_imbalance_rule")&&["urgent","inspection"].includes(alert.level)).sort((x,y)=>(x.level==="urgent"?0:1)-(y.level==="urgent"?0:1));
  if(currentAlerts.length){
    const alert=currentAlerts[0];
    const label=alert.source==="demo_imbalance_rule"?"상별 전류 편차":infoFor(alert.field).label;
    setText("next-title",`현재 시연 ${alert.level==="urgent"?"긴급":"점검"} 기준 도달`);
    setText("next-description",`${label}${currentAlerts.length>1?` 외 ${currentAlerts.length-1}개 항목`:""}에서 현재 기준 도달이 확인되었습니다.`);
    const next=crossings.find(c=>Number(c.eta_s)>0);
    setText("next-meta",next?`추가 예측: ${next.label||infoFor(next.field).label} · ${durationText(next.eta_s)} 후 ${next.level==="urgent"?"긴급":"점검"} 기준`:"현재 관측에 따른 판정 · 그래프에서 변화 추이를 확인하세요.");
  }else if(crossings.length){
    const c=crossings[0];
    setText("next-title",`${c.label||infoFor(c.field).label} · ${durationText(c.eta_s)}${number(c.eta_s)>0?" 후":""}`);
    setText("next-description",`예측값이 ${c.level==="urgent"?"긴급 확인":"점검"} 시연 기준에 도달할 것으로 추정됩니다.`);
    const range=number(c.earliest_s)!==null&&number(c.latest_s)!==null?`도달 추정 범위 ${durationText(c.earliest_s)}–${durationText(c.latest_s)}`:"도달 구간 미산출 · 점 추정값만 표시";
    setText("next-meta",range);
  }else if(Array.isArray(a.forecast)&&a.forecast.length){
    const horizon=Math.max(...a.forecast.map(p=>number(p.ahead_s)||0));
    setText("next-title","예측 구간 내 기준 도달 없음");
    setText("next-description",`앞으로 ${durationText(horizon)} 동안의 예측 추이를 확인하세요.`);
    setText("next-meta","기준 도달이 없다는 것이 무고장을 보장하지 않습니다.");
  }else{
    setText("next-title",data.playing?"예측을 위한 이력 수집 중":"재생 후 예측이 시작됩니다");
    setText("next-description","유효한 운전 이력이 쌓이면 미래 추정과 불확실성을 표시합니다.");
    setText("next-meta",`연속 운전 이력 수집 ${historyProgress(data)}`);
  }
}

function renderEvidence(data){
  const a=data.analysis||{};
  const evidence=Array.isArray(a.evidence)?a.evidence:[];
  const guidance=Array.isArray(a.guidance)?a.guidance:[];
  setText("reason",a.observation||a.reason||"유효한 분석 결과를 기다리고 있습니다.");
  setText("evidence-count",evidence.length?`${evidence.length}개 항목`:"수집 중");
  $("model-evidence-title").classList.toggle("hidden",!evidence.length);
  $("evidence-list").innerHTML=evidence.slice(0,3).map(e=>`<div class="evidence-item"><span>${escapeHTML(e.label||infoFor(e.field).label)}</span><b>${escapeHTML(formatField(e.field,e.value))}</b></div>`).join("");
  $("guidance-list").innerHTML=(guidance.length?guidance.slice(0,2):["데이터 출처와 장비 프로필을 확인하세요."]).map(g=>`<li>${escapeHTML(g)}</li>`).join("");
  $("evidence-table").innerHTML=evidence.length?evidence.map(e=>`<tr><td>${escapeHTML(e.label||infoFor(e.field).label)}</td><td>${escapeHTML(formatField(e.field,e.value))}</td><td>${escapeHTML(formatField(e.field,e.expected))}</td><td>${number(e.z)===null?"산출되지 않음":`${fmt(e.z,2)} σ`}</td></tr>`).join(""):'<tr><td colspan="4" class="empty-state">아직 산출된 측정 항목별 근거가 없습니다.</td></tr>';
  $("anomaly-summary").innerHTML=[{label:"이상 점수",value:fmt(a.anomaly_score,3)},{label:"탐지 기준",value:fmt(a.anomaly_threshold,3)},{label:"관측 이력",value:historyProgress(data)}].map(s=>`<div class="analysis-stat"><span>${s.label}</span><strong>${escapeHTML(s.value)}</strong></div>`).join("");
  setText("observation",a.observation||a.reason||"유효한 분석 결과를 기다리고 있습니다.");
  const crossings=Array.isArray(a.crossings)?a.crossings:[];
  $("crossings-list").innerHTML=crossings.length?crossings.map(c=>`<div class="crossing-row"><strong>${escapeHTML(c.label||infoFor(c.field).label)} · ${c.level==="urgent"?"긴급 확인":"점검"}</strong><p>${escapeHTML(c.kind==="derived_forecast_imbalance_pct"?`${fmt(c.threshold,1)} %`:formatField(c.field,c.threshold))} 시연 기준 · ${escapeHTML(durationText(c.eta_s))}${number(c.eta_s)>0?" 후 도달 추정":""}</p><small>${number(c.earliest_s)!==null&&number(c.latest_s)!==null?`추정 범위 ${escapeHTML(durationText(c.earliest_s))}–${escapeHTML(durationText(c.latest_s))}`:"도달 구간 미산출"}</small></div>`).join(""):'<div class="empty-state">현재 예측에 산출된 기준 도달 시점이 없습니다.</div>';
}

function renderEvents(data){
  const events=Array.isArray(data.events)?[...data.events].sort((a,b)=>(number(b.timestamp_s)||0)-(number(a.timestamp_s)||0)):[];
  setText("event-count",`${events.length}건`);
  const last=events.length?events[0]:null;
  setText("latest-event",last?`${timeText(last.timestamp_s)}  ${last.message||stateNames[last.state]||last.state}`:"아직 기록된 이벤트가 없습니다.");
  $("events-list").innerHTML=events.length?events.slice(0,60).map(e=>`<div class="event-row"><span class="event-time">${escapeHTML(timeText(e.timestamp_s))}</span><span class="event-state ${escapeHTML(e.state||"unknown")}">${escapeHTML(stateNames[e.state]||e.state||"기록")}</span><span class="event-message">${escapeHTML(e.message||"")}</span></div>`).join(""):'<div class="empty-state">상태가 바뀌면 이벤트가 여기에 기록됩니다.</div>';
}

function renderVibration(data){
  const v=data.vibration||{};
  const sourceNames={recorded_imu:"실측 기록 재생",live_imu:"실시간 IMU",disconnected:"연결 해제"};
  setText("vibration-source",sourceNames[v.source]||"출처 미확정");
  setText("vibration-pattern",v.pattern_id||"데이터 대기");
  setText("vibration-label",v.label||"유효한 진동 분석 결과가 없습니다.");
  $("vibration-stats").innerHTML=`<div class="analysis-stat"><span>벡터 RMS</span><strong>${fmt(v.rms_vector_g,4)}<small>g</small></strong></div><div class="analysis-stat"><span>포락선 변동계수</span><strong>${fmt(v.envelope_cv,3)}</strong></div>`;
  $("vibration-quality").innerHTML=(Array.isArray(v.quality_notes)?v.quality_notes:[]).map(n=>`<li>${escapeHTML(n)}</li>`).join("");
  const mode=({recorded_imu:"recorded",live_imu:"live",disconnected:"off"})[v.source];
  document.querySelectorAll("[data-vibration-mode]").forEach(b=>b.classList.toggle("selected",b.dataset.vibrationMode===mode));
}

function niceBounds(min,max){
  if(min===max){const span=Math.max(Math.abs(min)*.04,.5);min-=span;max+=span;}
  const pad=Math.max((max-min)*.15,.05);return [min-pad,max+pad];
}
function pathFrom(points,x,y,valueKey="value"){return points.map((p,i)=>`${i?"L":"M"}${x(p.time).toFixed(2)},${y(p[valueKey]).toFixed(2)}`).join(" ");}
function renderChart(data){
  const field=$("chart-field").value,info=infoFor(field),a=data.analysis||{};
  const limits=Array.isArray(data.profile?.rules)?data.profile.rules.filter(r=>r.field===field).flatMap(r=>["inspection","urgent"].filter(level=>number(r[level])!==null).map(level=>({value:Number(r[level]),label:level==="urgent"?"긴급":r.direction==="low"?"점검 하한":"점검 상한",level}))):info.limits;
  const rawHistory=Array.isArray(data.history)?data.history:[];
  const history=rawHistory.filter(r=>number(r.timestamp_s)!==null&&number(r[field])!==null).map(r=>({time:Number(r.timestamp_s),value:Number(r[field]),kind:"past"})).slice(-700);
  const latest=data.latest||{};
  if(!history.length&&number(latest.timestamp_s)!==null&&number(latest[field])!==null)history.push({time:Number(latest.timestamp_s),value:Number(latest[field]),kind:"past"});
  const now=number(latest.timestamp_s)??(history.length?history[history.length-1].time:0);
  const forecast=(Array.isArray(a.forecast)?a.forecast:[]).filter(p=>number(p.ahead_s)!==null&&number(p.values?.[field])!==null).map(p=>({time:now+Number(p.ahead_s),value:Number(p.values[field]),lower:number(p.lower?.[field]),upper:number(p.upper?.[field]),kind:"future"}));
  const svg=$("forecast-chart");
  chartPoints=[];chartGeometry=null;
  if(!history.length&&!forecast.length){
    svg.innerHTML='<defs><pattern id="empty-grid" width="100" height="45" patternUnits="userSpaceOnUse"><path d="M 100 0 L 0 0 0 45" fill="none" stroke="#f0f4f6" stroke-width="1"/></pattern></defs><rect x="45" y="12" width="700" height="195" fill="url(#empty-grid)"/><text x="400" y="105" text-anchor="middle" fill="#8fa5b1" font-size="12">아직 표시할 관측값이 없습니다.</text><text x="400" y="127" text-anchor="middle" fill="#b0bec6" font-size="10">재생을 시작하거나 계측 데이터를 연결하세요.</text>';
    setText("chart-subtitle",`${info.label} · ${info.unit}`);
    setText("chart-footnote","관측값과 예측값이 생성되면 표시됩니다.");
    return;
  }
  const W=800,H=245,L=48,R=78,T=17,B=32;
  const values=[...history.map(p=>p.value),...forecast.flatMap(p=>[p.value,p.lower,p.upper].filter(v=>v!==null)),...limits.map(t=>t.value)];
  let [minY,maxY]=niceBounds(Math.min(...values),Math.max(...values));
  let minX=history.length?history[0].time:now,maxX=forecast.length?forecast[forecast.length-1].time:now;
  if(maxX<=minX){minX=Math.max(0,minX-30);maxX=minX+60;}
  const x=t=>L+(t-minX)/(maxX-minX)*(W-L-R),y=v=>T+(maxY-v)/(maxY-minY)*(H-T-B);
  let markup='<defs><clipPath id="plot-clip"><rect x="48" y="17" width="674" height="196"/></clipPath><linearGradient id="future-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#78b8d4" stop-opacity=".22"/><stop offset="100%" stop-color="#78b8d4" stop-opacity=".1"/></linearGradient></defs>';
  if(forecast.length)markup+=`<rect x="${x(now)}" y="${T}" width="${Math.max(0,x(maxX)-x(now))}" height="${H-T-B}" fill="#f8fbfd"/>`;
  for(let i=0;i<5;i++){
    const value=minY+(maxY-minY)*i/4,py=y(value);
    const digits=(maxY-minY)<2?2:(maxY-minY)<20?1:0;
    markup+=`<line x1="${L}" x2="${W-R}" y1="${py}" y2="${py}" stroke="#edf2f5"/><text x="${L-10}" y="${py+3}" text-anchor="end" fill="#9aaab4" font-size="9">${fmt(value,digits)}</text>`;
  }
  for(let i=0;i<5;i++){
    const t=minX+(maxX-minX)*i/4;
    markup+=`<text x="${x(t)}" y="${H-10}" text-anchor="middle" fill="#9aaab4" font-size="9">${escapeHTML(timeText(t))}</text>`;
  }
  for(const limit of limits){
    const py=y(limit.value),color=limit.level==="urgent"?"#d19292":"#cfa55c";
    markup+=`<line x1="${L}" x2="${W-R}" y1="${py}" y2="${py}" stroke="${color}" stroke-dasharray="4 5" stroke-width=".9"/><text x="${W-R+7}" y="${py+3}" fill="${color}" font-size="8">${escapeHTML(limit.label)} ${limit.value}</text>`;
  }
  markup+='<g clip-path="url(#plot-clip)">';
  const band=forecast.filter(p=>p.lower!==null&&p.upper!==null);
  if(band.length>1){
    markup+=`<path d="${pathFrom(band,x,y,"upper")} ${[...band].reverse().map(p=>`L${x(p.time).toFixed(2)},${y(p.lower).toFixed(2)}`).join(" ")} Z" fill="url(#future-area)"/>`;
  }
  if(history.length>1)markup+=`<path d="${pathFrom(history,x,y)}" fill="none" stroke="#008980" stroke-width="2.1" stroke-linejoin="round" stroke-linecap="round"/>`;
  if(forecast.length){
    const predicted=history.length?[{time:now,value:history[history.length-1].value},...forecast]:forecast;
    markup+=`<path d="${pathFrom(predicted,x,y)}" fill="none" stroke="#5197b9" stroke-width="1.9" stroke-dasharray="5 4" stroke-linejoin="round"/>`;
  }
  markup+='</g>';
  if(history.length){const end=history[history.length-1];markup+=`<circle cx="${x(end.time)}" cy="${y(end.value)}" r="3.3" fill="#008980" stroke="white" stroke-width="1.5"/>`;}
  if(forecast.length){
    markup+=`<line x1="${x(now)}" x2="${x(now)}" y1="${T}" y2="${H-B}" stroke="#c6d6df" stroke-dasharray="2 3"/><text x="${Math.min(x(now)+7,W-R-54)}" y="${T+10}" fill="#8ca7b7" font-size="8">예측 시작</text>`;
  }
  markup+=`<text x="${L}" y="10" fill="#9babb3" font-size="8">${escapeHTML(info.unit)}</text>`;
  svg.innerHTML=markup;
  chartPoints=[...history,...forecast];chartGeometry={x,y,W,L,R,minX,maxX,field};
  setText("chart-subtitle",`${info.label} · ${info.unit} · ${data.mode==="stream"?"실시간 입력":"모의 계측"}`);
  const horizon=forecast.length?forecast[forecast.length-1].time-now:0;
  setText("chart-footnote",forecast.length?`관측 ${history.length}개 · 미래 ${durationText(horizon)} · ${band.length?"예측 구간 표시":"예측 구간 미산출"}`:`관측 ${history.length}개 · 예측 이력 수집 중`);
}

function render(data){
  currentState=data;
  renderControls(data);renderStatus(data.analysis?.state||"unknown");renderMetrics(data);renderForecastSummary(data);renderEvidence(data);renderEvents(data);renderVibration(data);renderChart(data);
  renderRecording(data);
  setText("last-updated",`마지막 수신 ${new Date().toLocaleTimeString("ko-KR",{hour12:false})}`);
}

function renderRecording(data){
  const recording=data.recording,element=$("recording-status");
  element.classList.toggle("hidden",!recording||typeof recording!=="object");
  if(!recording||typeof recording!=="object")return;
  element.classList.toggle("recording-error",Boolean(recording.error));
  element.title=recording.error?String(recording.error):"";
  element.textContent=recording.error?"기록 저장 오류 · 상세 사유 확인":recording.enabled?`기록 저장 중${number(recording.written)!==null?` · ${fmt(recording.written,0)}건`:""}`:"기록 저장 꺼짐";
}

async function poll(){
  try{const data=await api("/api/state");if(!data||typeof data!=="object"||!data.analysis)throw new Error("분석 데이터 형식이 올바르지 않습니다.");setConnection(true);render(data);}
  catch(error){setConnection(false,error.name==="AbortError"?"응답 시간이 초과되었습니다.":error.message);}
  finally{setTimeout(poll,500);}
}

async function control(body){
  if(requestBusy)return;
  requestBusy=true;if(currentState)renderControls(currentState);
  try{await api("/api/control",body);const data=await api("/api/state");setConnection(true);render(data);}
  catch(error){showToast(`제어 요청 실패: ${error.message}`);}
  finally{requestBusy=false;if(currentState)renderControls(currentState);}
}

function selectView(view){
  if(!["dashboard","analysis","vibration","evaluation"].includes(view))view="dashboard";
  activeView=view;
  document.querySelectorAll(".view").forEach(el=>el.classList.toggle("active",el.id===`view-${view}`));
  document.querySelectorAll(".nav-item").forEach(el=>el.classList.toggle("active",el.dataset.view===view));
  setText("page-title",({dashboard:"운전 대시보드",analysis:"예측·판정 근거",vibration:"진동 센서",evaluation:"모델 검증"})[view]);
  if(location.hash!==`#${view}`)history.replaceState(null,"",`#${view}`);
  if(view==="evaluation"&&!evaluationLoaded)loadEvaluation();
}

const evaluationLabels={
  telemetry:"계측 모델 검증",vibration:"진동 모델 검증",training:"학습 정보",model:"모델",model_name:"모델 이름",model_type:"모델 유형",model_id:"모델 식별자",algorithm:"알고리즘",method:"방법",description:"설명",note:"참고",notes:"참고 사항",status:"상태",source:"데이터 출처",dataset:"데이터셋",dataset_name:"데이터셋 이름",data_source:"데이터 출처",samples:"표본 수",n_samples:"표본 수",train_samples:"학습 표본 수",test_samples:"평가 표본 수",validation_samples:"검증 표본 수",train_size:"학습 데이터 수",test_size:"평가 데이터 수",training_rows:"학습 행 수",test_rows:"평가 행 수",train_rows:"학습 행 수",n_train:"학습 표본 수",n_test:"평가 표본 수",features:"입력 특징",feature_names:"특징 이름",feature_count:"특징 수",n_features:"특징 수",target:"예측 대상",targets:"예측 대상",seed:"난수 시드",random_state:"난수 시드",split:"데이터 분할",split_method:"분할 방법",test_split:"평가 분할",holdout:"분리 평가",holdout_ratio:"평가 데이터 비율",accuracy:"정확도",balanced_accuracy:"균형 정확도",precision:"정밀도",recall:"재현율",f1:"F1 점수",f1_score:"F1 점수",macro_f1:"매크로 F1",roc_auc:"ROC AUC",auc:"AUC",rmse:"RMSE",mae:"평균 절대 오차",mse:"평균 제곱 오차",r2:"결정계수",confusion_matrix:"혼동 행렬",classification_report:"분류 평가",classes:"클래스",labels:"라벨",support:"표본 수",threshold:"판정 기준",anomaly_threshold:"이상 탐지 기준",metrics:"평가 지표",results:"평가 결과",per_scenario:"시나리오별 결과",scenarios:"시나리오",scenario:"시나리오",scenario_id:"시나리오",forecast:"예측 평가",forecast_mae:"예측 평균 절대 오차",forecast_rmse:"예측 RMSE",forecast_metrics:"예측 지표",anomaly:"이상 탐지",anomaly_detection:"이상 탐지",detection:"탐지 평가",false_positive_rate:"오탐 비율",false_alarm_rate:"오경보 비율",detection_rate:"탐지 비율",detection_delay_s:"탐지 지연 (초)",first_detection_s:"최초 탐지 (초)",lead_time_s:"선행 탐지 시간 (초)",forecast_horizon_s:"예측 범위 (초)",window_s:"분석 창 (초)",window_seconds:"분석 창 (초)",history_seconds:"관측 이력 (초)",duration_s:"기간 (초)",n_estimators:"모델 수",contamination:"오염 비율 설정",limitations:"검증 한계",caveats:"해석 유의사항",created_at:"생성 시각",trained_at:"학습 시각",evaluation_date:"평가 시각",version:"버전",rows:"행 수",count:"개수",total:"전체",correct:"정분류",incorrect:"오분류",passed:"통과",failed:"미통과",normal:"정상",inspection:"점검",urgent:"긴급",unknown:"미확정",collecting:"수집 중",train:"학습",test:"평가",validation:"검증",feature_importances:"특징 중요도",forecast_model:"예측 모델",anomaly_model:"이상 탐지 모델",classifier:"분류기",training_scenarios:"학습 시나리오",evaluation_scenarios:"평가 시나리오",held_out_scenarios:"분리 평가 시나리오",synthetic:"모의 데이터",recorded:"실측 기록",live:"실시간",units:"단위",baseline:"기준선",mean:"평균",std:"표준편차",min:"최솟값",max:"최댓값",median:"중앙값",p95:"95백분위",sample_rate_hz:"샘플링 주파수 (Hz)",sampling_hz:"샘플링 주파수 (Hz)",recordings:"기록",recording_count:"기록 수",files:"파일",file:"파일",pattern:"패턴",pattern_id:"패턴 식별자",pattern_labels:"패턴 라벨",signal_features:"신호 특징",rms_vector_g:"벡터 RMS (g)",envelope_cv:"포락선 변동계수",time_split:"시간 기준 분할",fit_samples:"학습 표본 수",heldout_samples:"분리 평가 표본 수",training_seconds:"학습 시간 (초)",normal_false_alarm_rate:"정상 구간 오경보율",healthy_false_alarm_rate:"정상 구간 오경보율",mae_by_field:"항목별 평균 절대 오차",rmse_by_field:"항목별 RMSE",recording_ids:"실측 기록 식별자",normal_training_only:"정상 데이터만 학습",profile:"장비 프로필",runtime:"실행 정보",warnings:"해석 주의",checks:"검증 항목",evaluation:"평가",scope:"평가 범위"
};
Object.assign(evaluationLabels,{
  per_horizon:"예측 시간별 평가",by_scenario_mean_across_horizons:"시나리오별 평균 오차",learned_mlp:"학습 예측 모델",last_value:"직전값 유지",recent_linear_30s:"최근 30초 기울기",test_runs:"분리 평가 운전 수",test_windows:"평가 구간 수",coverage:"예측 구간의 관측값 포함률",unannounced_intervention_windows:"예고 없는 상태변화가 포함된 구간",interpretation:"해석 범위",schema_version:"자료 형식 버전",feature_contract:"입력 특징 정의",split_policy:"데이터 분할 정책",splits:"분할별 자료",epochs:"학습 반복 수",selected_epoch:"선택된 학습 반복",selection_criterion:"모델 선택 기준",training_log:"학습 기록",model_sha256:"모델 파일 SHA-256",model_bytes:"모델 파일 크기 (바이트)",parameter_count:"학습 매개변수 수",export_max_abs_error_vs_torch:"모델 변환 최대 절대 오차",numpy_inference_ms:"현재 환경 추론 시간 (ms)",uno_q_measured:"UNO Q에서 측정했는가",not_proven:"아직 검증하지 않은 항목",fit:"학습",calibration:"구간 보정",fit_windows:"학습 구간 수",calibration_windows:"보정 구간 수",fit_cycles:"학습 반복 실험",calibration_cycles:"보정 반복 실험",test_recordings:"평가 녹화 수",test_used_for_training_or_radius:"평가 자료를 학습 또는 거절 기준에 사용했는가",generator_health_evaluated:"발전기 고장 진단을 평가했는가",growing_pattern_training_examples:"상승 패턴 학습 사례 수",uno_q_model_execution_measured:"UNO Q 실행 성능을 측정했는가",evaluation_scope:"평가 범위",models:"모델별 결과",provenance:"원본 기록 정보",rms_only:"RMS만 사용",rms_and_pattern:"RMS + 시간 패턴",repeat_calibration:"반복 기록 보정",random_with_unknown_rejection:"별도 순서 기록 평가 · 미확정 거절 사용",random_forced_choice_comparator:"비교군 · 강제 분류",fraction_correct_all_windows:"전체 구간 정분류 비율",macro_recall_all_windows:"전체 구간 매크로 재현율",accepted_count:"분류 수락 구간 수",fraction_correct_accepted:"수락 구간 정분류 비율",confusion_rows:"혼동 행렬 행 (실제 라벨)",confusion_columns:"혼동 행렬 열 (예측 라벨)",confusion:"혼동 행렬",unknown_counts_as_incorrect_in_overall_metric:"전체 평가에서 미확정을 오분류로 계산",rejection_enabled:"미확정 거절 사용",runs:"운전 기록",run_id:"운전 식별자",frames:"계측 프레임 수",source_file:"원본 파일",sha256:"파일 SHA-256",epoch:"학습 반복",train_standardized_mse:"학습 표준화 MSE",validation_standardized_mse:"검증 표준화 MSE",idle:"정지",increased:"진동 증가",intermittent:"간헐 진동",actual_phone_and_imu_recordings:"실제 휴대폰·IMU 녹화 기록"
});
function labelFor(key){return evaluationLabels[key]||fieldInfo[key]?.label||scenarioNames[key]||key.replaceAll("_"," ");}
function evalScalar(value){if(value===null||value===undefined)return "산출되지 않음";if(typeof value==="boolean")return value?"예":"아니요";if(typeof value==="number")return Number.isInteger(value)?value.toLocaleString("ko-KR"):value.toLocaleString("ko-KR",{maximumFractionDigits:6});return scenarioNames[value]||evaluationLabels[value]||String(value);}
function evaluationContent(value,depth=0){
  if(value===null||typeof value!=="object")return `<div class="evaluation-item"><strong>${escapeHTML(evalScalar(value))}</strong></div>`;
  if(depth>5)return `<div class="evaluation-item"><strong>${escapeHTML(JSON.stringify(value))}</strong></div>`;
  if(Array.isArray(value)){
    if(!value.length)return '<div class="evaluation-item"><strong>항목 없음</strong></div>';
    if(value.every(v=>v===null||typeof v!=="object"))return `<div class="evaluation-item nested"><strong>${value.map(v=>escapeHTML(evalScalar(v))).join(" · ")}</strong></div>`;
    return value.slice(0,100).map((item,index)=>`<div class="evaluation-item nested"><details ${value.length<=5?"open":""}><summary>${escapeHTML(item?.label||scenarioNames[item?.scenario]||item?.name||`항목 ${index+1}`)}</summary><div class="evaluation-content">${evaluationContent(item,depth+1)}</div></details></div>`).join("")+(value.length>100?`<div class="muted-small">총 ${value.length}개 중 앞의 100개를 표시합니다.</div>`:"");
  }
  return Object.entries(value).map(([key,val])=>{
    if(val!==null&&typeof val==="object")return `<div class="evaluation-item nested"><details ${depth<2?"open":""}><summary>${escapeHTML(labelFor(key))}</summary><div class="evaluation-content">${evaluationContent(val,depth+1)}</div></details></div>`;
    return `<div class="evaluation-item"><span>${escapeHTML(labelFor(key))}</span><strong>${escapeHTML(evalScalar(val))}</strong></div>`;
  }).join("");
}
async function loadEvaluation(){
  if(evaluationBusy)return;evaluationBusy=true;$("refresh-evaluation").disabled=true;
  if(!evaluationLoaded)$("evaluation-results").innerHTML='<div class="card empty-state">실제 평가 결과를 불러오는 중입니다.</div>';
  try{
    const data=await api("/api/evaluation");
    evaluationData=data;
    renderEvaluationReport(data);
    evaluationLoaded=true;
  }catch(error){$("evaluation-results").innerHTML=`<div class="card empty-state">검증 결과를 불러오지 못했습니다.<br>${escapeHTML(error.message)}</div>`;evaluationLoaded=false;}
  finally{evaluationBusy=false;$("refresh-evaluation").disabled=false;}
}

function reportDetails(label,value){return `<details class="report-details"><summary>${escapeHTML(label)}</summary><div class="evaluation-content">${evaluationContent(value)}</div></details>`;}
function reportStat(label,value){return `<div class="analysis-stat"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`;}
function renderEvaluationReport(data){
  const t=data.telemetry,v=data.vibration,tr=data.training;
  if(!t?.per_horizon||!tr){$("evaluation-results").innerHTML=Object.entries(data).map(([key,value])=>`<article class="card evaluation-group"><h2>${escapeHTML(labelFor(key))}</h2><div class="evaluation-content">${evaluationContent(value)}</div></article>`).join("");return;}
  const horizons=Object.keys(t.per_horizon.learned_mlp||{}).sort((a,b)=>Number(a)-Number(b));
  const telemetry=`<article class="card evaluation-group telemetry-report"><div class="card-heading"><div><h2>미래 계측값 예측 · 분리 평가 결과</h2><p>같은 시뮬레이터에서 생성한 별도 운전 기록으로 평가</p></div><span class="tiny-pill">모의 데이터</span></div><div class="analysis-stats">${reportStat("평가 운전 기록",fmt(t.test_runs,0)+"회")}${reportStat("평가 구간",fmt(t.test_windows,0)+"개")}${reportStat("예고 없는 변화 포함",fmt(t.unannounced_intervention_windows,0)+"개")}</div><div class="report-table-heading"><h3>예측 방법별 평균 절대 오차 (MAE)</h3><label>예측 시점 <select id="evaluation-horizon">${horizons.map(h=>`<option value="${escapeHTML(h)}" ${h===horizons[horizons.length-1]?"selected":""}>${escapeHTML(h)}초 후</option>`).join("")}</select></label></div><div class="table-scroll"><table class="forecast-eval-table"><thead><tr><th>측정 항목 · 단위</th><th>학습 모델</th><th>직전값 유지</th><th>최근 기울기</th><th>예측 구간 포함률</th></tr></thead><tbody id="forecast-eval-body"></tbody></table></div><p class="report-explanation">MAE는 예측값과 관측값의 평균 차이로, 작을수록 좋습니다. 포함률은 관측값이 예측 구간 안에 들어간 비율이며, 고장 진단 정확도가 아닙니다.</p><div class="inline-note">이 결과는 실제 발전기와 실제 고장으로 검증한 성능이 아닙니다. 예고 없이 시작되는 고장·조작·정비를 관측 전에 알 수는 없습니다.</div>${reportDetails("전체 계측 검증 결과 펼치기",t)}</article>`;
  let vibration="";
  if(v){
    const rows=[["rms_only","RMS만 사용"],["rms_and_pattern","RMS + 시간 패턴"]].map(([key,label])=>{
      const r=v.models?.[key]?.random_with_unknown_rejection;
      return r?`<tr><td>${label}</td><td>${fmt(r.correct,0)} / ${fmt(r.count,0)}</td><td>${fmt(r.unknown,0)}</td></tr>`:"";
    }).join("");
    vibration=`<article class="card evaluation-group"><div class="card-heading"><div><h2>진동 패턴 분류</h2><p>별도 순서로 녹화한 휴대폰·IMU 기록</p></div><span class="tiny-pill">실측 기록</span></div><div class="analysis-stats">${reportStat("평가 녹화",fmt(v.test_recordings,0)+"개")}${reportStat("평가 구간",fmt(v.test_windows,0)+"개")}</div><div class="table-scroll"><table><thead><tr><th>입력 특징</th><th>정분류 / 전체</th><th>미확정</th></tr></thead><tbody>${rows||'<tr><td colspan="3">산출된 비교 결과 없음</td></tr>'}</tbody></table></div><p class="report-explanation">같은 녹화에서 잘라낸 구간은 서로 연관될 수 있습니다. 구간 수를 독립적인 실험 횟수로 해석하지 않습니다.</p><div class="inline-note">휴대폰 진동 패턴을 분류한 결과이며, 발전기의 고장 원인이나 잔여수명은 검증하지 않았습니다.</div>${reportDetails("진동 평가 범위와 세부 결과",v)}</article>`;
  }
  const splitRows=Object.entries(tr.splits||{}).map(([key,val])=>`<tr><td>${escapeHTML(labelFor(key))}</td><td>${Array.isArray(val.runs)?fmt(val.runs.length,0):"—"}</td></tr>`).join("");
  const training=`<article class="card evaluation-group"><div class="card-heading"><div><h2>학습 및 재현 정보</h2><p>서버에 저장된 학습 산출물</p></div><span class="tiny-pill">학습 완료 모델</span></div><div class="analysis-stats">${reportStat("학습 매개변수",fmt(tr.parameter_count,0))}${reportStat("선택 반복 / 전체",`${fmt(tr.selected_epoch,0)} / ${fmt(tr.epochs,0)}`)}</div><div class="table-scroll"><table><thead><tr><th>데이터 용도</th><th>운전 기록 수</th></tr></thead><tbody>${splitRows}</tbody></table></div><p class="report-explanation">운전 기록 단위로 학습·검증·구간 보정·평가를 분리합니다. 같은 운전의 겹치는 분석 창이 서로 다른 분할에 들어가지 않게 합니다.</p><div class="inline-note">학습에는 시나리오 이름이나 미래 관측값을 입력 특징으로 사용하지 않습니다. UNO Q의 자원 사용량과 실행 속도는 별도 장비 검증이 필요합니다.</div>${reportDetails("학습 구조·원본 식별자·반복별 기록",tr)}</article>`;
  $("evaluation-results").innerHTML=telemetry+vibration+training;
  $("evaluation-horizon").addEventListener("change",renderForecastEvaluationTable);
  renderForecastEvaluationTable();
}
function renderForecastEvaluationTable(){
  const t=evaluationData?.telemetry,h=$("evaluation-horizon")?.value;
  if(!t||!h)return;
  const learned=t.per_horizon?.learned_mlp?.[h]||{},held=t.per_horizon?.last_value?.[h]||{},linear=t.per_horizon?.recent_linear_30s?.[h]||{};
  $("forecast-eval-body").innerHTML=Object.entries(learned).map(([field,report])=>{
    const info=infoFor(field),digits=info.unit==="kgf/cm²"||info.unit==="Hz"||info.unit==="bar"?3:2;
    const coverage=number(t.coverage?.[h]?.[field]);
    return `<tr><td>${escapeHTML(info.label)} <span class="muted-small">${escapeHTML(info.unit)}</span></td><td class="learned-result">${fmt(report.mae,digits)}</td><td>${fmt(held[field]?.mae,digits)}</td><td>${fmt(linear[field]?.mae,digits)}</td><td>${coverage===null?"—":fmt(coverage*100,1)+" %"}</td></tr>`;
  }).join("");
}

$("play").addEventListener("click",()=>control({action:currentState?.playing?"pause":"start"}));
$("reset").addEventListener("click",()=>control({action:"reset"}));
$("speed").addEventListener("change",event=>control({action:"speed",speed:Number(event.target.value)}));
$("scenario").addEventListener("change",event=>control({action:"scenario",scenario:event.target.value}));
$("chart-field").addEventListener("change",()=>{if(currentState)renderChart(currentState);});
document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>selectView(button.dataset.view)));
document.querySelector(".brand").addEventListener("click",()=>selectView("dashboard"));
$("refresh-evaluation").addEventListener("click",loadEvaluation);
document.querySelectorAll("[data-vibration-mode]").forEach(button=>button.addEventListener("click",async()=>{
  const mode=button.dataset.vibrationMode;
  const port=Number($("imu-port").value);
  if(mode==="live"&&(!Number.isInteger(port)||port<1024||port>65535)){showToast("유효한 센서 수신 포트를 입력하세요.");return;}
  document.querySelectorAll("[data-vibration-mode]").forEach(b=>b.disabled=true);
  try{const result=await api("/api/vibration",{mode,port,profile_confirmed:$("profile-confirmed").checked});setText("vibration-message",result.message||"진동 입력 설정이 적용되었습니다. 유효한 데이터 수신 상태를 확인하세요.");showToast("진동 입력 설정을 적용했습니다.");}
  catch(error){setText("vibration-message",`연결 설정 실패: ${error.message}`);showToast(error.message);}
  finally{document.querySelectorAll("[data-vibration-mode]").forEach(b=>b.disabled=false);}
}));
$("forecast-chart").addEventListener("pointermove",event=>{
  if(!chartPoints.length||!chartGeometry)return;
  const rect=event.currentTarget.getBoundingClientRect(),g=chartGeometry,px=(event.clientX-rect.left)/rect.width*g.W;
  const t=g.minX+(px-g.L)/(g.W-g.L-g.R)*(g.maxX-g.minX);
  let closest=chartPoints[0];for(const p of chartPoints)if(Math.abs(p.time-t)<Math.abs(closest.time-t))closest=p;
  const tip=$("chart-tooltip");tip.innerHTML=`${closest.kind==="future"?"예측":"관측"} · ${escapeHTML(timeText(closest.time))}<br><strong>${escapeHTML(formatField(g.field,closest.value))}</strong>${closest.lower!==null&&closest.lower!==undefined&&closest.upper!==null&&closest.upper!==undefined?`<br>구간 ${escapeHTML(fmt(closest.lower,infoFor(g.field).digits))}–${escapeHTML(fmt(closest.upper,infoFor(g.field).digits))}`:""}`;
  tip.classList.remove("hidden");tip.style.left=`${Math.max(0,Math.min(event.clientX-rect.left+12,rect.width-155))}px`;tip.style.top=`${Math.max(0,event.clientY-rect.top-65)}px`;
});
$("forecast-chart").addEventListener("pointerleave",()=>$("chart-tooltip").classList.add("hidden"));
window.addEventListener("hashchange",()=>selectView(location.hash.slice(1)));
renderMetrics({latest:{}});selectView(location.hash.slice(1)||"dashboard");poll();
