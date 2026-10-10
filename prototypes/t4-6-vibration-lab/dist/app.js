/* Live data comes from the UNO Q QWIIC receiver. Replay uses saved recordings. */
const $ = (id) => document.getElementById(id);
const INPUT_SOURCE = 'unoq_qwiic';
const state = { status: null, samples: [], lastId: 0, busy: false, error: '', replay: null, records: [], lastRecording: null };
const axes = [{ key: 'ax_g', color: '#7ae0c6' }, { key: 'ay_g', color: '#85b5f9' }, { key: 'az_g', color: '#f8bd83' }];
const number = (value, digits = 0) => Number.isFinite(value) ? value.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const numeric = (value) => typeof value === 'number' && Number.isFinite(value);
const text = (id, value) => { $(id).textContent = value; };
const timeSeconds = (sample) => sample.elapsed_s;

async function api(path, body) {
  const response = await fetch(path, body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.detail || '요청을 처리하지 못했습니다.');
  return data;
}

function showError(error) {
  state.error = error ? String(error.message || error) : '';
  const deviceError = state.status?.error;
  $('error-banner').hidden = !(state.error || deviceError);
  text('error-banner', state.error || deviceError || '');
}

async function action(fn) {
  if (state.busy) return;
  state.busy = true; showError(''); renderControls();
  try { await fn(); } catch (error) { showError(error); }
  finally { state.busy = false; render(); }
}

function renderControls() {
  const status = state.status || {};
  const recording = !!status.recording;
  const replaying = !!state.replay;
  $('connect').disabled = state.busy || status.connected || recording || replaying;
  $('disconnect').disabled = state.busy || !status.connected;
  $('record-start').disabled = state.busy || replaying || !status.connected || !status.fresh || ! $('record-label').value.trim() || recording;
  $('record-start').hidden = recording;
  $('record-stop').hidden = !recording;
  $('record-stop').disabled = state.busy;
  ['record-label', 'record-condition', 'record-notes'].forEach((id) => { $(id).disabled = recording || replaying; });
  document.querySelectorAll('[data-replay]').forEach((button) => { button.disabled = state.busy || recording || Number(button.dataset.count) === 0; });
  $('replay-bar').hidden = !replaying;
  text('record-badge', recording ? '● 기록 중' : replaying ? '재생 중' : '준비');
  $('record-badge').style.color = recording ? 'var(--danger)' : '';
  if (recording) text('record-message', `${status.recording.label} · ${number(status.recording.samples)}개 샘플 수집 중`);
  else if (replaying) text('record-message', '저장된 기록을 재생 중입니다. 실시간으로 돌아가면 새 기록을 시작할 수 있습니다.');
  else if (status.fresh) text('record-message', $('record-label').value.trim() ? '현재 수신 중인 실제 측정값을 저장합니다.' : '기록 이름을 입력하세요.');
  else text('record-message', '신호 수신 후 이름을 입력하면 기록할 수 있습니다.');
}

function replayWindow() {
  const replay = state.replay;
  if (!replay) return [];
  const endpoint = replay.startedAt + replay.position;
  let lo = 0, hi = replay.samples.length;
  while (lo < hi) { const middle = (lo + hi) >> 1; if (timeSeconds(replay.samples[middle]) <= endpoint) lo = middle + 1; else hi = middle; }
  replay.visibleCount = lo;
  let start = Math.max(0, lo - 18000);
  while (start < lo && timeSeconds(replay.samples[start]) < endpoint - 8) start++;
  return replay.samples.slice(start, lo);
}

function calculateStats(samples) {
  if (!samples.length) return {};
  const end = timeSeconds(samples.at(-1));
  const rmsSamples = samples.filter((sample) => timeSeconds(sample) >= end - 1);
  const rateSamples = samples.filter((sample) => timeSeconds(sample) >= end - 2);
  const means = axes.map(({ key }) => rmsSamples.reduce((sum, sample) => sum + sample[key], 0) / rmsSamples.length);
  const variance = rmsSamples.reduce((sum, sample) => sum + axes.reduce((v, { key }, i) => v + (sample[key] - means[i]) ** 2, 0), 0) / rmsSamples.length;
  const duration = end - timeSeconds(rateSamples[0]);
  let dropped = 0;
  for (let index = 1; index < samples.length; index++) { const gap = samples[index].seq - samples[index - 1].seq; if (gap > 1) dropped += gap - 1; }
  return { sample_rate_hz: duration > 0 ? (rateSamples.length - 1) / duration : null, ac_rms_g: rmsSamples.length >= 2 ? Math.sqrt(variance) : null, dropped };
}

