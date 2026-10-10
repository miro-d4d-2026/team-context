"""Frozen iPhone/IMU stimulus classifier. Not a generator health classifier.
Standard library only. Definitions match the supplied October 10 report.
"""
import math
import statistics
from collections import defaultdict

CLASSES = ('idle', 'baseline', 'increased', 'intermittent')
LABELS = dict(zip(CLASSES, ('무진동', '기준 연속 진동', '강한 연속 진동', '간헐 진동')))
FEATURE_DEFINITION = 'axis-demean-vector-rms__100ms-bin-demean-cv-v1'


def rms(axes):
    if not axes:
        raise ValueError('빈 측정 구간입니다.')
    if any(len(a) != 3 or any(not math.isfinite(float(v)) for v in a) for a in axes):
        raise ValueError('유한한 XYZ 가속도가 필요합니다.')
    means = [statistics.fmean(a[k] for a in axes) for k in range(3)]
    return math.sqrt(statistics.fmean(sum((a[k]-means[k])**2 for k in range(3)) for a in axes))


def features(times, axes, origin=None):
    if len(times) != len(axes) or len(times) < 2:
        raise ValueError('시간과 XYZ 배열의 길이를 확인하세요.')
    if any(not math.isfinite(t) for t in times) or any(b <= a for a,b in zip(times,times[1:])):
        raise ValueError('측정 시각은 유한하며 엄격히 증가해야 합니다.')
    origin = times[0] if origin is None else origin
    bins = defaultdict(list)
    for t,a in zip(times,axes):
        bins[math.floor((t-origin)/0.1)].append(a)
    env = [rms(a) for _,a in sorted(bins.items()) if len(a) >= 5]
    if len(env) < 2:
        raise ValueError('100ms 구간에 샘플이 충분하지 않습니다.')
    rv = rms(axes)
    return {'rms_vector_g': rv, 'rms_contract_g': rv / math.sqrt(3),
            'envelope_cv': statistics.pstdev(env) / max(statistics.fmean(env),1e-9),
            'sample_rate_hz': (len(times)-1)/(times[-1]-times[0]),
            'sample_count': len(times), 'feature_definition': FEATURE_DEFINITION,
            'max_gap_ms': max(b-a for a,b in zip(times,times[1:]))*1000}


def predict(rms_vector_g, envelope_cv, frozen, method='rms_and_pattern'):
    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0 for v in (rms_vector_g,envelope_cv)):
        raise ValueError('RMS와 CV는 유한한 0 이상 값이어야 합니다.')
    model = frozen['models'][method]
    vals = {'log_rms': math.log(max(rms_vector_g,1e-9)), 'envelope_cv': envelope_cv}
    scaled = [(vals[f]-m)/s for f,m,s in zip(model['features'],model['mean'],model['std'])]
    distances = [sum((v-c)**2 for v,c in zip(scaled,center)) for center in model['centroids']]
    label = model['classes'][min(range(len(distances)),key=distances.__getitem__)]
    return {'pattern_id': label, 'label': LABELS[label], 'method': method,
            'confidence': None, 'forced_choice': True, 'health_state': 'unknown', 'rul': None}
