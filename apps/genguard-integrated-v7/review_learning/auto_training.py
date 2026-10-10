"""One user action, run-separated fit/calibration/evaluation inside the service.

Human review is evidence, never an instruction to assign an ML split. Split
manifests belong to immutable candidates. Evaluation is deliberately not called
an independent final test when the same runs have been inspected before.
"""
import itertools
import json
import threading
from .model import PROFILES, values, fit_model, score
from .label_model import fit_label_model, predict_label

ROLES = ('fit', 'calibration', 'validation')
LABELS = ('normal', 'abnormal')
POLICY = {'version': 1, 'max_sessions': 30, 'min_windows_per_run_class': 30,
          'min_coverage': .95, 'fit_sessions_per_class': 2,
          'calibration_sessions_per_class': 1, 'validation_sessions_per_class': 1,
          'split_unit': 'independent_run', 'activation': 'explicit_next_run'}


def _allocate(records, required, previous_validation, previous_used):
    """Keep old evaluation runs out of fitting, including across model lanes."""
    eligible = [r for r in records if any(r['classes'].get(k) for k in required)]
    available = {r['id']: r for r in eligible}
    if any(sum(bool(r['classes'].get(label)) for r in eligible) < 4 for label in required):
        return None
    def enough(ids, minimum):
        return all(sum(bool(available[sid]['classes'].get(k)) for sid in ids) >= minimum for k in required)
    fixed = {r['id'] for r in eligible if r['id'] in previous_validation and r['id'] not in previous_used}
    # Historical fit or calibration runs never become held-out data on retraining.
    fresh = [r['id'] for r in reversed(eligible) if r['id'] not in previous_used | previous_validation]
    pool = [r['id'] for r in eligible if r['id'] not in previous_validation]
    for size in range(len(required) + 1):
        for extra in itertools.combinations(fresh, size):
            validation = fixed | set(extra)
            if not enough(validation, 1):
                continue
            remaining = [sid for sid in pool if sid not in validation]
            for count in range(1, len(required) + 1):
                for calibration in itertools.combinations(reversed(remaining), count):
                    fit = [sid for sid in remaining if sid not in calibration]
                    if enough(calibration, 1) and enough(fit, 2):
                        return {'fit': fit, 'calibration': list(calibration),
                                'validation': [r['id'] for r in eligible if r['id'] in validation]}
    return None


def _counts(records, ids, labels):
    by_id = {r['id']: r for r in records}
    out = {'sessions': len(ids), 'windows': 0, 'normal_sessions': 0,
           'abnormal_sessions': 0, 'normal_windows': 0, 'abnormal_windows': 0,
           'segmented_sessions': sum(by_id[sid]['review'] == 'segmented' for sid in ids)}
    for label in labels:
        out[label + '_sessions'] = sum(bool(by_id[sid]['classes'].get(label)) for sid in ids)
        out[label + '_windows'] = sum(len(by_id[sid]['classes'].get(label, [])) for sid in ids)
        out['windows'] += out[label + '_windows']
    return out


