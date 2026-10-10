// Run-to-run comparison against the first normal runs (mock data, demo assumptions).
// A run is one simulated 15-minute operation sampled once a minute. The first
// BASELINE_RUNS normal runs set each metric's usual band; a later run is flagged
// only when it moves sharply outside that band. A high value alone is not a fault.

export const BASELINE_RUNS = 4;
export const RUN_MINUTES = 15;

export const METRICS = [
  { key: 'coolant_rise', label: '수온 상승폭 (5→15분)', unit: '℃', digits: 1, abs: 3, rel: 0.25,
    part: '냉각계통', checks: ['냉각수량·누수 확인', '라디에이터 막힘·통풍 확인', '서모스탯·워터펌프 확인'] },
  { key: 'coolant_end', label: '15분 시점 수온', unit: '℃', digits: 1, abs: 3, rel: 0.03,
    part: '냉각계통', checks: ['냉각수량·누수 확인', '라디에이터 막힘·통풍 확인'] },
  { key: 'oil_mean', label: '안정 운전 유압', unit: 'kgf/cm²', digits: 2, abs: 0.25, rel: 0.08,
    part: '윤활계통', checks: ['윤활유량·누유 확인', '오일 필터 확인', '유압 센서·배선 확인'] },
  { key: 'oil_step', label: '운전 중 유압 급변', unit: 'kgf/cm²', digits: 2, abs: 0.25, rel: 0,
    part: '윤활계통', checks: ['윤활유량·누유 확인', '오일 펌프·릴리프 밸브 확인'] },
  { key: 'rpm_swing', label: '회전수 흔들림', unit: 'rpm', digits: 0, abs: 15, rel: 0.5,
    part: '연료·조속계통', checks: ['연료 필터·공급 확인', '조속기 상태 확인'] },
  { key: 'volt_swing', label: '출력 전압 흔들림', unit: 'V', digits: 1, abs: 2, rel: 0.5,
    part: '발전부', checks: ['전압조정기(AVR) 확인', '결선·단자 체결 확인'] },
  { key: 'vib_rms', label: '진동 세기', unit: 'g', digits: 3, abs: 0.01, rel: 0.3,
    part: '회전부·체결부', checks: ['베어링 상태·윤활 확인', '고정 볼트·방진 마운트 확인'] },
];

export const KINDS = {
  normal: { label: '정상 운전' },
  coolant_spike: { label: '수온 급상승', note: '수온이 평소보다 두 배 가깝게 오름' },
  oil_drop: { label: '유압 급변', note: '운전 중 유압이 갑자기 떨어짐' },
  vib_jump: { label: '진동 급증', note: '진동이 평소의 두 배 이상' },
  volt_unstable: { label: '전압 불안정', note: '출력 전압이 크게 흔들림' },
  missing: { label: '값 끊김', note: '중간부터 컨트롤러 값 미수신' },
};

