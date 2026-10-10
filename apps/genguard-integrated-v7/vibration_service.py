"""Replay measured iPhone/IMU windows through the trained classifier on this device.

The phone fixture and the synthetic generator model keep separate provenance.
The browser chooses a window, never its features or its predicted class.
"""
from __future__ import annotations

import copy

from bridge_service import BridgeError
from inspection_service import InspectionService
from vibration_classifier import VibrationClassifier


class VibrationService(InspectionService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vibration_classifier = VibrationClassifier()
        self.active_replay = None

    def vibration_catalog(self):
        result = copy.deepcopy(self.vibration_classifier.catalog())
        for record in result['recordings']:
            record['label'] = record.get('label') or record.get('title') or record['id']
        return result

    def vibration_replay(self, body):
        allowed = {'recording_id', 'frame_index', 'values', 'reference_id'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise BridgeError(422, 'invalid_replay', '기록과 측정 구간을 선택하세요.')
        if body.get('recording_id') not in ('training', 'random'):
            raise BridgeError(422, 'unknown_recording', '등록된 실측 기록을 선택하세요.')
        index = body.get('frame_index')
        if type(index) is not int:
            raise BridgeError(422, 'invalid_frame', '측정 구간 번호를 확인하세요.')
        record = self.vibration_classifier.load_recording(body['recording_id'])
        if not 0 <= index < len(record['frames']):
            raise BridgeError(422, 'invalid_frame', '측정 구간 번호를 확인하세요.')
        if not isinstance(body.get('values'), dict) or 'vibration_rms_g' in body['values']:
            raise BridgeError(422, 'recording_override_forbidden', '진동은 저장된 실측 기록에서 가져옵니다.')
        frame = record['frames'][index]
        values = dict(body['values'], vibration_rms_g=frame['rms_g'])
        current, _, reference = self._input({'mode': 'scenario', 'values': values,
                                             'reference_id': body.get('reference_id')})
        current.update(source='replay', installation_id='iphone-imu-20261010-fixed-fixture')
        current['sources']['vibration_rms_g'] = 'recorded_imu'
        current['acquisition'] = {'sample_rate_hz': 209.03, 'window_s': 2,
                                  'dc_removed': True, 'feature_version': 'iphone-imu-window/1'}
        current['replay_window'] = {'recording_id': record['id'], 'frame_index': index,
                                    'start_s': frame['start_s'], 'end_s': frame['end_s']}
        result = super()._overview(current, 'replay', reference)

        # Ground truth is deliberately read only AFTER the feature-only inference.
        inferred = self.vibration_classifier.classify(frame['rms_g'], frame['envelope_cv'])
        label = inferred['label']
        title, state, summary, checks = (inferred[key] for key in ('title', 'state', 'summary', 'checks'))
        prediction = {key: copy.deepcopy(inferred[key]) for key in
                      ('label', 'title', 'state', 'summary', 'checks', 'model_id')}
        prediction['basis'] = '반복 수집 기록의 진동 세기·시간 패턴으로 학습한 분류 모델'
        operational = result['operational_status']
        if state == 'inspect':
            operational['alerts'].append({'id': 'recorded_vibration_pattern', 'title': title + ' · 점검 권고',
                'severity': 'watch', 'field': 'vibration_rms_g', 'source': 'recorded_imu',
                'reason': summary, 'checks': checks, 'basis': {'kind': 'learned_phone_fixture_pattern'}})
        non_vibration = [c for c in result['comparison']['channels'] if c['field'] != 'vibration_rms_g']
        if operational['state'] != 'urgent':
            if operational['alerts'] or any(c['state'] == 'inspect' for c in non_vibration):
                operational['state'] = 'inspect'
                operational['headline'] = '진동 변화 확인이 필요합니다' if state == 'inspect' else '운전 값 변화 확인이 필요합니다'
                operational['summary'] = summary if state == 'inspect' else result['comparison']['summary']
            elif state in ('idle', 'unavailable'):
                operational.update(state='unavailable', headline='진동원 정지 여부를 확인하세요' if state == 'idle' else '학습한 진동 패턴 밖입니다', summary=summary)
            elif any(c['state'] == 'unavailable' for c in non_vibration):
                operational.update(state='unavailable', headline='일부 항목은 비교 기준이 필요합니다',
                    summary='진동은 기준 패턴과 비슷합니다. 수온·유압의 비교 조건을 확인하세요.')
            else:
                operational.update(state='normal', headline='학습한 기준 진동과 비슷합니다',
                    summary='실측 기록의 진동은 기준 패턴입니다. 모의 수온·유압도 이전 점검과 비슷합니다.')
        result['vibration_replay'] = {
            'recording_id': record['id'], 'recording_label': record.get('title', record['id']),
            'recorded_at': record['recorded_at'], 'analyzed_at': self._now(),
            'frame_index': index, 'frame_count': len(record['frames']),
            'start_s': frame['start_s'], 'end_s': frame['end_s'], 'samples': frame['samples'],
            'rms_g': frame['rms_g'], 'envelope_cv': frame['envelope_cv'], 'prediction': prediction,
            'evaluation': {'expected_label': frame['expected_label'], 'correct': label == frame['expected_label']},
            'scope': '2초 평가 구간 재생 · 전환 구간 제외 · 아이폰 진동 실험',
        }
        result['provenance_note'] = '진동은 아이폰·IMU 실측 기록 재생입니다. 수온·유압·운전 조건은 모의 값이며, 발전기 고장 진단 검증이 아닙니다.'
        result['model_scope'] = '실측 진동 패턴 분류와 모의 발전기 정상 기록 비교를 각각 수행합니다. 두 데이터는 함께 학습하지 않습니다.'
        self.preview, self.preview_mode, self.preview_reference = copy.deepcopy(current), 'replay', reference
        self.active_replay = copy.deepcopy(body)
        return result

    def inspection_overview(self):
        if self.active_replay is not None:
            return self.vibration_replay(self.active_replay)
        return super().inspection_overview()

    def inspection_compare(self, body):
        result = super().inspection_compare(body)
        self.active_replay = None
        return result

    def inspection_save(self, body):
        if self.active_replay is not None:
            raise BridgeError(409, 'replay_not_new_inspection', '실측 기록 재생은 새 점검으로 저장하지 않습니다.')
        return super().inspection_save(body)

    def inspection_retrain(self, body):
        if self.active_replay is not None:
            raise BridgeError(409, 'replay_not_training_input', '재생 기록은 발전기 정상 학습에 추가하지 않습니다.')
        return super().inspection_retrain(body)
