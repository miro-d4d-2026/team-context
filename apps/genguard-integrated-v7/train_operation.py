"""Reproduce the synthetic phase-specific model and independent scenario checks."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random

from operation_fixtures import make_record, scenarios, seed_current, seed_records
from operation_model import OperationAnalyzer


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def train_and_evaluate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records = seed_records()
    analyzer = OperationAnalyzer(records)
    example_results = {name: analyzer.analyze(run) for name, run in scenarios().items()}
    analyzer.analyze(seed_current())
    exported = analyzer.export_model()
    report = {"data_source": "synthetic", "training_record_count": len(records), "training_intervals": 32,
              "evaluation_kind": "학습과 다른 난수로 만든 합성 시나리오의 동작 확인. 실제 발전기 진단 정확도 평가가 아님.", "cases": []}
    labels = {"normal": [], "coolant_rise": ["coolant_c"], "oil_drop": ["oil_kgf_cm2"],
              "vibration_increase": ["vibration_rms_g"], "combined": ["coolant_c", "oil_kgf_cm2", "vibration_rms_g"]}
    rng = random.Random(67291)
    for index in range(20):
        context = {"load_pct": 62 + rng.uniform(-1.5, 1.5), "ambient_c": 28.5 + rng.uniform(-1, 1),
                   "engine_rpm": 1800 + rng.uniform(-8, 8)}
        normal = make_record(f"evaluation-{index}", context, 7200 + index, "2026-10-11T10:00:00+09:00", confirmed_normal=False)
        for name, expected_fields in labels.items():
            run = copy.deepcopy(normal)
            for sample in run["samples"]:
                if name in {"coolant_rise", "combined"} and sample["elapsed_s"] >= 600:
                    sample["coolant_c"] += 12
                if name in {"oil_drop", "combined"} and sample["elapsed_s"] >= 900:
                    sample["oil_kgf_cm2"] -= .9
                if name in {"vibration_increase", "combined"} and sample["elapsed_s"] >= 900:
                    sample["vibration_rms_g"] *= 2
            result = analyzer.analyze(run)
            predicted = [c["field"] for c in result["channels"] if c["state"] == "inspect"]
            report["cases"].append({"id": run["id"], "scenario": name, "expected_fields": expected_fields,
                                    "observed_fields": predicted, "state": result["state"],
                                    "passed": predicted == expected_fields and result["state"] == ("normal" if not expected_fields else "inspect")})
    report["passed"] = sum(c["passed"] for c in report["cases"])
    report["total"] = len(report["cases"])
    report["normal_false_alerts"] = sum(c["state"] != "normal" for c in report["cases"] if c["scenario"] == "normal")
    # Examples expose the precise interval evidence without turning labels into
    # model inputs. The run selected for prediction is never in training.
    write_json(output / "normal-records.json", records)
    write_json(output / "scenarios.json", scenarios())
    write_json(output / "model.json", exported)
    write_json(output / "example-results.json", example_results)
    write_json(output / "validation.json", report)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob("*.json")) if p.name != "hashes.json"}
    write_json(output / "hashes.json", hashes)
    print(json.dumps({"output": str(output), "training_records": 8, "synthetic_cases": report["total"],
                      "passed": report["passed"], "normal_false_alerts": report["normal_false_alerts"]}, ensure_ascii=False))
    if report["passed"] != report["total"]:
        raise SystemExit("Some synthetic scenario checks failed; inspect validation.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(Path(__file__).with_name("operation_artifacts")))
    train_and_evaluate(parser.parse_args().output)