function rng(seed) { let s = seed >>> 0 || 1; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
function gauss(r) { return Math.sqrt(-2 * Math.log(r() || 1e-9)) * Math.cos(2 * Math.PI * r()); }

// One minute-by-minute run. ambient 28.5 ℃, 62 % load, 1800 rpm, 208 V.
export function simulateRun(kind, seed) {
  const r = rng(seed), n = () => gauss(r);
  const usualRise = 10 + n() * 0.8;               // 5→15 min rise in normal runs
  const tau = 4.5 + n() * 0.2, top = 80 + n() * 0.8;
  const oilBase = 3.2 + n() * 0.05;
  const series = [];
  for (let m = 0; m <= RUN_MINUTES; m++) {
    let coolant = 28.5 + (top - 28.5) * (1 - Math.exp(-m / tau));
    if (m >= 5) coolant = Math.max(coolant, 0);
    if (kind === 'coolant_spike' && m > 5) coolant += (m - 5) * 1.0;            // ≈ +10 ℃ extra over 10 min
    let oil = m < 2 ? 1.0 + m * 1.1 : oilBase + n() * 0.03;
    if (kind === 'oil_drop' && m >= 9) oil -= 0.9;
    let rpm = m < 1 ? 900 : 1800 + n() * 4;
    let volt = m < 1 ? 0 : 208 + n() * 0.6 + (kind === 'volt_unstable' && m >= 6 ? n() * 6 : 0);
    let vib = 0.045 + n() * 0.002 + (kind === 'vib_jump' && m >= 7 ? 0.06 : 0);
    const missing = kind === 'missing' && m >= 8;
    series.push({ minute: m, coolant_c: missing ? null : +coolant.toFixed(2), oil_kgf_cm2: missing ? null : +oil.toFixed(3),
                  engine_rpm: missing ? null : +rpm.toFixed(0), voltage_ll_v: missing ? null : +volt.toFixed(1), vibration_rms_g: +vib.toFixed(4) });
  }
  return series;
}

const at = (s, m, k) => s.find(p => p.minute === m)?.[k];
const range = vals => Math.max(...vals) - Math.min(...vals);
// Summary metrics from the minutes seen so far (null until enough data).
export function summarize(series) {
  const upto = series.length - 1, steady = series.filter(p => p.minute >= 5);
  const ok = k => steady.every(p => p[k] != null) && steady.length >= 2;
  const out = {};
  out.coolant_rise = upto >= 15 && at(series, 5, 'coolant_c') != null && at(series, 15, 'coolant_c') != null ? at(series, 15, 'coolant_c') - at(series, 5, 'coolant_c') : null;
  out.coolant_end = upto >= 15 ? at(series, 15, 'coolant_c') ?? null : null;
  out.oil_mean = ok('oil_kgf_cm2') ? steady.reduce((a, p) => a + p.oil_kgf_cm2, 0) / steady.length : null;
  out.oil_step = ok('oil_kgf_cm2') ? Math.max(0, ...steady.slice(1).map((p, i) => Math.abs(p.oil_kgf_cm2 - steady[i].oil_kgf_cm2))) : null;
  out.rpm_swing = ok('engine_rpm') ? range(steady.map(p => p.engine_rpm)) : null;
  out.volt_swing = ok('voltage_ll_v') ? range(steady.map(p => p.voltage_ll_v)) : null;
  out.vib_rms = steady.length >= 2 ? steady.reduce((a, p) => a + p.vibration_rms_g, 0) / steady.length : null;
  out.missing = series.some(p => p.coolant_c == null);
  return out;
}

export function baselineFrom(summaries) {
  return Object.fromEntries(METRICS.map(m => {
    const v = summaries.map(s => s[m.key]).filter(x => x != null);
    const mean = v.reduce((a, b) => a + b, 0) / v.length;
    const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, v.length - 1));
    const buffer = Math.max(3 * sd, m.abs, m.rel * Math.abs(mean));
    return [m.key, { mean, sd, lo: mean - buffer, hi: mean + buffer, buffer, n: v.length }];
  }));
}

// sharp = outside the band by more than one more buffer width.
export function compare(summary, baseline) {
  const rows = METRICS.map(m => {
    const b = baseline[m.key], v = summary[m.key];
    if (v == null) return { ...m, value: null, base: b, state: 'pending' };
    const off = v > b.hi ? v - b.hi : v < b.lo ? v - b.lo : 0;
    const state = off === 0 ? 'usual' : Math.abs(off) > b.buffer ? 'sharp' : 'outside';
    return { ...m, value: v, base: b, off, state, ratio: b.mean ? v / b.mean : null };
  });
  const flagged = rows.filter(r => r.state === 'sharp' || r.state === 'outside')
    .sort((a, b) => (b.state === 'sharp') - (a.state === 'sharp'));  // stable: METRICS order breaks ties (rise before level)
  return { rows, flagged, missing: summary.missing };
}