class AutomaticTrainingMixin:
    def _automatic_plan(self, ids):
        from .service import signature
        if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) for sid in ids) or len(set(ids)) != len(ids):
            raise ValueError('검토한 서로 다른 운전 기록을 선택하세요.')
        if len(ids) > POLICY['max_sessions']:
            raise ValueError('한 번에 최대 30회 기록을 학습할 수 있습니다. 기존 모델과 누적 기록은 유지됩니다.')
        records = sorted([self.session(sid) for sid in ids], key=lambda r: (r['started'], r['id']))
        sig = signature(records[0]['meta'])
        if any(signature(r['meta']) != sig for r in records):
            raise ValueError('장비·설치·입력 프로필·출처·운전 조건·센서 설정이 같은 기록만 함께 학습합니다.')
        keys = PROFILES[sig['profile']]['context'] + PROFILES[sig['profile']]['targets']
        usable, excluded = [], []
        for r in records:
            if not r['ended'] or r['review'] not in ('normal', 'abnormal', 'segmented'):
                excluded.append({'session_id': r['id'], 'reason': '종료 후 정상·비정상으로 확인한 기록만 사용합니다.'})
                continue
            rows = self._labeled_rows(r)
            classes, coverage = {}, {}
            for label in LABELS:
                labeled = [item for item in rows if item['label'] == label]
                good = []
                for item in labeled:
                    try:
                        values(item['frame'], keys)
                        good.append(item)
                    except ValueError:
                        pass
                coverage[label] = len(good) / max(1, len(labeled))
                if len(good) >= POLICY['min_windows_per_run_class'] and coverage[label] >= POLICY['min_coverage']:
                    classes[label] = good
                elif labeled:
                    excluded.append({'session_id': r['id'], 'label': label,
                                     'reason': '이 라벨의 유효 안정 운전 창 30개 이상·유효율 95% 이상이 필요합니다.'})
            if classes:
                usable.append({'id': r['id'], 'review': r['review'], 'classes': classes, 'coverage': coverage})
        previous = []
        for table in ('models', 'feedback_models'):
            previous.extend(json.loads(r[0]) for r in self.db.execute(f'SELECT data FROM {table}'))
        previous = [m for m in previous if m['signature'] == sig]
        seen_validation = set().union(*(set(m['splits'].get('validation', [])) for m in previous)) if previous else set()
        seen_used = set().union(*(set(m['splits'].get('fit', []) + m['splits'].get('calibration', [])) for m in previous)) if previous else set()
        feedback_splits = _allocate(usable, LABELS, seen_validation, seen_used)
        # Use a single partition when both models train: no run is fit in one
        # lane and presented as a held-out evaluation in the other lane.
        if feedback_splits:
            normal_splits = {role: [sid for sid in sids if next(r for r in usable if r['id'] == sid)['classes'].get('normal')]
                             for role, sids in feedback_splits.items()}
        else:
            normal_splits = _allocate(usable, ('normal',), seen_validation, seen_used)
        result = {'version': 1, 'signature': sig, 'ready': bool(normal_splits or feedback_splits),
                  'reason': '', 'excluded': excluded, 'policy': dict(POLICY)}
        for kind, splits, labels in [('normal', normal_splits, ('normal',)), ('feedback', feedback_splits, LABELS)]:
            if splits:
                used = list(dict.fromkeys(sum(splits.values(), [])))
                old_eval = [sid for sid in splits['validation'] if sid in seen_validation]
                new_eval = [sid for sid in splits['validation'] if sid not in seen_validation]
                evaluation = {'kind': 'mixed_holdout' if old_eval and new_eval else 'fixed_holdout' if old_eval else 'new_holdout',
                              'reused_session_ids': old_eval, 'new_session_ids': new_eval,
                              'independent_final_test': False,
                              'meaning': '운전 회차를 분리한 후보 평가. 반복 사용한 평가 기록은 새로운 최종 시험이 아닙니다.'}
                result[kind] = {'ready': True, 'reason': '', 'splits': splits, 'session_ids': used,
                                **_counts(usable, used, labels),
                                'counts_by_split': {role: _counts(usable, sids, labels) for role, sids in splits.items()},
                                'evaluation': evaluation}
            else:
                counts = _counts(usable, [r['id'] for r in usable], labels)
                requirement = '정상' if kind == 'normal' else '정상·비정상 각각'
                result[kind] = {'ready': False, 'splits': {}, 'session_ids': [], **counts,
                                'reason': f'{requirement} 유효 독립 기록 4회 이상이 필요합니다. 과거 학습·경계 조정에 쓰지 않은 평가 기록도 필요합니다.'}
        result['reason'] = ('정상 패턴과 정상·비정상 분류 후보를 함께 학습합니다.' if feedback_splits else
                            '정상 패턴 후보를 학습합니다. 비정상 사례 분류는 기록을 더 모은 뒤 추가합니다.' if normal_splits else
                            '학습 데이터 부족: ' + result['normal']['reason'])
        return result, usable

    def automatic_plan(self, ids):
        with self.lock:
            try:
                plan, _ = self._automatic_plan(ids)
                return plan
            except ValueError as exc:
                return {'ready': False, 'reason': str(exc), 'normal': {'ready': False, 'reason': str(exc)},
                        'feedback': {'ready': False, 'reason': str(exc)}, 'excluded': [], 'policy': dict(POLICY)}

    def _candidate_evaluation(self, model, items, kind):
        counts = {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0, 'unknown_windows': 0, 'total_windows': len(items)}
        for item in items:
            try:
                positive = (predict_label(model, item['frame'])['state'] == 'abnormal_like' if kind == 'feedback'
                            else score(model, item['frame'])['ratio'] > 1)
                actual = item['label'] == 'abnormal'
                counts['tp' if positive and actual else 'fp' if positive else 'fn' if actual else 'tn'] += 1
            except ValueError:
                counts['unknown_windows'] += 1
        n, a = counts['fp'] + counts['tn'], counts['tp'] + counts['fn']
        return {**counts, 'false_positive_rate': counts['fp'] / n if n else None,
                'abnormal_recall': counts['tp'] / a if a else None,
                'coverage': 1 - counts['unknown_windows'] / max(1, len(items)), 'unit': 'analysis_window'}

    def _compare_incumbent(self, model, items, kind):
        from .service import signature
        incumbent = self.compatible_feedback(model['signature']) if kind == 'feedback' else self.compatible_model(model['signature'])
        # signature() accepts the same metadata keys; its feature version is fixed.
        if not incumbent or signature(incumbent['signature']) != model['signature']:
            return {'available': False, 'reason': '동일 조건에 적용 중인 검토 학습 모델이 없습니다. 기존 실시간 진동 기준은 별도로 유지됩니다.'}
        used = set(incumbent['splits'].get('fit', []) + incumbent['splits'].get('calibration', []))
        held = {item['session_id'] for item in items}
        if held & used:
            return {'available': False, 'incumbent_id': incumbent['id'], 'reason': '평가 기록이 기존 모델의 학습에 사용되어 공정한 비교를 보류합니다.'}
        old = self._candidate_evaluation(incumbent, items, kind)
        new = self._candidate_evaluation(model, items, kind)
        if old['coverage'] < .95 or new['coverage'] < .95:
            return {'available': False, 'incumbent_id': incumbent['id'], 'reason': '두 모델의 유효 분석 범위가 충분히 겹치지 않아 개선 여부를 단정하지 않습니다.',
                    'incumbent': old, 'candidate': new}
        required_metrics = ('false_positive_rate', 'abnormal_recall') if kind == 'feedback' else ('false_positive_rate',)
        if any(old.get(metric) is None or new.get(metric) is None for metric in required_metrics):
            return {'available': False, 'incumbent_id': incumbent['id'],
                    'reason': '정상 또는 비정상 평가 창이 비교 가능한 조건에 없어 클래스별 성능 비교를 보류합니다.',
                    'incumbent': old, 'candidate': new}
        better = new['false_positive_rate'] < old['false_positive_rate']
        worse = new['false_positive_rate'] > old['false_positive_rate']
        if new['abnormal_recall'] is not None and old['abnormal_recall'] is not None:
            better |= new['abnormal_recall'] > old['abnormal_recall']
            worse |= new['abnormal_recall'] < old['abnormal_recall']
        return {'available': True, 'incumbent_id': incumbent['id'], 'incumbent': old, 'candidate': new,
                'session_ids': sorted(held), 'result': 'tradeoff' if better and worse else 'improved' if better else 'regressed' if worse else 'unchanged',
                'meaning': '같은 회차의 미학습 평가 창에서 비교. 개선 결과만으로 자동 교체하지 않습니다.'}

    def automatic_training(self, ids):
        from .service import pack
        with self.lock:
            if self.live_id or self.job['state'] == 'running':
                raise ValueError('운전과 현재 학습이 끝난 뒤 AI 학습을 시작하세요.')
            plan, records = self._automatic_plan(ids)
            if not plan['ready']:
                raise ValueError(plan['reason'])
            all_ids = sorted(set(plan['normal'].get('session_ids', []) + plan['feedback'].get('session_ids', [])))
            dependencies = {sid: self.feedback_fingerprint(sid) for sid in all_ids}
            by_id = {r['id']: r for r in records}
            self.cancel_train = threading.Event()
            cancel = self.cancel_train
            self.job = {'state': 'running', 'kind': 'auto', 'message': '승인 기록을 운전 회차별로 나누어 AI 학습 중',
                        'model_id': None, 'model_ids': {'normal': None, 'feedback': None}, 'plan': plan}
            def tick(message):
                if cancel.is_set():
                    raise InterruptedError('운전 감시를 우선하여 후보 학습을 취소했습니다.')
                with self.lock:
                    self.job['message'] = message
            def run():
                completed = {}
                candidates = []
                try:
                    for kind in ('normal', 'feedback'):
                        info = plan[kind]
                        if not info['ready']:
                            continue
                        labels = ('normal',) if kind == 'normal' else LABELS
                        dataset = {role: [item for sid in sids for label in labels for item in by_id[sid]['classes'].get(label, [])]
                                   for role, sids in info['splits'].items()}
                        dep = {sid: dependencies[sid] for sid in info['session_ids']}
                        if kind == 'normal':
                            model = fit_model(*([item['frame'] for item in dataset[role]] for role in ROLES),
                                              plan['signature']['profile'], plan['signature'], info['splits'], tick)
                            model['dependencies'] = dep
                        else:
                            coverage = {role: min(by_id[sid]['coverage'][label] for sid in sids for label in labels if by_id[sid]['classes'].get(label))
                                        for role, sids in info['splits'].items()}
                            model = fit_label_model(dataset, plan['signature']['profile'], plan['signature'], info['splits'], dep, coverage, tick)
                        model['fit_count'] = len(dataset['fit'])
                        model['auto_training'] = {**info, 'version': 1, 'requested_session_ids': list(ids),
                                                  'policy': dict(POLICY), 'user_role_assignment': False}
                        with self.lock:
                            model['auto_training']['incumbent_comparison'] = self._compare_incumbent(model, dataset['validation'], kind)
                            model['auto_training']['evaluation_metrics'] = self._candidate_evaluation(model, dataset['validation'], kind)
                        candidates.append((kind, model))
                    with self.lock:
                        if cancel.is_set() or self.live_id:
                            raise InterruptedError('새 운전이 시작되어 후보 저장을 취소했습니다. 기존 모델은 유지됩니다.')
                        if any(self.feedback_fingerprint(sid) != digest for sid, digest in dependencies.items()):
                            raise ValueError('학습 도중 검토 상태가 바뀌었습니다. 바뀐 기록으로 다시 학습하세요.')
                        for kind, model in candidates:
                            table = 'models' if kind == 'normal' else 'feedback_models'
                            self.db.execute(f'INSERT INTO {table}(id,data) VALUES(?,?)', (model['id'], pack(model)))
                            self.audit('automatic_trained', {'model_id': model['id'], 'kind': kind,
                                       'passed': model['passed'], 'splits': model['splits']})
                            completed[kind] = model['id']
                        self.db.commit()
                        passed = any(model['passed'] for _, model in candidates)
                        self.job = {'state': 'completed' if passed else 'rejected', 'kind': 'auto', 'model_id': None,
                                    'model_ids': {'normal': completed.get('normal'), 'feedback': completed.get('feedback')},
                                    'message': ('후보 학습·평가 완료. 성능을 확인하고 새 모델 적용을 선택하세요.' if passed else '평가 기준 미달. 기존 모델을 유지합니다.') +
                                               (' 비정상 분류는 독립 기록이 더 필요합니다.' if not plan['feedback']['ready'] else ''),
                                    'plan': plan}
                except Exception as exc:
                    with self.lock:
                        self.db.rollback()
                        self.job = {'state': 'cancelled' if isinstance(exc, InterruptedError) else 'failed',
                                    'kind': 'auto', 'message': str(exc), 'model_id': None,
                                    'model_ids': {'normal': None, 'feedback': None}, 'plan': plan}
            self.training_thread = threading.Thread(target=run, daemon=True)
            self.training_thread.start()
            return dict(self.job)
