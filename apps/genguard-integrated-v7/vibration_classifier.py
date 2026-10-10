"""Small, offline classifier for the measured iPhone/IMU fixture.

Only RMS and envelope variation enter inference. Command labels remain in the
recordings solely for the separate evaluation view; this is not a fault model.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path

ARTIFACT_DIR = Path(__file__).resolve().parent / "replay_artifacts"
CLASS_NAMES = {"idle": "진동 미감지", "baseline": "기준 진동", "increased": "강한 진동", "intermittent": "간헐 진동"}
MODEL_ID = "phone-pattern-repeat12-cal3-v1"
MODEL_FILE = "phone-pattern-calibrated-v1.json"
POLICY = {
    "idle": ("idle", "진동이 거의 감지되지 않습니다.", "정지 구간인지 확인하세요. 진동이 없다는 사실만으로 장비 고장이라고 판단하지 않습니다.", ["장비가 운전 중인지 확인", "센서 부착 상태 확인"]),
    "baseline": ("normal", "학습한 기준 진동과 유사합니다.", "이번 2초 구간의 진동 세기와 시간 패턴이 기준 진동에 가장 가깝습니다.", []),
    "increased": ("inspect", "기준보다 강한 진동이 감지되었습니다.", "학습한 강한 진동과 유사합니다. 같은 부하와 부착 조건인지 확인한 뒤 점검하세요.", ["센서와 장비의 고정 상태 확인", "부하와 운전 조건 변화 확인", "진동 증가가 반복되면 담당 정비자에게 점검 요청"]),
    "intermittent": ("inspect", "진동이 끊기며 반복되는 패턴입니다.", "평균 세기가 낮아도 진동 세기의 시간 변동이 큽니다. 반복 진동과 운전 상태를 확인하세요.", ["주기적인 기동·정지 여부 확인", "센서 접촉과 장비 고정 상태 확인", "같은 운전 조건에서 반복되는지 확인"]),
}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


class VibrationClassifier:
    def __init__(self, artifact_dir=None):
        self.path = Path(artifact_dir) if artifact_dir is not None else ARTIFACT_DIR
        self.artifact = json.loads((self.path / MODEL_FILE).read_text())
        if self.artifact.get("model_id") != MODEL_ID or self.artifact.get("feature_definition") != "axis-demean-vector-rms__100ms-bin-demean-cv-v1":
            raise ValueError("Unsupported calibrated classifier contract")
        self.model = self.artifact["models"]["rms_and_pattern"]
        if self.model["features"] != ["log_rms", "envelope_cv"] or self.model["classes"] != list(CLASS_NAMES):
            raise ValueError("Unsupported classifier feature or class contract")
        vectors = [self.model["mean"], self.model["std"], *self.model["centroids"]]
        if any(len(v) != 2 or not all(math.isfinite(n) for n in v) for v in vectors) or len(self.model["centroids"]) != 4 or any(n <= 0 for n in self.model["std"]):
            raise ValueError("Invalid classifier parameters")
        if not math.isfinite(self.model.get("acceptance_radius", math.nan)) or self.model["acceptance_radius"] <= 0:
            raise ValueError("Invalid calibrated acceptance radius")
        self._catalog = json.loads((self.path / "catalog.json").read_text())
        self._catalog["model_id"] = MODEL_ID
        self._catalog["model"].update(training_windows=32, calibration_windows=16,
            fit_cycles=[1, 2], calibration_cycles=[3], unknown_rejection=True,
            acceptance_radius=self.model["acceptance_radius"], probability_calibrated=False)
        self._catalog["limits"] = [line for line in self._catalog["limits"] if "미지의 진동 거부" not in line]
        self._catalog["limits"].append("학습·보정 범위를 벗어나면 판단을 보류합니다. 거리 기준은 적은 표본의 가드이며 고장 확률·미지 패턴 전체 검증이 아닙니다.")
        for record in self._catalog["recordings"]:
            current = self.load_recording(record["id"])
            record["evaluation"] = current["evaluation"]
            if record["id"] == "training":
                record.update(title="반복 테스트 · 학습 32개 / 보정 16개", role="fit_and_calibration")
        self._catalog["evaluation"] = self.load_recording("random")["evaluation"]
        fit_baseline = [f["rms_g"] for f in self.load_recording("training")["frames"][:32] if f["expected_label"] == "baseline"]
        ordered = sorted(fit_baseline)
        self._catalog["model"]["baseline_rms_g"] = (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2

    def catalog(self):
        return deepcopy(self._catalog)

    def load_recording(self, recording_id):
        if recording_id not in ("training", "random"):
            raise ValueError("Unknown recording; choose training or random")
        record = json.loads((self.path / f"{recording_id}.json").read_text())
        # Stored signal features remain unchanged; evaluation follows the active model.
        record["evaluation"] = {name: self._evaluate(record["frames"], model)
            for name, model in self.artifact["models"].items()}
        record["evaluation"].update(role="held_out_recording" if recording_id == "random" else "fit_and_calibration",
                                     model_id=MODEL_ID)
        return record

    def _nearest(self, rms_g, envelope_cv, model):
        raw = {"log_rms": math.log(max(rms_g, 1e-9)), "envelope_cv": envelope_cv}
        z = [(raw[key] - mean) / scale for key, mean, scale in zip(model["features"], model["mean"], model["std"])]
        if not all(math.isfinite(value) for value in z):
            raise ValueError("Feature exceeds numeric range")
        distances = [math.hypot(*(value - center for value, center in zip(z, centroid))) for centroid in model["centroids"]]
        closest = min(range(len(distances)), key=distances.__getitem__)
        distance = distances[closest]
        label = model["classes"][closest] if distance <= model["acceptance_radius"] else "unknown"
        return label, distance

    def _evaluate(self, frames, model):
        classes = list(CLASS_NAMES)
        columns = classes + ["unknown"]
        confusion = [[0] * len(columns) for _ in classes]
        segments = {}
        for frame in frames:
            prediction, _ = self._nearest(frame["rms_g"], frame["envelope_cv"], model)
            # Command labels participate only in this evaluation, after prediction.
            expected = frame["expected_label"]
            confusion[classes.index(expected)][columns.index(prediction)] += 1
            segments.setdefault(frame["segment_index"], {"expected": expected, "predictions": []})["predictions"].append(prediction)
        correct = sum(confusion[i][i] for i in range(len(classes)))
        majority_correct = sum(max(columns, key=s["predictions"].count) == s["expected"] for s in segments.values())
        return {"correct": correct, "count": len(frames), "unknown": sum(row[-1] for row in confusion),
                "confusion": confusion, "class_order": classes, "confusion_columns": columns,
                "segment_majority_correct": majority_correct, "segment_count": len(segments),
                "unknown_rejection": True, "unknown_counts_as_incorrect": True, "model_id": MODEL_ID}

    def classify(self, rms_g, envelope_cv):
        rms_g, envelope_cv = _number(rms_g, "rms_g"), _number(envelope_cv, "envelope_cv")
        label, distance = self._nearest(rms_g, envelope_cv, self.model)
        metadata = {"model_id": MODEL_ID, "features": {"rms_g": rms_g, "envelope_cv": envelope_cv},
                    "scope": "동일한 아이폰·IMU 부착 조건에서 학습한 네 가지 진동의 유사도 분류",
                    "confidence": None, "unknown_rejection": True, "model_distance": distance,
                    "acceptance_radius": self.model["acceptance_radius"], "forced_choice": False,
                    "generator_fault_diagnosis": False}
        if label == "unknown":
            return {**metadata, "label": "unknown", "title": "학습 범위 밖 · 판단 보류", "label_ko": "판단 보류",
                    "state": "unavailable", "summary": "기록된 네 가지 진동과 충분히 유사하지 않습니다.",
                    "headline": "새로운 진동은 판단을 보류합니다.",
                    "detail": "부착 위치·센서·진동원이 학습 조건과 같은지 확인하세요. 학습 범위 밖이라는 사실만으로 고장이라고 판단하지 않습니다.",
                    "checks": ["센서 부착 위치와 진동원 확인", "기준 진동을 다시 측정해 비교"],
                    "unknown_reason": "outside_recorded_pattern_support"}
        state, summary, detail, checks = POLICY[label]
        return {"label": label, "title": CLASS_NAMES[label], "label_ko": CLASS_NAMES[label], "state": state,
                "summary": summary, "headline": summary, "detail": detail, "checks": list(checks),
                **metadata, "unknown_reason": None}


@lru_cache(maxsize=1)
def _default():
    return VibrationClassifier()


def catalog():
    return _default().catalog()


def load_recording(recording_id):
    return _default().load_recording(recording_id)


def classify(rms_g, envelope_cv):
    return _default().classify(rms_g, envelope_cv)
