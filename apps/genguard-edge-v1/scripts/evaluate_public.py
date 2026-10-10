"""Locked source-file split evaluation of real engine data, without fake IMU."""
import argparse
import csv
import json
from pathlib import Path
import platform
import resource
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from genguard.core import EdgeRuntime, atomic_json, validate_frame
from genguard.model import predict
from prepare_public import SPLITS, DATA_DIR, ARCHIVE_NAME, frame_iter, labeled_frame_iter


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    db = output / "public.sqlite"
    if db.exists():
        raise ValueError("Use a new output directory")
    engine = EdgeRuntime(db, ROOT / "profiles/marine-engine-public.json")
    archive = DATA_DIR / ARCHIVE_NAME
    group_ids = {}
    try:
        for role in ("fit", "calibration", "validation"):
            group_ids[role] = []
            for name in SPLITS[role]:
                sid, count = None, 0
                for frame in frame_iter(archive, name, "normal"):
                    if sid is None:
                        sid = frame["session_id"]
                        engine.start_session(sid, frame["installation_id"])
                    engine.ingest(frame)
                    count += 1
                if sid:
                    engine.close_session(sid)
                    engine.review_session(sid, "normal", "dataset-label-adapter", "저자 라벨0=고장 개입 전 구간. 실제 팀 장비의 정비사 점검 확인이 아님.")
                    group_ids[role].append(sid)
                print(json.dumps({"role": role, "file": name, "normal_rows_imported": count}), flush=True)
        try:
            bundle = engine.train_candidate(group_ids["fit"], group_ids["calibration"], group_ids["validation"])
        except ValueError as exc:
            report = {"status": "training_gate_rejected", "error": str(exc), "splits": SPLITS,
                      "scope": "실제 공개 엔진 시계열. IMU 없음. 분할/파라미터를 평가에 맞춰 고치지 않음."}
            atomic_json(output / "report.json", report)
            return report
        atomic_json(output / "candidate-model.json", bundle)
        accepted = bundle["validation"]["passed"]
        # Inspect a rejected candidate without activating or presenting it as an
        # operational model. Test labels NEVER enter model inputs or thresholds.
        summaries, traces = [], []
        for filename in SPLITS["test"]:
            counts = {str(label): {"total": 0, "unknown": 0, "change": 0, "within_baseline": 0} for label in (0, 1)}
            onset, first_after, first_before = None, None, None
            streak, previous_time = 0, None
            for frame, label in labeled_frame_iter(archive, filename):
                truth = label["anomaly_state"]
                if truth not in (0, 1):
                    continue
                if truth == 1 and onset is None:
                    onset = frame["elapsed_s"]
                flags = validate_frame(frame, engine.profile)
                if flags:
                    result = {"state": "unknown", "score": None, "threshold": None, "reason": "input_quality"}
                else:
                    result = predict(frame, engine.profile, bundle)
                change = result["state"] == "change"
                contiguous = previous_time is not None and frame["elapsed_s"] - previous_time <= engine.profile["max_window_gap_s"]
                streak = (streak + 1 if contiguous else 1) if change else 0
                persistent = streak >= engine.profile["model"]["persistence_windows"]
                if persistent:
                    if truth == 1 and first_after is None:
                        first_after = frame["elapsed_s"]
                    if truth == 0 and first_before is None:
                        first_before = frame["elapsed_s"]
                previous_time = frame["elapsed_s"]
                bucket = counts[str(truth)]
                bucket["total"] += 1
                bucket[result["state"]] += 1
                traces.append({"source_file": filename, "elapsed_s": frame["elapsed_s"], "truth": truth,
                               "state": result["state"], "score": result["score"], "threshold": result["threshold"],
                               "persistent_change": int(persistent), "quality_flags": ";".join(flags)})
            summaries.append({"source_file": filename, "labels": counts, "fault_intervention_s": onset,
                              "first_pre_intervention_alarm_s": first_before,
                              "first_post_intervention_alarm_s": first_after,
                              "post_intervention_delay_s": first_after - onset if first_after is not None else None,
                              "interpretation": "고장 개입 시점 이후 변화 검사. 고장 전 예지 시간이나 RUL이 아님."})
        with (output / "traces.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(traces[0]))
            writer.writeheader(); writer.writerows(traces)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2 if platform.system() == "Darwin" else 1024)
        detections = sum(s["first_post_intervention_alarm_s"] is not None for s in summaries)
        report = {"status": "evaluated_candidate_not_activated",
                  "normal_validation_passed": accepted,
                  "fault_trials_with_any_post_intervention_alarm": detections,
                  "fault_trials": len(summaries),
                  "operational_use_approved": False,
                  "source": "https://zenodo.org/records/19857425", "model_id": bundle["model_id"],
                  "scope": "실제 공개 선박 디젤엔진 시험대의 별도 원본 파일 평가. IMU/발전 전압·전류 없음.",
                  "algorithm": bundle["algorithm"], "validation": bundle["validation"], "training_seconds": bundle["training_seconds"],
                  "training_split": group_ids, "test": summaries,
                  "hardware": {"system": platform.system(), "machine": platform.machine(), "uno_q_measured": False},
                  "process_peak_rss_mib": rss,
                  "limitations": ["고장 개입 전후 라벨이며 실제 발전기 장기 열화/RUL 검증이 아님.",
                                  "동일 원본 파일의 정상 prefix와 고장 suffix를 서로 다른 train/test 역할에 넣지 않음.",
                                  "미학습 조건·품질불량은 unknown; 이를 정상 또는 탐지 성공으로 세지 않음.",
                                  "모델/프로파일/분할은 테스트 결과에 맞춰 변경하지 않음."]}
        atomic_json(output / "report.json", report)
        return report
    finally:
        engine.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    args = p.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
