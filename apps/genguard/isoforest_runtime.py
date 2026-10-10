"""Portable Isolation Forest inference: JSON only, no pickle or sklearn at runtime."""
import hashlib
import json
import math
import struct
from signal_features import FEATURE_DEFINITION

FORMAT = 'genguard-isolation-forest-v1'
FEATURES = ('log_rms_vector_g', 'envelope_cv')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def feature_vector(f):
    if f.get('feature_definition') != FEATURE_DEFINITION:
        raise ValueError('특징 계산 버전 불일치')
    r, cv = f.get('rms_vector_g'), f.get('envelope_cv')
    if not finite(r) or not finite(cv) or r <= 0 or cv < 0:
        raise ValueError('유효한 양수 RMS와 0 이상 CV가 필요합니다.')
    return [math.log(max(r, 1e-9)), cv]


def average_path(n):
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1.0) + 0.5772156649015329) - 2.0 * (n - 1.0) / n


class Forest:
    def __init__(self, artifact):
        if artifact.get('format') != FORMAT or artifact.get('features') != list(FEATURES):
            raise ValueError('지원하지 않는 모델 형식/특징')
        if artifact.get('feature_definition') != FEATURE_DEFINITION:
            raise ValueError('전처리 버전이 일치하지 않습니다.')
        self.artifact = artifact
        self.trees = artifact.get('trees')
        n = artifact.get('max_samples')
        if type(n) is not int or not 2 <= n <= 256:
            raise ValueError('모델 샘플 한도 오류')
        if not isinstance(self.trees, list) or not 1 <= len(self.trees) <= 64:
            raise ValueError('모델 트리 한도 오류')
        for tree in self.trees:
            nodes = len(tree.get('left', []))
            if not 1 <= nodes <= 511 or any(len(tree.get(k, [])) != nodes for k in ('right','feature','threshold','count')):
                raise ValueError('트리 배열 한도 오류')
            for i in range(nodes):
                left, right = tree['left'][i], tree['right'][i]
                if type(left) is not int or type(right) is not int or type(tree['count'][i]) is not int or tree['count'][i] < 1:
                    raise ValueError('트리 정수/샘플 수 오류')
                if not finite(tree['threshold'][i]):
                    raise ValueError('트리 임계값 오류')
                if left == right == -1:
                    continue
                if not (i < left < nodes and i < right < nodes) or tree['feature'][i] not in (0,1):
                    raise ValueError('순환/잘못된 분기')
        threshold = artifact.get('threshold')
        if not finite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('이상 점수 경계 오류')
        self.threshold = threshold
        self.denominator = len(self.trees) * average_path(n)

    def score(self, x):
        if len(x) != 2 or not all(finite(v) for v in x):
            raise ValueError('두 유한한 특징값이 필요합니다.')
        # sklearn trees see float32 input, even if the caller uses Python doubles.
        try:
            x = [struct.unpack('<f', struct.pack('<f', v))[0] for v in x]
        except (OverflowError, struct.error) as exc:
            raise ValueError('float32 범위 초과') from exc
        if not all(math.isfinite(v) for v in x):
            raise ValueError('float32 범위 초과')
        total = 0.0
        for t in self.trees:
            i = depth = 0
            while t['left'][i] != -1:
                i = t['left'][i] if x[t['feature'][i]] <= t['threshold'][i] else t['right'][i]
                depth += 1
            total += depth + average_path(t['count'][i])
        return 2.0 ** (-total / self.denominator)

    def result(self, x):
        score = self.score(x)
        return {'status': 'outside_reference' if score > self.threshold else 'reference_like',
                'score': score, 'threshold': self.threshold,
                'score_meaning': '고장 확률이 아닌 학습 참조에서의 이례도',
                'probability': None, 'rul': None,
                'model_id': self.artifact.get('model_id'),
                'feature_definition': FEATURE_DEFINITION,
                'validation_scope': self.artifact.get('validation_scope', 'unverified')}
