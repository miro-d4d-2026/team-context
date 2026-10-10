"""Rebuild fixture model + replay data from the two hash-checked source recordings.

Build dependencies: numpy, pandas. Runtime inference uses only Python stdlib.
No code is loaded from the ZIPs or original analysis scripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CLASSES = ["idle", "baseline", "increased", "intermittent"]
SOURCE_CONFIG = {
    "training": {"folder": "20261010T101556", "csv": "20261010T101556-f934655e.csv", "log": "399032D5-A905-4B15-B7C5-F5367DEFAFAA.json", "csv_sha256": "5695094e0a8f23a3a72571ed85252beb23920afc61b99aeb1661df0a4319599a", "log_sha256": "9cce93b375381750d77d8b9ffb3e20b417c2596c688e79854af46d145f7eb099", "zip": "반복테스트.zip", "zip_sha256": "36be913726a14ca6aaa8cd9b424460c312951a04ef794a000ed17cc0c694105e", "recipe": "datasetTraining", "title": "반복 테스트 · 학습 기록"},
    "random": {"folder": "20261010T104429", "csv": "20261010T104429-70687ed6.csv", "log": "4EBCA240-73C0-4FEC-999C-136073E774E8.json", "csv_sha256": "13d48102e02e945fd57c0e71caf28372059e6a06f5165594675fa84f65140cc7", "log_sha256": "6a04ebc1b085ccf5078edbf947600dbbfc62d48fe83195a04a1ecce1cf4601b7", "zip": "랜덤테스트.zip", "zip_sha256": "b320dca0e2d4ae37b83fa9acb3ed4baab2795390579554c2632274ae5f6fe84f", "recipe": "blindTest", "title": "랜덤 테스트 · 별도 검증 기록"},
}
FROZEN_SHA256 = "43590d31f824e1bea4b795d007a88d3a27cefd8dae76f9d96d305da27b524e19"
EXTRACTOR_SHA256 = "bf6ccbcd5e4cc186610894f7544defbf406d395a762bfe5d2be4cdddf6ac8c52"
LIMITS = ["아이폰 진동 실험이며 실제 발전기의 고장 정답이 아닙니다.", "별도 검증 기록은 1개이며, 48개 평가 창은 서로 연관된 데이터입니다.", "자극의 시작·종료를 아는 2초 구간만 평가했습니다. 전이·대기 구간은 포함하지 않았습니다.", "네 가지 중 가장 가까운 패턴을 고릅니다. 미지의 진동 거부나 신뢰도 보정은 없습니다.", "모델 실행은 Mac에서 검증했습니다. UNO Q의 실제 실행 성능과 새로운 부착 조건은 별도 검증이 필요합니다."]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_file(path, expected):
    if sha(path) != expected:
        raise ValueError(f"Source changed: {path.name}")


def read_sources(source_root, config):
    folder = source_root / config["folder"]
    csv_path, log_path = folder / "inputs" / config["csv"], folder / "inputs" / config["log"]
    check_file(csv_path, config["csv_sha256"])
    check_file(log_path, config["log_sha256"])
    # macOS names can be NFD. Compare normalized names, but do not extract ZIP contents.
    import unicodedata
    candidates = [p for p in source_root.glob("*.zip") if unicodedata.normalize("NFC", p.name) == config["zip"]]
    if len(candidates) != 1:
        raise ValueError(f"Require original user ZIP: {config['zip']}")
    check_file(candidates[0], config["zip_sha256"])
    with zipfile.ZipFile(candidates[0]) as archive:
        for filename, expected in [(config["csv"], config["csv_sha256"]), (config["log"], config["log_sha256"])]:
            names = [name for name in archive.namelist() if Path(name).name == filename and not name.startswith("__MACOSX/")]
            if len(names) != 1 or hashlib.sha256(archive.read(names[0])).hexdigest() != expected:
                raise ValueError(f"User ZIP does not contain expected input: {filename}")
    log = json.loads(log_path.read_text())
    if log["isPreview"] or log["outcome"] != "completed" or log["plan"]["recipe"] != config["recipe"]:
        raise ValueError("Require completed physical-device recording")
    return pd.read_csv(csv_path), log


def extract_windows(df, log):
    required = ["ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps", "elapsed_s", "t_us", "seq"]
    if not np.isfinite(df[required].to_numpy()).all():
        raise ValueError("Nonfinite sensor input")
    t = df.elapsed_s.to_numpy() - df.elapsed_s.iloc[0]
    if not np.all(np.diff(t) > 0) or not np.all(np.diff(df.seq) == 1):
        raise ValueError("Non-contiguous recording")
    a = df[["ax_g", "ay_g", "az_g"]].to_numpy()
    host = pd.to_datetime(df.host_time, utc=True)
    wall = (host - host.iloc[0]).dt.total_seconds().to_numpy()
    scale, _ = np.polyfit(wall, t, 1)
    nominal = (pd.Timestamp(log["commandSubmittedAt"]) - host.iloc[0]).total_seconds()
    markers = np.asarray(log["plan"]["syncPulseStarts"], dtype=float)
    expected = nominal + markers
    quiet = a[(t >= expected[0] - .65) & (t < expected[0] - .25)]
    if not len(quiet):
        raise ValueError("No quiet interval for sync reference")
    amplitude = np.linalg.norm(a - np.median(quiet, axis=0), axis=1)
    observed = []
    for guess in expected:
        candidates = np.flatnonzero((t >= guess - .3) & (t < guess + .5) & (amplitude > .03))
        if not len(candidates):
            raise ValueError("Sync pulse missing")
        observed.append(float(t[candidates[0]]))
    if not np.all(np.abs(np.diff(observed) - 1) < .04):
        raise ValueError("Invalid sync spacing")
    offset = float(np.median(np.asarray(observed) - scale * markers))
    command_t = (t - offset) / scale
    frames = []
    for segment_index, segment in enumerate(log["plan"]["segments"], 1):
        start, end = segment["relativeStart"], segment["relativeStart"] + segment["duration"]
        if command_t[0] > start or command_t[-1] < end:
            raise ValueError("Incomplete stimulus segment")
        for window_start in np.arange(start + .5, end - .5 - 2 + .00001, 2):
            selected = (command_t >= window_start) & (command_t < window_start + 2)
            sub_t, samples = command_t[selected], a[selected]
            if len(samples) < 400:
                raise ValueError("Insufficient samples in window")
            centered = samples - np.mean(samples, axis=0)
            rms = float(np.sqrt(np.mean(np.sum(centered * centered, axis=1))))
            indexes = np.floor((sub_t - window_start) / .1).astype(int)
            axes = ["ax_g", "ay_g", "az_g"]
            grouped = pd.DataFrame(samples, columns=axes).assign(bin=indexes).groupby("bin")[axes]
            variance, counts = grouped.var(ddof=0), grouped.size()
            env = np.sqrt(variance.sum(axis=1).to_numpy()[counts >= 5])
            cv = float(np.std(env) / max(np.mean(env), 1e-9))
            frames.append({"index": len(frames), "start_s": float(window_start), "end_s": float(window_start + 2), "samples": int(len(samples)), "rms_g": rms, "envelope_cv": cv, "segment_index": segment_index, "expected_label": segment["mode"]})
    if len(frames) != 48:
        raise ValueError("Expected 48 evaluation windows")
    return frames, {"scale": float(scale), "offset_s": offset, "marker_sensor_s": observed, "method": "Three sync pulses for offset; host/sensor timestamps for scale. No class labels or classification result used."}, {"raw_rows": len(df), "sensor_duration_s": float(t[-1]), "effective_hz": float((len(t)-1) / t[-1]), "missing_sequence_count": 0, "gaps_over_10ms": int((np.diff(t) > .01).sum()), "window_samples_min": min(f["samples"] for f in frames), "window_samples_max": max(f["samples"] for f in frames)}


def train(frames, use_pattern=True):
    features = ["log_rms", "envelope_cv"] if use_pattern else ["log_rms"]
    x = np.array([[np.log(max(f["rms_g"], 1e-9)), f["envelope_cv"]] for f in frames])[:, :len(features)]
    y = np.array([f["expected_label"] for f in frames])
    mean, std = x.mean(axis=0), np.maximum(x.std(axis=0), 1e-9)
    z = (x - mean) / std
    return {"features": features, "mean": mean.tolist(), "std": std.tolist(), "classes": CLASSES, "centroids": [z[y == label].mean(axis=0).tolist() for label in CLASSES]}


def predict(frame, model):
    x = np.array([np.log(max(frame["rms_g"], 1e-9)), frame["envelope_cv"]])[:len(model["features"])]
    z = (x - model["mean"]) / model["std"]
    return model["classes"][int(np.argmin(np.sum((z - np.array(model["centroids"])) ** 2, axis=1)))]


def evaluate(frames, model):
    predictions = [predict({"rms_g": f["rms_g"], "envelope_cv": f["envelope_cv"]}, model) for f in frames]
    matrix = [[sum(f["expected_label"] == actual and guess == predicted for f, guess in zip(frames, predictions)) for predicted in CLASSES] for actual in CLASSES]
    segments_correct = 0
    for segment in sorted({f["segment_index"] for f in frames}):
        votes = [p for f, p in zip(frames, predictions) if f["segment_index"] == segment]
        ordered = sorted([(votes.count(label), label) for label in CLASSES], reverse=True)
        winner = ordered[0][1] if ordered[0][0] > ordered[1][0] else None
        truth = next(f["expected_label"] for f in frames if f["segment_index"] == segment)
        segments_correct += int(winner == truth)
    return {"correct": sum(matrix[i][i] for i in range(4)), "count": len(frames), "confusion": matrix, "class_order": CLASSES, "segment_majority_correct": segments_correct, "segment_count": 12}


def build(source_root, output):
    frozen_path = source_root / SOURCE_CONFIG["training"]["folder"] / "frozen-classifiers.json"
    extractor = frozen_path.parent / "analysis-source.py"
    check_file(frozen_path, FROZEN_SHA256)
    check_file(extractor, EXTRACTOR_SHA256)
    original = json.loads(frozen_path.read_text())
    recordings = {}
    for identifier, config in SOURCE_CONFIG.items():
        df, log = read_sources(source_root, config)
        frames, alignment, quality = extract_windows(df, log)
        recordings[identifier] = {"id": identifier, "title": config["title"], "role": "training" if identifier == "training" else "held_out_evaluation", "run_id": log["id"], "recorded_at": log["commandSubmittedAt"], "frame_count": len(frames), "window_s": 2, "recording_elapsed_s": log["elapsedSeconds"], "evaluated_duration_s": 96, "source": "recorded_imu", "scope": "각 10초 자극에서 0.5–8.5초의 2초 창 4개만 재생합니다. 구간 사이의 대기·전이는 미평가입니다.", "alignment": alignment, "quality": quality, "frames": frames, "provenance": {"csv": config["csv"], "csv_sha256": config["csv_sha256"], "log": config["log"], "log_sha256": config["log_sha256"], "zip": config["zip"], "zip_sha256": config["zip_sha256"]}}
    if recordings["training"]["run_id"] == recordings["random"]["run_id"]:
        raise ValueError("Train and held-out recordings must differ")
    # Fit exclusively on the repeated recording; check against the pre-test frozen parameters.
    models = {"rms_only": train(recordings["training"]["frames"], False), "rms_and_pattern": train(recordings["training"]["frames"])}
    max_error = 0.0
    for name, model in models.items():
        for key in ["mean", "std", "centroids"]:
            error = float(np.max(np.abs(np.array(model[key]) - np.array(original["models"][name][key]))))
            max_error = max(max_error, error)
            if not np.allclose(model[key], original["models"][name][key], atol=1e-12, rtol=1e-12):
                raise ValueError("Rebuilt training parameters differ from frozen pre-test model")
    # Ship the original frozen numeric parameters, after proving retraining reproduces them.
    artifact = {"schema_version": "genguard.vibration-fixture-model/1", "model_id": "iphone-imu-standardized-nearest-centroid/1", "training_run_id": recordings["training"]["run_id"], "training_recordings": 1, "training_samples": 48, "held_out_run_id": recordings["random"]["run_id"], "models": original["models"], "preprocessing": original["preprocessing"], "baseline_training_rms_median_g": float(np.median([f["rms_g"] for f in recordings["training"]["frames"] if f["expected_label"] == "baseline"])), "reproduction_max_parameter_error": max_error, "frozen_source_sha256": FROZEN_SHA256, "original_extractor_sha256": EXTRACTOR_SHA256, "trained_with_test_data": False, "limits": LIMITS}
    for identifier, record in recordings.items():
        record["evaluation"] = {name: evaluate(record["frames"], model) for name, model in artifact["models"].items()}
        record["evaluation"]["role"] = "in_sample" if identifier == "training" else "held_out_recording"
    result = recordings["random"]["evaluation"]
    if result["rms_only"]["correct"] != 36 or result["rms_and_pattern"]["correct"] != 48:
        raise ValueError("Recomputed held-out result changed")
    catalog = {"schema_version": "genguard.vibration-catalog/1", "model_id": artifact["model_id"], "default_recording_id": "random", "model": {"training_recordings": 1, "training_windows": 48, "features": ["RMS", "100ms 세기 변동 CV"], "baseline_rms_g": artifact["baseline_training_rms_median_g"], "trained_with_test_data": False}, "recordings": [{k: r[k] for k in ["id", "title", "role", "run_id", "recorded_at", "frame_count", "window_s", "recording_elapsed_s", "evaluated_duration_s", "source", "scope", "evaluation"]} for r in recordings.values()], "evaluation": result, "class_names": {"idle": "진동 미감지", "baseline": "기준 진동", "increased": "강한 진동", "intermittent": "간헐 진동"}, "limits": LIMITS}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in {"model": artifact, "catalog": catalog, **recordings}.items():
        (output / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    shutil.copyfile(frozen_path, output / "frozen-original.json")
    manifest = {"builder": "build_vibration_replay.py", "builder_sha256": sha(Path(__file__)), "source_zip_bytes_verified": True, "raw_recomputed": True, "frozen_parameters_reproduced": True, "reproduction_max_parameter_error": max_error, "artifacts": {p.name: sha(p) for p in sorted(output.glob("*.json")) if p.name != "manifest.json"}, "note": "Original frozen artifact retains its historical pre-evaluation status. Current evaluation is catalog.json and random.json."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"training_windows": 48, "held_out_windows": 48, "rms_only_correct": 36, "rms_and_pattern_correct": 48, "max_parameter_error": max_error, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=HERE.parent / "iphone-imu")
    parser.add_argument("--output", type=Path, default=HERE / "replay_artifacts")
    args = parser.parse_args()
    build(args.source_root, args.output)
