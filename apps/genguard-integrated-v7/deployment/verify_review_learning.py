"""Isolated end-to-end learning proof; never touches the live workspace DB."""
import argparse
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning_workspace import LearningWorkspace


def verify():
    with tempfile.TemporaryDirectory(prefix='genguard-reviewed-proof-') as directory:
        workspace = LearningWorkspace(directory)
        service = workspace.service
        try:
            ids = {'normal': [], 'abnormal': []}
            for label in ids:
                for index, role in enumerate(('fit', 'fit', 'calibration', 'validation')):
                    sid = service.start(workspace._demo_meta(f'격리 검증 {label} {index}'))
                    seed = 5100 + index + (100 if label == 'abnormal' else 0)
                    scenario = 'normal' if label == 'normal' else 'vibration'
                    for frame in workspace._demo_frames(sid, seed, scenario, 300):
                        service.ingest(sid, frame)
                    service.stop('isolated_software_verification')
                    workspace.dispatch('review', dict(session_id=sid, status=label, role=role,
                        reviewer='자동 소프트웨어 검증', note='합성 시험의 입력 생성 조건 확인. 실제 발전기 정비 승인 아님.'))
                    ids[label].append(sid)

            def train(kind, selected):
                workspace.dispatch('train', {'kind': kind, 'session_ids': selected})
                end = time.monotonic() + 30
                while workspace.state()['job']['state'] == 'running' and time.monotonic() < end:
                    time.sleep(.05)
                state = workspace.state()
                assert state['job']['state'] == 'completed', state['job']
                model = state['models' if kind == 'normal' else 'feedback_models'][0]
                workspace.dispatch('activate', {'kind': kind, 'model_id': model['id']})
                return model

            normal = train('normal', ids['normal'])
            classifier = train('feedback', ids['normal'] + ids['abnormal'])
            sid = service.start(workspace._demo_meta('새 독립 합성 운전'))
            outputs = []
            for index, frame in enumerate(workspace._demo_frames(sid, 9601, 'normal', 12)):
                if index >= 5:
                    frame['readings']['vibration_rms_g']['value'] *= 2.2
                outputs.append(service.ingest(sid, frame))
            assert outputs[4]['feedback']['state'] == 'normal_like', outputs[4]
            assert outputs[-1]['feedback']['state'] == 'abnormal_like', outputs[-1]
            assert outputs[-1]['state'] == 'attention', outputs[-1]
            service.stop('isolated_software_verification')
            workspace.dispatch('note', {'session_id': sid, 'author': '자동 소프트웨어 검증', 'body': '재시작 후 보존 확인'})
            before = workspace.session_detail(sid)
            workspace.close()
            workspace = LearningWorkspace(directory)
            after = workspace.session_detail(sid)
            assert before['frames'] == after['frames']
            assert after['report']['notes'] == before['report']['notes']
            return {'host': platform.node(), 'architecture': platform.machine(), 'python': platform.python_version(),
                'data_source': 'isolated_synthetic', 'independent_synthetic_runs': 8,
                'live_state_modified': False, 'normal_core_algorithm': normal['core_algorithm'],
                'normal_validation': normal['validation'], 'normal_training_seconds': normal['training_seconds'],
                'classifier_algorithm': classifier['algorithm'], 'classifier_validation': classifier['validation'],
                'classifier_training_seconds': classifier['training_seconds'],
                'next_run': {'normal': outputs[4]['feedback']['state'], 'changed': outputs[-1]['feedback']['state'],
                             'final_state': outputs[-1]['state'], 'pinned_baseline': before['session']['model_id'],
                             'pinned_classifier': before['session']['feedback_model_id']},
                'restart_preserved_predictions_and_notes': True,
                'scope': '합성 입력으로 실제 학습·검증·활성화·다음 운전·재시작 경로를 확인. 실제 발전기 성능 아님.'}
        finally:
            workspace.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('architecture', 'data_source', 'next_run', 'restart_preserved_predictions_and_notes')}, ensure_ascii=False))
