"""Export learned inspection model and evaluate unseen complete synthetic runs."""
from __future__ import annotations

import json
from pathlib import Path

from inspection_fixtures import make_record, seed_current, seed_records
from inspection_model import InspectionComparator


def main():
    output = Path(__file__).parent / "inspection_artifacts"
    output.mkdir(exist_ok=True)
    records = seed_records()
    model = InspectionComparator(records)
    cases = {"normal": {}, "condition_change": {}, "coolant_rise": {"coolant_c": 7},
             "oil_drop": {"oil_kgf_cm2": -.65}, "vibration_increase": {"vibration_rms_g": 1.65},
             "combined": {"coolant_c": 7, "oil_kgf_cm2": -.65, "vibration_rms_g": 1.65}}
    rows, summary = [], {}
    for scenario_index, (scenario, changes) in enumerate(cases.items()):
        case_rows = []
        for i in range(20):
            context = {"load_pct": 62, "ambient_c": 28.5, "engine_rpm": 1800, "elapsed_s": 600}
            if scenario == "condition_change":
                context.update(load_pct=66.5, ambient_c=32.5, engine_rpm=1810, elapsed_s=690)
            record = make_record(f"heldout-{scenario}-{i}", context, 3000 + scenario_index * 100 + i, "2026-11-01T10:00:00+09:00", confirmed_normal=False)
            for field, change in changes.items():
                record["values"][field] = record["values"][field] * change if field == "vibration_rms_g" else record["values"][field] + change
            result = model.compare(record)
            case_rows.append({"id": record["id"], "state": result["state"],
                              "flagged_fields": [c["field"] for c in result["channels"] if c["state"] == "inspect"],
                              "coolant_raw_delta": result["channels"][0]["delta"],
                              "coolant_adjusted_delta": result["channels"][0]["adjusted_delta"]})
        summary[scenario] = {"runs": 20, "inspect": sum(r["state"] == "inspect" for r in case_rows),
                             "normal": sum(r["state"] == "normal" for r in case_rows),
                             "unavailable": sum(r["state"] == "unavailable" for r in case_rows),
                             "exact_target_match": sum(set(r["flagged_fields"]) == set(changes) for r in case_rows)}
        rows.extend(case_rows)
    report = {"schema_version": "genguard.inspection-evaluation/1", "data_source": "synthetic",
              "training": {"whole_runs": 8, "windows": 240, "seeds": [1100, 1107]},
              "evaluation": {"whole_runs": 120, "seeds": {name: [3000 + i * 100, 3019 + i * 100] for i, name in enumerate(cases)},
                             "split": "Complete unseen runs; no shuffled-row split, no test rows used for fitting."},
              "policy_frozen_before_evaluation": True, "summary": summary, "runs": rows,
              "limitations": ["The same synthetic equation family creates training and evaluation; no generator validation.",
                              "Each test record is a 30-window mean. Single-window real-time noise is covered only by targeted tests, not this performance table.",
                              "Demonstration inspection thresholds are not OEM protection limits.",
                              "Physical IMU plus simulated generator context is excluded from contextual vibration fitting.",
                              "No failure timing, remaining life, or fault-cause classification is measured."]}
    for name, data in (("model.json", model.export_model(seed_current())), ("seed-history.json", records), ("evaluation.json", report)):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
