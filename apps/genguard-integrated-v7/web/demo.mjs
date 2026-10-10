import {GenGuardClient} from './client.mjs';
import {createTrendUI} from './trend.mjs';

const $ = id => document.getElementById(id);
const trendUi = createTrendUI({get:$,changed:()=>updateButtons()});
let client = null, config = null, active = false, working = false, auto = false, timer = null;
let lastResult = null, receivedAt = 0, connectionFailed = false, imu = null, imuReceivedAt = 0;
const seenEvents = new Set();
const labels = {engine_rpm:'엔진 RPM',coolant_c:'냉각수 온도',oil_bar:'윤활유 압력',oil_kgf_cm2:'윤활유 압력',voltage_v:'선간전압',voltage_ll_v:'선간전압',vibration_rms_g:'진동 RMS',envelope_cv:'진동 간헐성',load_pct:'부하율',ambient_c:'주변 온도'};
const statusNames = {normal:'학습 기준 내',watch:'점검 필요',urgent:'긴급 경고',unknown:'판단 불가',inactive:'운전 판단 대기'};
const sourceNames = {live_imu:'IMU 실측',recorded_imu:'IMU 기록 재생',scenario:'모의',unknown:'출처 미확인'};
const imuNames = {waiting:'수신 대기',fresh:'최근 창 수신 중',stale:'수신 지연 · 이전 값',invalid:'센서 품질 확인 필요'};
const calibrationNames = {untrained:'미학습',collecting:'기준 수집 중',ready:'기준 등록됨',failed:'기준 등록 실패'};
const stateNames = {change:'변화 관찰 · 연속 확인 중',within_baseline:'학습 기준 내',change_observed:'변화 관측 · 연속 확인 중',persistent_change:'지속 변화 · 점검',unknown:'판단 불가',untrained:'기준 미학습',collecting:'정상 기준 수집 중',inactive:'운전 판단 대기',insufficient_data:'새 센서 창 대기'};
const fmt = value => typeof value === 'number' ? new Intl.NumberFormat('ko-KR', {maximumFractionDigits:4}).format(value) : '—';
const sourceKey = value => typeof value === 'string' ? value : value?.source || 'unknown';
const scenarioFields = () => (config?.fields || []).filter(field => field.editable === true && field.source === 'scenario');
$('api-url').value = location.origin;

