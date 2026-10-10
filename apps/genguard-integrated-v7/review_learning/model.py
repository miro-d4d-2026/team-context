"""Small learned conditional baseline. No LLM, torch, or sklearn is required.

Fit regression coefficients and residual covariance on approved normal sessions.
Calibrate a score boundary on DIFFERENT sessions. Test on held-out sessions.
This prototype has not been validated for generator fault diagnosis.
"""
import math
import time
import uuid
import numpy as np

VERSION = 'genguard-reviewed-core-baseline/1'
PROFILES = {
    'vibration': {'name': '진동 전용', 'targets': ['vibration_rms_g', 'envelope_cv'], 'context': []},
    'fusion': {'name': '진동 + 제어반', 'targets': ['vibration_rms_g', 'envelope_cv', 'engine_rpm', 'coolant_c', 'oil_bar', 'voltage_v', 'current_a'], 'context': ['load_pct']},
}
METRICS = {
    'vibration_rms_g': ('진동 RMS', 'g', 0.0001),
    'vibration_peak_g': ('진동 Peak', 'g', 0.0002),
    'envelope_cv': ('진동 세기 변동성', '', 0.02),
    'engine_rpm': ('엔진 회전수', 'rpm', 5.0),
    'coolant_c': ('냉각수 온도', '°C', 0.5),
    'oil_bar': ('윤활유 압력', 'bar', 0.03),
    'voltage_v': ('출력 전압', 'V', 0.5),
    'current_a': ('출력 전류', 'A', 0.1),
    'load_pct': ('부하율', '%', 1.0),
    'ambient_c': ('주변 공기 온도', '°C', 0.3),
    'humidity_pct': ('상대습도', '%RH', 1.0),
    'frequency_hz': ('출력 주파수', 'Hz', 0.1),
}
LOG_KEYS = {'vibration_rms_g', 'vibration_peak_g'}

def finite(x):
    return type(x) in (int, float) and math.isfinite(x)

def values(frame, keys):
    out = []
    if frame.get('phase') != 'running':
        raise ValueError('안정 운전 기준만 지원합니다. 시동·예열·정지는 판단하지 않습니다.')
    if frame.get('quality') != 'ok':
        raise ValueError('계측 품질을 확인하세요.')
    for k in keys:
        x = frame.get('values', {}).get(k)
        if not finite(x) or (k in LOG_KEYS and x <= 0):
            raise ValueError(f'{METRICS[k][0]} 결측 또는 유효하지 않은 값')
        if k=='envelope_cv' and not 0<=x<=100:
            raise ValueError('진동 변동성 값이 유효 범위 밖입니다.')
        if k=='vibration_rms_g' and not 0<x<=30:
            raise ValueError('진동 RMS 값이 유효 범위 밖입니다.')
        out.append(math.log(x) if k in LOG_KEYS else x)
    return out

def matrix(frames, keys):
    return np.array([values(f, keys) for f in frames], dtype=np.float64)

# Keep the deployed core numerical implementation. This wrapper has a separate
# schema and database; it never masquerades as or replaces the frozen bundle.
import importlib.util
from pathlib import Path
_core_path = Path(__file__).resolve().parents[1]/'model_bundle/runtime/genguard/model.py'
_spec = importlib.util.spec_from_file_location('genguard_review_core_math', _core_path)
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

def core_profile(profile):
    p=PROFILES[profile]
    return {'context':p['context'], 'targets':p['targets'],
      'fields':{k:{'unit':METRICS[k][1], 'transform':'log' if k in LOG_KEYS else 'identity',
                   'scale_floor':.02 if k in LOG_KEYS else METRICS[k][2]} for k in p['context']+p['targets']},
      'model':{'ridge_alpha':.1,'covariance_shrinkage':.25},
      'context_tolerance':{k:METRICS[k][2]*2 for k in p['context']}}

def core_frame(frame):
    return {'elapsed_s':frame['elapsed_s'], 'phase':frame['phase'],
            'readings':{k:{'value':v} for k,v in frame['values'].items()}}

def score(model, frame):
    values(frame,model['context']+model['targets'])
    r=_core.score_phase(core_frame(frame),model['core_profile'],model['core_phase'])
    if r['outside_context']:
        raise ValueError('학습한 운전 조건 범위 밖입니다: '+', '.join(r['outside_context']))
    evidence=[]
    for e in r['evidence']:
        k=e['field'];actual=e['value'];expected=e['expected']
        evidence.append({'key':k,'name':METRICS[k][0],'unit':METRICS[k][1],
          'actual':actual,'expected':expected,'delta':actual-expected,
          'change_pct':(actual-expected)/abs(expected)*100 if abs(expected)>METRICS[k][2] else None,
          'standardized_deviation':abs(e['standardized_residual'])})
    return {'score':r['score'],'threshold':model['threshold'],
      'ratio':r['score']/model['threshold'],'evidence':evidence,'model_id':model['id']}

def fit_model(fit,calibration,validation,profile,signature,splits,progress=None):
    tick=progress or (lambda _:None);started=time.perf_counter();p=PROFILES[profile]
    tick('기존 조건부 회귀 코어로 승인 정상 기록 학습')
    cp=core_profile(profile)
    phase=_core.fit_phase([core_frame(f) for f in fit],cp)
    model={'id':'review-'+uuid.uuid4().hex[:12],'algorithm':VERSION,'core_algorithm':_core.ALGORITHM,
      'profile':profile,'signature':signature,'targets':p['targets'],'context':p['context'],
      'core_profile':cp,'core_phase':phase,'threshold':1.0,'splits':splits,'created_at':time.time(),
      'scope':'검토된 동일 설치의 정상 참고 비교. 실제 발전기 고장 탐지 성능 미검증.'}
    tick('별도 운전으로 정상 경계 보정')
    scores=[score(model,f)['score'] for f in calibration]
    model['threshold']=max(1.,float(np.quantile(scores,.99))*1.5)
    phase['threshold']=model['threshold']
    tick('미사용 정상 기록으로 독립 검증')
    results=[]
    for f in validation:
        try:results.append(score(model,f)['ratio']>1)
        except ValueError:results.append(None)
    count=sum(x is not None for x in results)
    rate=sum(x is True for x in results)/count if count else 1.
    coverage=count/len(validation) if validation else 0.
    model.update(validation={'valid_windows':count,'total_windows':len(validation),'outside_rate':rate,
      'coverage':coverage,'outside_rate_limit':.10,'coverage_min':.95},
      passed=bool(rate<=.10 and coverage>=.95),training_seconds=time.perf_counter()-started)
    return model
