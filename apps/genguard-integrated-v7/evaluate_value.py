"""Compare timing against the already shipped current-state model, without fitting."""
from __future__ import annotations

import json
from pathlib import Path
import statistics
import tempfile

from trend_service import TrendBridgeService
from trend_scenarios import scenario_catalog

ROOT = Path(__file__).resolve().parent


def main():
    model = json.loads((ROOT / "trend_artifacts/model.json").read_text())
    rows = []
    with tempfile.TemporaryDirectory(prefix="genguard-value-evaluation-") as state:
        service = TrendBridgeService(state)
        try:
            for spec in scenario_catalog():
                for seed in model["split"]["test_seeds"]:
                    result = service._experiment(spec["id"], spec, seed=seed, include_evaluation=False)
                    rows.append({"scenario": spec["id"], "seed": seed, **result["comparison"]})
        finally:
            service.close()
    summary = {}
    for spec in scenario_catalog():
        group = [row for row in rows if row["scenario"] == spec["id"]]
        added = [row["added_lead_vs_current_s"] for row in group if row["added_lead_vs_current_s"] is not None]
        lead = [row["trend_lead_s"] for row in group if row["trend_lead_s"] is not None]
        summary[spec["id"]] = {
            "label": spec["label"], "runs": len(group),
            "current_anomaly_alert_runs": sum(row["first_current_anomaly_s"] is not None for row in group),
            "trend_advisory_runs": sum(row["first_trend_advisory_s"] is not None for row in group),
            "fixed_warning_runs": sum(row["first_fixed_warning_s"] is not None for row in group),
            "trend_earlier_than_current_runs": sum(value > 0 for value in added),
            "trend_later_than_current_runs": sum(value < 0 for value in added),
            "median_added_lead_vs_current_s": statistics.median(added) if added else None,
            "median_lead_to_fixed_warning_s": statistics.median(lead) if lead else None,
        }
    report = {
        "source": "synthetic_held_out_runs", "model_id": "existing-v2-vs-trend-v3",
        "test_runs": len(rows), "test_seeds": model["split"]["test_seeds"],
        "summary": summary, "runs": rows,
        "interpretation": [
            "새 추세 모델은 이 비교 결과로 다시 선택하거나 학습하지 않았습니다.",
            "모든 방식은 같은 10초 간격의 모의 운전을 관측합니다. 실제 2초 입력의 현재 이상 탐지는 최대 16초 더 빠를 수 있습니다.",
            "음수 추가 시간은 추세 권고가 기존 이상 탐지보다 늦었다는 뜻입니다.",
            "일시 변화의 현재 이상 탐지는 실제 현재 편차를 발견한 것입니다. 미래 경계 접근을 잘못 예측한 오경보와 같은 지표로 취급하지 않습니다.",
            "경보 시각 차이는 정비사의 실제 조치 시간·효용을 검증한 결과가 아닙니다.",
            "정상 배경은 기존 모의 정상식과 맞췄습니다. 같은 합성 계열의 비교이며 실제 발전기 일반화 성능이 아닙니다.",
        ],
    }
    out = ROOT / "trend_artifacts/value_evaluation.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"test_runs": len(rows), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
