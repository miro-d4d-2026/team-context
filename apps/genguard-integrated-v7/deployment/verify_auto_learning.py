"""Isolated automatic-learning proof. Never opens the live operation database."""
import argparse
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning_workspace import LearningWorkspace
from review_learning.service import check_meta


def verify():
    with tempfile.TemporaryDirectory(prefix='genguard-auto-proof-') as directory:
        workspace = LearningWorkspace(directory)
        service = workspace.service
        try:
            normal_ids, abnormal_ids = [], []

            def add_run(label, index):
                sid = service.start(workspace._demo_meta(f'Auto proof {label} {index}'))
                seed = 5100 + index + (100 if label == 'abnormal' else 0)
                for frame in workspace._demo_frames(sid, seed, 'vibration' if label == 'abnormal' else 'normal', 300):
                    service.ingest(sid, frame)
                service.stop('isolated_automatic_learning_test')
                workspace.dispatch('review', {'session_id': sid, 'status': label,
                    'reviewer': 'Automatic software verification',
                    'note': 'Synthetic generator input; not human approval of a real generator.'})
                assert service.session(sid)['role'] == 'none'
                return sid

            def train(ids):
                plan = workspace.dispatch('training-plan', {'session_ids': ids})
                assert plan['ready'], plan
                workspace.dispatch('train', {'kind': 'auto', 'session_ids': ids})
                service.training_thread.join(45)
                state = workspace.state()
                assert state['job']['state'] == 'completed', state['job']
                models = {}
                for kind, mid in state['job']['model_ids'].items():
                    if mid:
                        models[kind] = next(m for m in state['models' if kind == 'normal' else 'feedback_models'] if m['id'] == mid)
                        assert not models[kind]['active']
                        assert models[kind]['passed'], models[kind]['validation']
                assert all(service.session(sid)['role'] == 'none' for sid in ids)
                return plan, models

            for index in range(4):
                normal_ids.append(add_run('normal', index))
            initial_plan, initial = train(normal_ids)
            assert 'feedback' not in initial
            assert initial['normal']['auto_training']['evaluation_metrics']['abnormal_recall'] is None
            workspace.dispatch('activate', {'kind': 'normal', 'model_id': initial['normal']['id']})
            first_holdout = set(initial['normal']['splits']['validation'])

            for index in (4, 5):
                normal_ids.append(add_run('normal', index))
            retrain_plan, retrained = train(normal_ids)
            assert first_holdout <= set(retrained['normal']['splits']['validation'])
            assert not first_holdout & set(retrained['normal']['splits']['fit'])
            assert service.compatible_model(check_meta(workspace._demo_meta('check')))['id'] == initial['normal']['id']
            comparison = retrained['normal']['auto_training']['incumbent_comparison']
            assert comparison['available'], comparison
            workspace.dispatch('activate', {'kind': 'normal', 'model_id': retrained['normal']['id']})

            for index in range(4):
                abnormal_ids.append(add_run('abnormal', index))
            both_plan, both = train(normal_ids + abnormal_ids)
            assert set(both) == {'normal', 'feedback'}
            for kind, model in both.items():
                assert not first_holdout & set(model['splits']['fit'] + model['splits']['calibration'])
                workspace.dispatch('activate', {'kind': kind, 'model_id': model['id']})

            sid = service.start(workspace._demo_meta('New independent synthetic run'))
            outputs = []
            for index, frame in enumerate(workspace._demo_frames(sid, 9601, 'normal', 12)):
                if index >= 5:
                    frame['readings']['vibration_rms_g']['value'] *= 2.2
                outputs.append(service.ingest(sid, frame))
            assert outputs[4]['feedback']['state'] == 'normal_like', outputs[4]
            assert outputs[-1]['feedback']['state'] == 'abnormal_like', outputs[-1]
            assert outputs[-1]['state'] == 'attention', outputs[-1]
            service.stop('isolated_automatic_learning_test')
            workspace.dispatch('note', {'session_id': sid, 'author': 'Software verification', 'body': 'Auto-learning report persistence'})
            before = workspace.session_detail(sid)
            workspace.close()
            workspace = LearningWorkspace(directory)
            after = workspace.session_detail(sid)
            assert before['frames'] == after['frames']
            assert before['report']['notes'] == after['report']['notes']
            return {'architecture': platform.machine(), 'python': platform.python_version(),
                'source': 'isolated_synthetic', 'live_state_modified': False,
                'normal_runs': len(normal_ids), 'abnormal_runs': len(abnormal_ids),
                'no_manual_roles': True, 'normal_only_initial_learning': True,
                'retraining_keeps_holdout': True, 'explicit_activation_required': True,
                'incumbent_comparison': comparison,
                'final_models': {k: {'id': m['id'], 'training_seconds': m['training_seconds'],
                    'evaluation': m['auto_training']['evaluation_metrics'],
                    'split_counts': m['auto_training']['counts_by_split']} for k, m in both.items()},
                'next_run': {'normal': outputs[4]['feedback']['state'], 'changed': outputs[-1]['feedback']['state'],
                    'warning': outputs[-1]['state']}, 'restart_preserved_report': True,
                'scope': 'Actual ML training and automatic per-run splitting on synthetic inputs; not real generator accuracy.'}
        finally:
            workspace.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('architecture', 'source', 'no_manual_roles', 'next_run', 'restart_preserved_report')}))