function updateButtons() {
  const unresolved = Boolean(client?.pendingSample), busy = working || Boolean(client?.busy);
  trendUi.updateConnection(active,busy);
  $('connect-button').disabled = active || busy;
  $('connect-button').textContent = client && !active ? '연결 재시도' : '연결 · 시험 시작';
  $('close-button').disabled = !active || busy;
  $('api-url').disabled = Boolean(client); $('api-token').disabled = Boolean(client);
  $('send-button').disabled = !active || busy || auto || unresolved;
  $('auto-button').disabled = !active || (busy && !auto) || unresolved;
  $('auto-button').textContent = auto ? '자동 분석 정지' : '2초마다 자동 분석';
  $('retry-button').disabled = !active || busy || !unresolved;
  $('calibrate-button').disabled = !active || busy || unresolved || currentImuState() !== 'fresh' || imu?.calibration?.state === 'collecting' || !$('normal-confirmation').checked;
  $('normal-confirmation').disabled = !active || busy || imu?.calibration?.state === 'collecting';
  for (const input of $('fields').querySelectorAll('input')) input.disabled = !active || busy || unresolved;
  for (const button of $('presets').querySelectorAll('button')) button.disabled = !active || busy || unresolved;
}
function setError(error) {
  connectionFailed = true; stopAuto();
  $('error-box').hidden = false;
  $('error-box').textContent = `${error.message}${error.status ? ` (HTTP ${error.status})` : ''}`;
  $('connection-state').textContent = '응답 확인 필요'; renderAge();
}
function clearError() { connectionFailed = false; $('error-box').hidden = true; $('error-box').textContent = ''; }
function stopAuto() { auto = false; clearTimeout(timer); timer = null; updateButtons(); }
function showFields() {
  $('fields').replaceChildren();
  for (const field of scenarioFields()) {
    const label = document.createElement('label'), heading = document.createElement('span'), name = document.createElement('span'), unit = document.createElement('span'), input = document.createElement('input');
    heading.className = 'field-heading'; name.textContent = field.label; unit.textContent = field.unit; unit.className = 'unit';
    input.type = 'number'; input.id = `field-${field.key}`; input.name = field.key; input.required = true; input.step = field.step ?? 'any';
    if (field.min !== undefined) input.min = field.min; if (field.max !== undefined) input.max = field.max;
    input.value = config.default_values[field.key] ?? field.default;
    heading.append(name,unit); label.append(heading,input); $('fields').append(label);
  }
}
function getValues() {
  return Object.fromEntries(scenarioFields().map(field => {
    const raw = $(`field-${field.key}`).value, value = Number(raw);
    if (raw === '' || !Number.isFinite(value) || (field.min !== undefined && value < field.min) || (field.max !== undefined && value > field.max)) throw new Error(`${field.label} 입력값과 허용 범위를 확인하세요.`);
    return [field.key,value];
  }));
}
function setSourceBadge(element, source, mini = false) {
  const key = sourceKey(source), known = Object.hasOwn(sourceNames,key) ? key : 'unknown';
  element.className = `${mini ? 'mini-source' : 'badge source'} ${known}`;
  element.textContent = sourceNames[known];
}
function currentImuState() {
  if (!imu) return 'waiting';
  const age = (typeof imu.age_s === 'number' ? imu.age_s : 0) + (Date.now()-imuReceivedAt)/1000;
  if (imu.state === 'fresh' && (!active || connectionFailed || age > 5)) return 'stale';
  return imu.state;
}
function renderImu(next) {
  if (next) { imu = next; imuReceivedAt = Date.now(); }
  const state = currentImuState(), calibration = imu?.calibration || {state:'untrained',count:0,required:15};
  document.querySelector('.imu-card').dataset.state = state;
  setSourceBadge($('imu-source'),imu?.source);
  if (!imu?.source) $('imu-source').textContent = '센서 미연결';
  $('imu-state').textContent = imuNames[state] || state;
  $('imu-rms').textContent = fmt(imu?.features?.vibration_rms_g); $('imu-cv').textContent = fmt(imu?.features?.envelope_cv);
  const age = typeof imu?.age_s === 'number' ? imu.age_s + (Date.now()-imuReceivedAt)/1000 : null;
  $('imu-detail').textContent = state === 'waiting' ? '기기에서 IMU 데이터를 수신하지 못했습니다. 수신기를 실행하고 센서 연결을 확인하세요.' : `${age === null ? '' : `센서 창 ${fmt(age)}초 전 · `}${state === 'fresh' ? '웹에서 바꿀 수 없는 센서 입력입니다.' : '아래 진동값은 현재 정상 판정에 사용하지 않습니다.'}`;
  $('installation-id').textContent = imu?.installation_id || '—'; $('window-id').textContent = imu?.window_id ?? '—';
  $('calibration-state').textContent = calibrationNames[calibration.state] || calibration.state;
  $('calibration-progress').max = calibration.required || 15; $('calibration-progress').value = calibration.count || 0;
  $('calibration-note').textContent = calibration.state === 'collecting' ? `고유 센서 창 ${calibration.count ?? 0} / ${calibration.required ?? 15}개 수집 중입니다. 진동과 부착 상태를 유지하세요.` : calibration.state === 'ready' ? `현재 부착 상태의 기준이 등록되었습니다. 기준 ID: ${calibration.baseline_id || '—'}` : calibration.state === 'failed' ? `기준 등록 실패: ${calibration.error || '센서 입력과 진동 상태를 확인하세요.'}` : 'IMU를 고정하고 정상으로 사용할 진동을 유지한 뒤 기준을 등록하세요.';
}
function renderAge() {
  const age = receivedAt ? (Date.now() - receivedAt) / 1000 : null;
  $('sample-age').textContent = age === null ? '미수신' : `마지막 응답 ${Math.floor(age)}초 전`;
  const stale = age !== null && age > 5;
  const unavailable = !active || connectionFailed || stale;
  const imuUnavailable = currentImuState() !== 'fresh' || imu?.calibration?.state !== 'ready';
  const imuChanged = lastResult?.imu && (lastResult.imu.source !== imu?.source || lastResult.imu.stream_id !== imu?.stream_id || lastResult.imu.installation_id !== imu?.installation_id);
  let status = unavailable ? 'unknown' : lastResult?.status || 'unknown';
  if (status === 'normal' && (imuUnavailable || imuChanged)) status = 'unknown';
  $('status-card').dataset.status = status;
  $('status-text').textContent = !lastResult && !connectionFailed ? '판단 대기' : statusNames[status] || status;
  if (!unavailable && status === 'normal' && lastResult?.ai?.state === 'change_observed') $('status-text').textContent = '변화 관찰 · 확인 중';
  if (!active) $('status-detail').textContent = lastResult ? '시험이 종료되었습니다. 아래에 마지막 분석과 이력을 보관합니다.' : '연결 후 정상 진동 기준을 등록하고 값을 보내세요.';
  else if (connectionFailed) $('status-detail').textContent = '통신 결과가 확인되지 않아 현재 상태를 정상으로 표시하지 않습니다. 아래 결과는 마지막 수신 기록입니다.';
  else if (stale) $('status-detail').textContent = '5초 이상 새 분석 응답이 없습니다. 아래 분석은 이전 기록이며 현재 상태를 보장하지 않습니다.';
  else if (lastResult?.status === 'normal' && imuChanged) $('status-detail').textContent = '센서 출처나 부착 상태가 바뀌었습니다. 새 정상 기준과 분석 결과를 확인하세요.';
  else if (lastResult?.status === 'normal' && imuUnavailable) $('status-detail').textContent = '센서 수신 또는 정상 기준이 준비되지 않아 현재 상태를 정상으로 판단하지 않습니다.';
  else if (lastResult) $('status-detail').textContent = lastResult.status_detail || lastResult.ai?.summary || '서버에서 보내온 최신 결과입니다.';
  else $('status-detail').textContent = '정상 진동 기준을 등록하고 첫 값을 보내세요. 센서 미연결·기준 미학습은 정상으로 판단하지 않습니다.';
  renderImu();
  trendUi.renderLive(lastResult?.trend,{stale:unavailable,age_s:age});
}
function renderComponent(name, component) {
  const value = component || {};
  $(`${name}-score`).textContent = fmt(value.score);
  $(`${name}-threshold`).textContent = fmt(value.threshold);
  $(`${name}-streak`).textContent = `${value.change_streak ?? 0}개 창`;
  $(`${name}-state`).textContent = value.persistent_change ? '지속 변화 · 점검 필요' : stateNames[value.state] || value.state || '미분석';
}
function clearAnalysisPanels(message) {
  trendUi.renderLive(null);
  $('ai-label').textContent = '새 분석 대기'; $('ai-summary').textContent = message;
  renderComponent('generator',null); renderComponent('vibration',null);
  setSourceBadge($('vibration-source'),imu?.source,true);
  $('elapsed').textContent = '—'; $('seq').textContent = '—'; $('checks').replaceChildren();
  const row = document.createElement('tr'), cell = document.createElement('td');
  cell.colSpan = 5; cell.className = 'muted'; cell.textContent = '새 분석을 수신하면 근거가 표시됩니다.';
  row.append(cell); $('evidence').replaceChildren(row);
  $('fixed-alerts').textContent = '새 분석 대기 · 이전 경고는 알림 기록에서 확인하세요.';
}
function renderResult(result) {
  // A retried response confirms a past analysis; receipt time must not make it fresh.
  const analysisAge = typeof result.analysis_age_s === 'number' && Number.isFinite(result.analysis_age_s) && result.analysis_age_s >= 0 ? result.analysis_age_s : result.duplicate ? 6 : 0;
  lastResult = result; receivedAt = Date.now() - analysisAge * 1000; clearError();
  $('connection-state').textContent = 'API 응답 수신';
  $('elapsed').textContent = `${fmt(result.elapsed_s)}초`; $('seq').textContent = result.seq;
  const ai = result.ai || {}, comparison = ai.current_comparison || {};
  $('model-id').textContent = ai.model_id || config.model_id || '—';
  $('ai-label').textContent = ai.persistent_change ? '지속 변화 · 점검' : stateNames[ai.state] || ai.state || '판단 대기 / 불가';
  $('ai-summary').textContent = ai.summary || '분석 결과를 확인할 수 없습니다.';
  renderComponent('generator',ai.components?.generator); renderComponent('vibration',ai.components?.vibration);
  const displayedImu = result.imu ? {...result.imu, age_s: typeof result.imu.age_s === 'number' ? result.imu.age_s + analysisAge : null} : null;
  renderImu(displayedImu); setSourceBadge($('vibration-source'),result.imu?.source,true);
  $('evidence').replaceChildren();
  for (const item of comparison.evidence || []) {
    const row = document.createElement('tr');
    const source = sourceKey(item.source || result.provenance?.[item.field]);
    for (const [index,value] of [labels[item.field] || item.field,sourceNames[source] || '출처 미확인',fmt(item.value),fmt(item.expected),item.unit].entries()) {
      const cell = document.createElement('td');
      if (index === 1) { const badge = document.createElement('span'); setSourceBadge(badge,source,true); cell.append(badge); }
      else cell.textContent = value;
      row.append(cell);
    }
    $('evidence').append(row);
  }
  if (!$('evidence').children.length) { const row = document.createElement('tr'), cell = document.createElement('td'); cell.colSpan = 5; cell.className = 'muted'; cell.textContent = '표시할 변화 근거가 없습니다. 센서 연결과 정상 기준 상태도 확인하세요.'; row.append(cell); $('evidence').append(row); }
  $('checks').replaceChildren();
  for (const check of ai.checks || []) { const li = document.createElement('li'); li.textContent = check; $('checks').append(li); }
  $('fixed-alerts').replaceChildren();
  const fixedAlerts = result.fixed?.alerts || [];
  for (const alert of fixedAlerts) {
    const item = document.createElement('div'); item.className = `alert-item ${alert.severity === 'urgent' ? 'urgent' : ''}`;
    item.textContent = alert.title || alert.message || alert.id || alert.code || '규칙 경고';
    const note = document.createElement('small'); note.textContent = alert.held ? '이전 경고 유지 · 현재 계측 판단 아님' : alert.message || alert.detail || alert.reason || '발전기 모의 입력에 대한 고정 기준 경고입니다.'; item.append(note); $('fixed-alerts').append(item);
  }
  if (!fixedAlerts.length) $('fixed-alerts').textContent = '현재 활성 고정 규칙 경고 없음';
  for (const event of result.notification_events || []) {
    if (!event.id || seenEvents.has(event.id)) continue;
    if (seenEvents.size === 0) $('notification-log').replaceChildren(); seenEvents.add(event.id);
    const li = document.createElement('li'), time = document.createElement('time'), title = document.createElement('span'); time.textContent = `경과 ${fmt(result.elapsed_s)}초`; title.textContent = event.title || event.id; li.append(time,title); $('notification-log').prepend(li);
  }
  renderAge();
}

