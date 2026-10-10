// Pure browser/Node calculation. These data gates are demo policy, not OEM limits.
export const TREND_POLICY = Object.freeze({
  minimum_samples: 6,
  minimum_span_s: 300,
  maximum_gap_s: 120,
  maximum_rpm_range: 90,
  maximum_load_pct_range: 5,
  maximum_ambient_c_range: 3,
  minimum_r2: 0.8,
  minimum_negative_slope_magnitude: 1e-10,
  required_source: 'scenario',
  required_phase: 'running',
  required_quality: 'ok',
  provenance: '해커톤 추세 계산을 위한 시연 정책. 제조사 진단 또는 RUL 기준이 아님',
});

const LABEL = '현재 추세 유지 시 참고 한계 도달시간';
const ASSUMPTION = '같은 운전 조건에서 관측한 선형 하락 추세가 앞으로도 유지된다는 가정입니다. RUL·고장시점·실제 보호정지시점이 아닙니다.';
const REASONS = Object.freeze({
  estimated: '현재 추세를 선형으로 연장한 단일 참고값입니다.',
  threshold_reached: '현재 유압이 선택한 참고 한계에 이미 도달했습니다.',
  invalid_limit: '참고 한계값이 올바르지 않습니다.',
  invalid_history: '시간순 기록 배열이 필요합니다.',
  insufficient_samples: '추세 계산에는 최소 6개의 유효 기록이 필요합니다.',
  invalid_measurement: '유압 또는 운전 조건에 누락·잘못된 값이 있습니다.',
  data_unavailable: '미수신·오래된 데이터 또는 품질이 확인되지 않은 기록이 있습니다.',
  source_mismatch: '이 시연 계산에는 모의 기록만 사용할 수 있습니다.',
  phase_change: '모든 기록이 안정 운전 단계여야 합니다.',
  invalid_time_order: '기록 시각은 중복 없이 시간순이어야 합니다.',
  insufficient_duration: '관측 구간이 5분보다 짧습니다.',
  data_gap: '관측 중 120초를 넘는 데이터 공백이 있습니다.',
  conditions_changed: '회전수·부하·주변 온도 조건이 변해 단일 추세를 적용할 수 없습니다.',
  not_decreasing: '유압이 유지되거나 상승하여 하한 도달시간을 계산하지 않습니다.',
  unstable_trend: '관측값의 직선 추세 적합도가 시연 기준에 미달합니다.',
  invalid_result: '추세 계산 결과를 표시할 수 없습니다.',
});

const finite = value => typeof value === 'number' && Number.isFinite(value);
const range = values => Math.max(...values) - Math.min(...values);

/**
 * history is ordered and contains t_s, phase, oil_kgf_cm2, engine_rpm,
 * load_pct, ambient_c, source and quality. Oil units are fixed by field name.
 * t_s is scenario/observation time, never replay wall-clock time.
 * No timestamp-now argument exists: upstream must mark stale input in quality.
 * Records are rejected as a group; missing points are never silently discarded.
 */
export function estimateOilLimitTime(history, { limit = 0.6 } = {}) {
  const base = {
    status: 'unavailable',
    reason: null,
    message: null,
    minutes: null,
    label: LABEL,
    assumption: ASSUMPTION,
    method: 'ordinary_least_squares_linear_extrapolation',
    source: 'scenario',
    unit: 'kgf/cm²',
    limit: finite(limit) ? limit : null,
    current: null,
    slope_kgf_cm2_per_min: null,
    r2: null,
    window_minutes: null,
    sample_count: Array.isArray(history) ? history.length : 0,
    policy: TREND_POLICY,
  };
  const result = (reason, extra = {}) => ({ ...base, reason, message: REASONS[reason], ...extra });
  if (!finite(limit) || limit <= 0) return result('invalid_limit');
  if (!Array.isArray(history)) return result('invalid_history');
  if (history.length < TREND_POLICY.minimum_samples) return result('insufficient_samples');

  for (const p of history) {
    if (!p || typeof p !== 'object') return result('invalid_measurement');
    if (p.quality !== TREND_POLICY.required_quality) return result('data_unavailable');
    if (p.source !== TREND_POLICY.required_source) return result('source_mismatch');
    if (p.phase !== TREND_POLICY.required_phase) return result('phase_change');
    if (!finite(p.t_s) || p.t_s < 0 || !finite(p.oil_kgf_cm2) || p.oil_kgf_cm2 < 0
      || !finite(p.engine_rpm) || p.engine_rpm <= 0 || !finite(p.load_pct) || p.load_pct < 0
      || !finite(p.ambient_c)) return result('invalid_measurement');
  }

  const first = history[0];
  const last = history.at(-1);
  const span_s = last.t_s - first.t_s;
  for (let i = 1; i < history.length; i += 1) {
    const gap = history[i].t_s - history[i - 1].t_s;
    if (gap <= 0) return result('invalid_time_order');
    if (gap > TREND_POLICY.maximum_gap_s) return result('data_gap');
  }
  if (span_s < TREND_POLICY.minimum_span_s) return result('insufficient_duration');
  if (range(history.map(p => p.engine_rpm)) > TREND_POLICY.maximum_rpm_range
    || range(history.map(p => p.load_pct)) > TREND_POLICY.maximum_load_pct_range
    || range(history.map(p => p.ambient_c)) > TREND_POLICY.maximum_ambient_c_range) {
    return result('conditions_changed');
  }

  base.current = last.oil_kgf_cm2;
  base.window_minutes = span_s / 60;
  if (last.oil_kgf_cm2 <= limit) return result('threshold_reached', {
    status: 'threshold_reached', minutes: 0,
  });

  // Shift time to the first observation before fitting to avoid epoch precision loss.
  const x = history.map(p => (p.t_s - first.t_s) / 60);
  const y = history.map(p => p.oil_kgf_cm2);
  const mean_x = x.reduce((sum, value) => sum + value, 0) / x.length;
  const mean_y = y.reduce((sum, value) => sum + value, 0) / y.length;
  let ss_x = 0;
  let ss_y = 0;
  let ss_xy = 0;
  for (let i = 0; i < x.length; i += 1) {
    const dx = x[i] - mean_x;
    const dy = y[i] - mean_y;
    ss_x += dx * dx;
    ss_y += dy * dy;
    ss_xy += dx * dy;
  }
  const slope = ss_xy / ss_x;
  const r2 = ss_y > 0 ? Math.max(0, Math.min(1, (ss_xy * ss_xy) / (ss_x * ss_y))) : 0;
  if (!finite(slope) || !finite(r2)) return result('invalid_result');
  base.slope_kgf_cm2_per_min = slope;
  base.r2 = r2;
  if (slope >= -TREND_POLICY.minimum_negative_slope_magnitude) return result('not_decreasing');
  if (r2 < TREND_POLICY.minimum_r2) return result('unstable_trend');

  // Anchor at the latest observed value, as stated in the on-screen formula.
  const minutes = (last.oil_kgf_cm2 - limit) / -slope;
  if (!finite(minutes) || minutes < 0) return result('invalid_result');
  return result('estimated', { status: 'estimated', minutes });
}