function render() {
  const status = state.status || {};
  const replay = state.replay;
  const fresh = !!status.fresh && !!status.connected;
  const samples = replay ? replayWindow() : state.samples;
  const latest = replay ? samples.at(-1) : fresh ? status.latest || samples.at(-1) : null;
  const stats = replay ? calculateStats(samples) : fresh ? status.stats || {} : {};
  const badge = $('signal-badge');
  let badgeText, badgeClass;
  if (replay) { badgeText = 'REPLAY · 저장된 기록'; badgeClass = 'replay'; }
  else if (fresh) { badgeText = 'LIVE · 실측'; badgeClass = 'live'; }
  else if (status.connected) { badgeText = '수신 대기'; badgeClass = 'stale'; }
  else { badgeText = '미연결'; badgeClass = 'neutral'; }
  badge.className = `badge ${badgeClass}`; badge.replaceChildren(document.createElement('span'), document.createTextNode(badgeText));
  text('signal-heading', replay ? replay.recording.label : '실시간 신호');
  text('metric-rate', number(stats.sample_rate_hz, 1));
  text('metric-rate-note', replay ? 'Hz · 센서 기록 시간 기준' : 'Hz · 실제 수신 기준');
  text('metric-count', replay ? number(replay.visibleCount) : number(status.connected || status.stats?.sample_count > 0 ? status.stats?.sample_count : null));
  text('metric-rms', number(stats.ac_rms_g, 4));
  text('metric-dropped', replay ? number(stats.dropped) : number(status.connected || status.stats?.sample_count > 0 ? status.stats?.dropped : null));
  text('metric-count-note', replay ? `전체 ${number(replay.samples.length)}개 중` : '연결 후 누적');
  text('metric-dropped-note', replay ? '최근 8초 · 순번 간격' : '순번 간격 기준');
  text('readings-source', replay ? '저장된 기록 재생' : fresh ? '실시간 실측' : '실측 대기');
  ['ax', 'ay', 'az', 'gx', 'gy', 'gz'].forEach((axis) => text(`${axis}-value`, number(latest?.[`${axis}_${axis.startsWith('a') ? 'g' : 'dps'}`], 3)));
  text('connection-badge', status.connected ? fresh ? '수신 중' : '연결됨 · 대기' : '미연결');
  $('connection-badge').style.color = fresh ? 'var(--teal)' : '';
  const gateway = status.connected && status.source === INPUT_SOURCE ? status.gateway : null;
  text('device-board', gateway?.board || status.board || '미확인');
  text('device-sensor', status.sensor || '미확인');
  text('device-odr', numeric(status.meta?.odr_hz) ? `${number(status.meta.odr_hz)} Hz` : '—');
  text('gateway-sensor', gateway?.sensor_ready === true ? '준비됨' : gateway?.sensor_ready === false ? '준비되지 않음' : '미확인');
  text('gateway-led', gateway?.led_ready === true ? '준비됨' : gateway?.led_ready === false ? '준비되지 않음' : '미확인');
  if (status.connected) {
    text('connection-message', fresh ? 'UNO Q · QWIIC IMU → USB · 실제 데이터 수신 중' : gateway?.sensor_ready === false ? 'UNO Q가 연결되었습니다. QWIIC 센서 연결을 확인하세요.' : status.last_sample_age_s == null ? 'UNO Q가 연결되었습니다. IMU 측정 데이터를 기다립니다.' : `마지막 수신 ${number(status.last_sample_age_s, 1)}초 전 · UNO Q의 USB 연결과 수신기를 확인하세요.`);
  } else text('connection-message', state.error || status.error ? 'UNO Q의 USB 연결과 수신기를 확인하세요.' : 'UNO Q 연결하기를 누르면 측정 수신을 시작합니다.');
  text('signal-note', replay ? `${replay.recording.condition || '조건 미기재'} · 저장된 측정 데이터이며 현재 센서 상태를 나타내지 않습니다.` : status.stats?.invalid_lines > 0 ? `인식하지 못한 수신 줄 ${number(status.stats.invalid_lines)}개 · 정지 상태에도 중력에 의해 축별 가속도에 약 1 g가 나타날 수 있습니다.` : '정지 상태에도 중력으로 인해 축별 가속도에 약 1 g가 나타날 수 있습니다.');
  $('chart-empty').hidden = !!samples.length;
  $('chart-stale').hidden = !!replay || fresh || !samples.length;
  if (replay) {
    text('replay-toggle', replay.playing ? '일시정지' : replay.position >= replay.duration ? '다시 재생' : '재생');
    text('replay-time', `${number(replay.position, 1)} / ${number(replay.duration, 1)}초`);
    $('replay-progress').value = replay.duration ? Math.round(replay.position / replay.duration * 1000) : 0;
  }
  renderControls(); drawChart(samples, !replay && !fresh); showError(state.error);
}

