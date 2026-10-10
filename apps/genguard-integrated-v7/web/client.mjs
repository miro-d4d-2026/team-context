export class GenGuardError extends Error {
  constructor(message, {code = 'request_failed', status = null, details = null} = {}) {
    super(message); this.name = 'GenGuardError';
    this.code = code; this.status = status; this.details = details;
  }
}

function canonical(value) {
  if (typeof value === 'number' && !Number.isFinite(value)) throw new GenGuardError('숫자는 유한한 값이어야 합니다.', {code: 'invalid_input'});
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  if (value === undefined) throw new GenGuardError('없는 값은 undefined 대신 null로 보내세요.', {code: 'invalid_input'});
  return value;
}
const clone = value => JSON.parse(JSON.stringify(value));
const sensorFields = new Set(['vibration_rms_g', 'envelope_cv']);
const requestId = () => globalThis.crypto?.randomUUID?.() || `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;

/** Browser client only: every analysis is performed by the device API. */
export class GenGuardClient {
  constructor({baseUrl = globalThis.location?.origin, token = '', timeoutMs = 10000} = {}) {
    let parsed;
    try { parsed = new URL(baseUrl); } catch { throw new GenGuardError('API 주소를 http://기기주소:포트 형식으로 입력하세요.', {code: 'invalid_url'}); }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) throw new GenGuardError('API 주소에는 http/https와 호스트·포트만 사용하세요.', {code: 'invalid_url'});
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new GenGuardError('timeoutMs는 양수여야 합니다.', {code: 'invalid_timeout'});
    this.baseUrl = parsed.href.replace(/\/$/, ''); this.token = token; this.timeoutMs = timeoutMs;
    this.sessionId = null; this.nextSeq = 1; this._busy = false; this._pending = null; this._startId = null;
  }
  get busy() { return this._busy; }
  get pendingSample() { return this._pending ? clone(this._pending) : null; }
  async _request(path, {method = 'GET', body} = {}) {
    if (this._busy) throw new GenGuardError('앞선 요청이 끝난 뒤 다시 보내세요.', {code: 'busy'});
    if (globalThis.location?.protocol === 'https:' && new URL(this.baseUrl).protocol === 'http:') throw new GenGuardError('HTTPS 웹에서는 HTTP 기기 API를 차단합니다. UNO Q가 제공하는 같은 주소의 웹을 열거나 HTTPS 프록시를 사용하세요.', {code: 'mixed_content'});
    this._busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers = {Accept: 'application/json'};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (this.token) headers.Authorization = `Bearer ${this.token}`;
      const response = await fetch(`${this.baseUrl}${path}`, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, credentials: 'omit', cache: 'no-store'});
      const raw = await response.text(); let data;
      try { data = raw ? JSON.parse(raw) : {}; } catch { throw new GenGuardError('API가 JSON이 아닌 응답을 보냈습니다. 주소·포트와 서버 로그를 확인하세요.', {code: 'invalid_response', status: response.status}); }
      if (!response.ok) {
        const help = response.status === 401 || response.status === 403 ? '인증 토큰과 서버의 허용 웹 주소(CORS/Origin)를 확인하세요.' : response.status === 409 ? '세션 또는 순번이 충돌했습니다. 다른 브라우저의 세션을 임의로 종료하지 말고 기존 실행 상태를 확인하세요.' : 'API가 입력을 처리하지 못했습니다. 값과 서버 로그를 확인하세요.';
        throw new GenGuardError(help, {code: 'http_error', status: response.status, details: data});
      }
      return data;
    } catch (error) {
      if (error instanceof GenGuardError) throw error;
      if (controller.signal.aborted) throw new GenGuardError('응답 시간이 초과되었습니다. 서버에서 처리됐을 수 있으므로 같은 값을 재전송하세요.', {code: 'timeout'});
      throw new GenGuardError('API에 연결하지 못했습니다. 기기 주소·네트워크·서버 실행·CORS 설정을 확인하세요. 같은 값을 재전송할 수 있습니다.', {code: 'network', details: String(error.message || error)});
    } finally { clearTimeout(timeout); this._busy = false; }
  }
  getConfig() { return this._request('/api/config'); }
  getImu() { return this._request('/api/imu'); }
  getTrendCatalog() { return this._request('/api/trend/catalog'); }
  runTrendExperiment(scenarioId) {
    if (typeof scenarioId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(scenarioId)) throw new GenGuardError('목록에서 추세 실험을 선택하세요.', {code:'invalid_scenario'});
    return this._request('/api/trend/experiment', {method:'POST',body:{scenario_id:scenarioId}});
  }
  startCalibration({confirmedNormal = false} = {}) {
    if (!confirmedNormal) throw new GenGuardError('현재 부착 상태와 진동을 정상 기준으로 사용할지 먼저 확인하세요.', {code: 'normal_confirmation_required'});
    if (this._pending) throw new GenGuardError('미확인 분석 요청을 재전송하거나 시험을 종료한 뒤 기준을 등록하세요.', {code: 'pending_conflict'});
    return this._request('/api/imu/calibration/start', {method: 'POST', body: {confirmed_normal: true}});
  }
  async startSession() {
    if (this._busy) throw new GenGuardError('앞선 요청이 끝난 뒤 다시 시작하세요.', {code: 'busy'});
    if (this.sessionId) return {session_id: this.sessionId, next_seq: this.nextSeq};
    this._startId ||= requestId();
    const result = await this._request('/api/sessions', {method: 'POST', body: {request_id: this._startId}});
    if (typeof result.session_id !== 'string' || !Number.isInteger(result.next_seq) || result.next_seq < 1) throw new GenGuardError('세션 응답 형식이 올바르지 않습니다.', {code: 'invalid_response'});
    this.sessionId = result.session_id; this.nextSeq = result.next_seq;
    return result;
  }
  async sendSample(values, {phase = 'running', quality = {}} = {}) {
    if (this._busy) throw new GenGuardError('앞선 요청이 끝난 뒤 다시 보내세요.', {code: 'busy'});
    if (!this.sessionId) throw new GenGuardError('먼저 본 브라우저에서 세션을 시작하세요.', {code: 'no_session'});
    // The device owns IMU provenance: the browser must never replace measured features.
    for (const key of [...Object.keys(values || {}), ...Object.keys(quality || {})]) {
      if (sensorFields.has(key)) throw new GenGuardError('진동 값과 품질은 UNO Q의 IMU에서 가져옵니다. 웹 입력으로 덮어쓸 수 없습니다.', {code: 'sensor_override'});
    }
    const body = canonical({seq: this.nextSeq, source: 'hybrid_demo', phase, values, quality});
    if (this._pending && JSON.stringify(body) !== JSON.stringify(this._pending)) throw new GenGuardError('결과가 확인되지 않은 요청이 있습니다. pendingSample의 동일한 값·품질로 재전송하거나 현재 세션을 종료하세요.', {code: 'pending_conflict'});
    this._pending ||= body;
    const result = await this._request(`/api/sessions/${encodeURIComponent(this.sessionId)}/samples`, {method: 'POST', body: this._pending});
    if (result.session_id !== this.sessionId || result.seq !== this.nextSeq || result.schema_version !== 'genguard.bridge.result/2') throw new GenGuardError('분석 응답의 세션·순번·버전이 일치하지 않습니다. 같은 요청을 재전송하세요.', {code: 'invalid_response'});
    this.nextSeq += 1; this._pending = null;
    return result;
  }
  async closeSession() {
    if (this._busy) throw new GenGuardError('앞선 요청이 끝난 뒤 종료하세요.', {code: 'busy'});
    if (!this.sessionId) return {closed: true, session_id: null};
    const result = await this._request(`/api/sessions/${encodeURIComponent(this.sessionId)}/close`, {method: 'POST', body: {}});
    if (result.closed !== true || result.session_id !== this.sessionId) throw new GenGuardError('종료 응답이 확인되지 않았습니다. 종료를 다시 요청하세요.', {code: 'invalid_response'});
    this.sessionId = null; this.nextSeq = 1; this._pending = null; this._startId = null;
    return result;
  }
}
