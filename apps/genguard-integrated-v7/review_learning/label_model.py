"""A small, separate supervised classifier trained from human-reviewed labels.

NumPy only. Fits standardization + quadratic logistic classifier on fit sessions;
chooses a boundary on calibration sessions; reports untouched validation metrics.
A score is a classifier output, NOT a physical fault probability. Unknown faults
remain the responsibility of the separate normal-reference anomaly detector.
"""
import time
import uuid
import numpy as np
from .model import PROFILES, METRICS, LOG_KEYS, values

ALGORITHM = 'genguard-reviewed-quadratic-logistic/1'
POLICY = {'max_false_positive_rate': 0.10, 'min_abnormal_recall': 0.80,
          'min_coverage': 0.95, 'min_windows_per_class_split': 30,
          'fit_sessions_per_class': 2, 'min_windows_per_run_class': 30, 'epochs': 400, 'regularization': 0.015}


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def _design(x, mean, scale):
    z = np.clip((x - mean) / scale, -20, 20)
    return np.column_stack((np.ones(len(z)), z, z * z))


def predict_label(model, frame):
    raw = np.array(values(frame, model['keys']), float)
    for key, low, high in zip(model['context'], model['context_min'], model['context_max']):
        value = frame['values'][key]
        margin = max((high-low)*0.1, METRICS[key][2]*2)
        if not low-margin <= value <= high+margin:
            raise ValueError(f'{METRICS[key][0]}가 라벨 학습의 운전 조건 밖입니다.')
    design = _design(raw[None, :], np.array(model['mean']), np.array(model['scale']))
    score = float(_sigmoid(design @ np.array(model['coef']))[0])
    return {'state': 'abnormal_like' if score >= model['threshold'] else 'normal_like',
            'score': score, 'threshold': model['threshold'], 'model_id': model['id'],
            'meaning': '사람이 검토한 정상/비정상 사례의 분류 점수 · 고장 확률 아님',
            'fault_probability': None, 'fault_type': None}


def _metrics(y, scores, threshold, sessions):
    pred = scores >= threshold
    positive = y == 1
    negative = ~positive
    tp, fn = int(np.sum(pred & positive)), int(np.sum(~pred & positive))
    fp, tn = int(np.sum(pred & negative)), int(np.sum(~pred & negative))
    return {'tp': tp, 'fn': fn, 'fp': fp, 'tn': tn,
            'abnormal_recall': tp/max(1, tp+fn),
            'false_positive_rate': fp/max(1, fp+tn),
            'balanced_accuracy': (tp/max(1, tp+fn)+tn/max(1, tn+fp))/2,
            'normal_windows': int(negative.sum()), 'abnormal_windows': int(positive.sum()),
            'sessions': sorted(set(sessions)), 'unit': '분석 창; 독립 시험 횟수 아님'}