$('connect-button').addEventListener('click', async () => {
  let startAttempted = false;
  working = true; updateButtons();
  try {
    client ||= new GenGuardClient({baseUrl:$('api-url').value.trim(), token:$('api-token').value.trim()});
    config = await client.getConfig();
    startAttempted = true;
    const session = await client.startSession();
    active = true; lastResult = null; receivedAt = 0; imu = null; imuReceivedAt = 0; seenEvents.clear();
    clearAnalysisPanels('정상 진동 기준을 확인하고 모의 발전기 값을 보내세요.');
    $('session-id').textContent = session.session_id; $('model-id').textContent = config.model_id;
    $('notification-log').replaceChildren(); const note = document.createElement('li'); note.className = 'muted'; note.textContent = '이번 시험의 경고 진입 기록이 여기에 표시됩니다.'; $('notification-log').append(note);
    $('connection-state').textContent = '연결됨 · 첫 입력 대기'; clearError(); showFields(); renderImu(await client.getImu()); renderAge();
    await trendUi.connect(client);
  } catch (error) {
    // Preserve the start ID after an ambiguous POST failure so a retry can recover our session.
    if (!active && (!startAttempted || (error.status >= 400 && error.status < 500))) client = null;
    setError(error);
  }
  finally { working = false; updateButtons(); }
});
$('close-button').addEventListener('click', async () => {
  stopAuto(); working = true; updateButtons();
  try { await client.closeSession(); active = false; trendUi.disconnect(); client = null; $('connection-state').textContent = '시험 종료'; clearError(); renderAge(); }
  catch (error) { setError(error); }
  finally { working = false; updateButtons(); }
});
$('normal-confirmation').addEventListener('change',updateButtons);
$('calibrate-button').addEventListener('click', async () => {
  stopAuto(); working = true; updateButtons();
  try {
    await client.startCalibration({confirmedNormal:$('normal-confirmation').checked});
    lastResult = null; receivedAt = 0; clearError(); $('normal-confirmation').checked = false;
    clearAnalysisPanels('정상 진동 기준을 새로 수집하고 있습니다. 완료 후 다시 분석하세요.');
    renderImu(await client.getImu()); renderAge();
  } catch (error) { setError(error); }
  finally { working = false; updateButtons(); }
});
async function send(retry = false) {
  if (working || client?.busy || !active) return false;
  working = true; updateButtons();
  try {
    const pending = retry ? client.pendingSample : null;
    const values = pending ? pending.values : getValues();
    const result = await client.sendSample(values, pending ? {phase:pending.phase,quality:pending.quality} : {});
    renderResult(result); return true;
  } catch (error) { setError(error); return false; }
  finally { working = false; updateButtons(); }
}
$('send-button').addEventListener('click', () => send());
$('retry-button').addEventListener('click', () => send(true));
$('values-form').addEventListener('submit', event => { event.preventDefault(); if (!auto) send(); });
async function autoTick() {
  if (!auto) return;
  if (working || client?.busy) { timer = setTimeout(autoTick,250); return; }
  const start = performance.now();
  const ok = await send();
  if (auto && ok) timer = setTimeout(autoTick, Math.max(0,2000-(performance.now()-start)));
  else stopAuto();
}
$('auto-button').addEventListener('click', () => { if (auto) stopAuto(); else { auto = true; updateButtons(); autoTick(); } });
async function pollImu() {
  // Analysis responses already include IMU status; poll only when automatic analysis is idle.
  if (!active || !client || working || client.busy || auto) return;
  working = true; updateButtons();
  try { renderImu(await client.getImu()); }
  catch (error) { setError(error); }
  finally { working = false; updateButtons(); }
}
const presets = {
  normal:{values:{},note:'발전기 모의 값만 기본값으로 돌립니다. IMU 진동과 정상 기준은 바꾸지 않습니다.'},
  coolant:{values:{coolant_c:96},note:'모의 냉각수 온도를 96°C로 변경합니다. 실제 IMU 입력과 함께 분석합니다.'},
  oil:{values:{oil_kgf_cm2:1.5},note:'모의 윤활유 압력을 1.5 kgf/cm²로 변경합니다. 모델 입력에서는 bar로 변환됩니다.'},
  joint:{values:{coolant_c:89,oil_kgf_cm2:2.6},note:'모의 수온·유압은 각각 고정 경고 한계 안이지만 평소와 다른 조합입니다. 실제 진동의 변화 여부는 별도 기준과 비교합니다.'},
  overspeed:{values:{engine_rpm:2000,frequency_hz:66.67},note:'RPM 2,000과 모의 주파수 66.67 Hz로 과속 고정 경고를 확인합니다.'},
  fuel:{values:{fuel_pct:10},note:'모의 연료량은 AI 학습 항목이 아닙니다. 10%에서 고정 규칙으로 경고하는지 확인합니다.'}
};
$('presets').addEventListener('click', event => {
  const button = event.target.closest('button[data-preset]'); if (!button || button.disabled) return;
  const preset = presets[button.dataset.preset], values = {...config.default_values,...preset.values};
  for (const field of scenarioFields()) $(`field-${field.key}`).value = values[field.key];
  $('preset-note').textContent = preset.note;
});
setInterval(() => {renderAge();updateButtons();},500);
setInterval(pollImu,2000);
updateButtons();