function drawChart(samples, stale = false) {
  const canvas = $('signal-chart'); const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * ratio) || canvas.height !== Math.round(rect.height * ratio)) { canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio); }
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, rect.width, rect.height);
  const area = { left: 40, top: 14, width: rect.width - 48, height: rect.height - 45 };
  let peak = 1.2; for (const sample of samples) for (const { key } of axes) peak = Math.max(peak, Math.abs(sample[key]) * 1.1);
  peak = peak <= 1.2 ? 1.2 : Math.ceil(peak * 2) / 2;
  ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (let row = 0; row <= 4; row++) {
    const y = area.top + row / 4 * area.height;
    ctx.strokeStyle = row === 2 ? '#3c505c' : '#2a3c46'; ctx.lineWidth = .65;
    ctx.beginPath(); ctx.moveTo(area.left, y); ctx.lineTo(area.left + area.width, y); ctx.stroke();
    ctx.fillStyle = '#8299a5'; ctx.fillText((peak * (1 - row / 2)).toFixed(1), area.left - 9, y);
  }
  ctx.textAlign = 'center';
  for (let second = 0; second <= 8; second += 2) {
    const x = area.left + second / 8 * area.width;
    ctx.strokeStyle = '#283944'; ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.top + area.height); ctx.stroke();
    ctx.fillStyle = '#8299a5'; ctx.fillText(second === 8 ? state.replay ? '재생' : '현재' : `${second - 8}s`, x, area.top + area.height + 19);
  }
  if (!samples.length) return;
  const end = timeSeconds(samples.at(-1));
  ctx.save(); ctx.beginPath(); ctx.rect(area.left, area.top, area.width, area.height); ctx.clip(); ctx.globalAlpha = stale ? .4 : 1;
  for (const { key, color } of axes) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.beginPath();
    samples.forEach((sample, index) => {
      const x = area.left + (timeSeconds(sample) - end + 8) / 8 * area.width;
      const y = area.top + (.5 - sample[key] / (2 * peak)) * area.height;
      const gap = index > 0 && (sample.seq - samples[index - 1].seq > 1 || timeSeconds(sample) - timeSeconds(samples[index - 1]) > .25);
      if (index === 0 || gap) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  ctx.restore();
}

function renderRecordings() {
  $('recordings-empty').hidden = !!state.records.length;
  $('recordings-list').replaceChildren();
  for (const record of state.records) {
    const row = document.createElement('article'); row.className = 'record-row';
    const details = document.createElement('div'); details.className = 'record-details';
    const title = document.createElement('div'); title.className = 'record-title'; title.textContent = record.label;
    const meta = document.createElement('div'); meta.className = 'record-meta';
    const date = new Date(record.started_at);
    const dateLabel = Number.isNaN(date.getTime()) ? String(record.started_at || '') : date.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    for (const value of [record.condition || '조건 미기재', `${number(record.samples)}개 샘플`, dateLabel]) { const span = document.createElement('span'); span.textContent = value; meta.append(span); }
    if (record.stop_reason && !['user', 'manual', 'stopped', 'manual_stop', 'user_stopped'].includes(record.stop_reason)) {
      const stopReasons = { disconnected: '연결 해제', sensor_stale: '센서 수신 중단', serial_error: 'USB 통신 오류', sensor_error: '센서 오류', device_restarted: '장치 재시작', gateway_error: 'UNO Q 수신 오류', gateway_disconnected: 'UNO Q 연결 끊김' };
      const reason = document.createElement('span'); reason.className = 'record-tag'; reason.textContent = `종료: ${stopReasons[record.stop_reason] || '자동 종료'}`; meta.append(reason);
    }
    details.append(title, meta);
    const actions = document.createElement('div'); actions.className = 'record-actions';
    const replay = document.createElement('button'); replay.className = 'button ghost small'; replay.textContent = '↺ 재생'; replay.dataset.replay = record.id; replay.dataset.count = record.samples;
    replay.addEventListener('click', () => action(() => loadReplay(record.id)));
    const download = document.createElement('a'); download.className = 'button ghost small'; download.textContent = 'CSV ↓'; download.href = `/api/recordings/${encodeURIComponent(record.id)}.csv`; download.download = '';
    actions.append(replay, download); row.append(details, actions); $('recordings-list').append(row);
  }
  renderControls();
}

async function refreshRecordings() { const data = await api('/api/recordings'); state.records = data.recordings || []; renderRecordings(); }

async function loadReplay(id) {
  if (state.status?.recording) throw new Error('현재 기록을 종료한 뒤 재생하세요.');
  const data = await api(`/api/recordings/${encodeURIComponent(id)}`);
  if (!data.samples?.length) throw new Error('이 기록에는 재생할 샘플이 없습니다.');
  const samples = data.samples;
  state.replay = { recording: data.recording, samples, startedAt: timeSeconds(samples[0]), duration: Math.max(0, timeSeconds(samples.at(-1)) - timeSeconds(samples[0])), position: 0, visibleCount: 0, playing: true, lastFrame: performance.now() };
  render(); $('signal-heading').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function poll() {
  try {
    const data = await api(`/api/samples?after=${encodeURIComponent(state.lastId)}`);
    const recordingId = data.status?.recording?.id || null;
    const stopped = state.lastRecording && !recordingId;
    state.lastRecording = recordingId; state.status = data.status || {};
    if (data.samples?.length) {
      state.lastId = data.samples.at(-1).id;
      const valid = data.samples.filter((sample) => numeric(sample.elapsed_s) && axes.every(({ key }) => numeric(sample[key])));
      if (valid.length && state.samples.length && timeSeconds(valid[0]) < timeSeconds(state.samples.at(-1))) state.samples = [];
      state.samples.push(...valid);
      if (state.samples.length) { const cut = timeSeconds(state.samples.at(-1)) - 8; state.samples = state.samples.filter((sample) => timeSeconds(sample) >= cut).slice(-18000); }
    }
    if (stopped) await refreshRecordings();
    if (state.error.startsWith('로컬 서버에 연결할 수 없습니다.')) state.error = '';
  } catch (error) {
    if (state.status) state.status = { ...state.status, fresh: false, connected: false };
    state.error = `로컬 서버에 연결할 수 없습니다. 실행 창을 확인하세요. ${error.message}`;
  }
  if (!state.replay?.playing) render();
  window.setTimeout(poll, 150);
}

function frame(now) {
  const replay = state.replay;
  if (replay?.playing) {
    replay.position = Math.min(replay.duration, replay.position + (now - replay.lastFrame) / 1000); replay.lastFrame = now;
    if (replay.position >= replay.duration) replay.playing = false;
    render();
  }
  window.requestAnimationFrame(frame);
}

$('connect').addEventListener('click', () => action(async () => { await api('/api/connect', { source: INPUT_SOURCE }); state.samples = []; state.lastId = 0; }));
$('disconnect').addEventListener('click', () => action(async () => { await api('/api/disconnect', {}); if (state.status) state.status = { ...state.status, connected: false, fresh: false, recording: null }; await refreshRecordings(); }));
$('record-label').addEventListener('input', renderControls);
$('record-start').addEventListener('click', () => action(async () => { const notes = $('record-notes').value.trim(); await api('/api/record/start', { label: $('record-label').value.trim(), condition: $('record-condition').value + (notes ? ` · ${notes}` : '') }); }));
$('record-stop').addEventListener('click', () => action(async () => { await api('/api/record/stop', {}); if (state.status) state.status.recording = null; await refreshRecordings(); }));
$('refresh-recordings').addEventListener('click', () => action(refreshRecordings));
$('replay-toggle').addEventListener('click', () => { const replay = state.replay; if (!replay) return; if (replay.position >= replay.duration) replay.position = 0; replay.playing = !replay.playing; replay.lastFrame = performance.now(); render(); });
$('replay-restart').addEventListener('click', () => { if (!state.replay) return; Object.assign(state.replay, { position: 0, playing: true, lastFrame: performance.now() }); render(); });
$('replay-progress').addEventListener('input', (event) => { if (!state.replay) return; state.replay.position = Number(event.target.value) / 1000 * state.replay.duration; state.replay.lastFrame = performance.now(); render(); });
$('return-live').addEventListener('click', () => { state.replay = null; state.samples = []; state.lastId = 0; render(); });
window.addEventListener('resize', render);
refreshRecordings().then(render).catch((error) => { showError(error); render(); });
render(); poll(); requestAnimationFrame(frame);