def fit_label_model(dataset, profile, signature, splits, dependencies, coverage, tick=None):
    tick = tick or (lambda _: None)
    started = time.perf_counter()
    p = PROFILES[profile]
    keys = p['context'] + p['targets']
    tick('사람이 검토한 정상·비정상 사례 확인')
    arrays = {}
    for role in ('fit', 'calibration', 'validation'):
        items = dataset[role]
        arrays[role] = (np.array([values(i['frame'], keys) for i in items], float),
                        np.array([i['label'] == 'abnormal' for i in items], float),
                        [i['session_id'] for i in items])
    x, y, sids = arrays['fit']
    mean = x.mean(0)
    floors = np.array([0.02 if k in LOG_KEYS else METRICS[k][2] for k in keys])
    scale = np.maximum(x.std(0), floors)
    design = _design(x, mean, scale)
    # Equal influence per class, then per run within a class. A long abnormal
    # recording must not outweigh every normal run just because it has more rows.
    weights = np.zeros(len(y), float)
    for label in (0, 1):
        group_ids = sorted({sid for sid, yy in zip(sids, y) if yy == label})
        for sid in group_ids:
            mask = np.array([s == sid for s in sids]) & (y == label)
            weights[mask] = 0.5 / len(group_ids) / int(mask.sum())
    coef = np.zeros(design.shape[1], float)
    penalty = np.ones_like(coef)*POLICY['regularization']; penalty[0] = 0
    # Conservative, data-dependent gradient step. No sklearn/torch dependency.
    lipschitz = 0.25 * float(np.linalg.eigvalsh(design.T @ (design * weights[:, None])).max()) + float(penalty.max())
    step = 0.9/max(lipschitz, 1e-6)
    for epoch in range(POLICY['epochs']):
        if epoch % 10 == 0:
            tick(f'라벨 분류기 학습 {epoch}/{POLICY["epochs"]}')
        gradient = design.T @ (weights*(_sigmoid(design @ coef)-y)) + penalty*coef
        coef -= step*gradient
        if not np.isfinite(coef).all():
            raise ValueError('라벨 모델 계산이 유효하지 않습니다.')
    tick('별도 정상·비정상 기록으로 판단 경계 보정')
    cx, cy, cs = arrays['calibration']
    context_x = x[:, :len(p['context'])]
    context_min=context_x.min(0);context_max=context_x.max(0)
    def context_valid(rows):
        accepted=np.ones(len(rows),dtype=bool)
        for i,key in enumerate(p['context']):
            margin=max((context_max[i]-context_min[i])*.1,METRICS[key][2]*2)
            accepted &= (rows[:,i]>=context_min[i]-margin)&(rows[:,i]<=context_max[i]+margin)
        return accepted
    if not context_valid(cx).all():
        raise ValueError('경계 보정 기록이 학습한 운전 조건 밖입니다. 같은 조건의 독립 기록이 필요합니다.')
    scores = _sigmoid(_design(cx, mean, scale) @ coef)
    candidates = np.unique(np.r_[np.linspace(.02, .98, 97), scores, np.nextafter(scores, 1.0)])
    options = []
    for threshold in candidates:
        m = _metrics(cy, scores, float(threshold), cs)
        if m['false_positive_rate'] <= POLICY['max_false_positive_rate']:
            # Prefer recall first, fewer false positives second, then larger margin.
            margin = float(np.min(np.abs(scores-threshold)))
            options.append((m['abnormal_recall'], -m['false_positive_rate'], margin, float(threshold), m))
    if not options:
        raise ValueError('정상 오경보 정책을 만족하는 판단 경계를 찾지 못했습니다.')
    _, _, _, threshold, calibration_metrics = max(options, key=lambda o: o[:3])
    tick('미사용 운전에서 정상 오경보·비정상 놓침 확인')
    vx, vy, vs = arrays['validation']
    accepted=context_valid(vx)
    vscores = _sigmoid(_design(vx[accepted], mean, scale) @ coef)
    validation = _metrics(vy[accepted], vscores, threshold, [sid for sid,ok in zip(vs,accepted) if ok])
    validation['coverage'] = coverage['validation']*float(accepted.mean())
    validation['unknown_windows']=int((~accepted).sum())
    validation['total_windows']=len(vx)
    # The held-out data never change coefficients, scaler, or threshold.
    passed = (calibration_metrics['abnormal_recall'] >= POLICY['min_abnormal_recall']
              and validation['abnormal_recall'] >= POLICY['min_abnormal_recall']
              and validation['false_positive_rate'] <= POLICY['max_false_positive_rate']
              and validation['coverage'] >= POLICY['min_coverage'])
    context_x = x[:, :len(p['context'])]
    return {'id': 'fb-'+uuid.uuid4().hex[:12], 'algorithm': ALGORITHM,
            'profile': profile, 'signature': signature, 'keys': keys, 'context': p['context'],
            'context_min': context_x.min(0).tolist(), 'context_max': context_x.max(0).tolist(),
            'mean': mean.tolist(), 'scale': scale.tolist(), 'coef': coef.tolist(),
            'threshold': float(threshold), 'splits': splits, 'dependencies': dependencies,
            'calibration': calibration_metrics, 'validation': validation, 'policy': POLICY,
            'passed': bool(passed), 'created_at': time.time(), 'training_seconds': time.perf_counter()-started,
            'scope': '검토 라벨 기반 이진 분류 시험. 실제 고장 부위·확률·RUL 미검증.',
            'source_kind': signature['source_kind'], 'coverage': coverage}
