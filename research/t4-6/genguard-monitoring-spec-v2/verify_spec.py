"""Check research fixture consistency, not real generator diagnostic accuracy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
def read(name):
    return json.loads((ROOT / name).read_text())

def main():
    files = sorted(p for p in ROOT.rglob("*.json") if p.name != "verification.json")
    for p in files:
        json.loads(p.read_text())
    fields = read("spec/field-catalog.json")["fields"]
    ids = [r["id"] for r in fields]
    assert len(ids) == len(set(ids))
    required = {"voltage_v", "current_a", "frequency_hz", "runtime_hours", "preheat_on",
                "oil_bar", "coolant_c", "battery_charge_reading", "voltage_selector",
                "current_selector", "oem_overspeed", "oem_low_oil", "oem_high_coolant",
                "oem_overload", "output_breaker_state"}
    assert required <= set(ids)
    profiles = read("spec/reference-profiles.json")
    assert profiles["army_requirement"]["rating"]["phases"] == 3
    assert profiles["manufacturer_reference"]["rating"]["phases"] == 1
    assert profiles["manufacturer_reference"]["low_oil_shutdown"]["operational_selected_value"] is None
    assert not profiles["operational_use_approved"]
    scenarios = read("spec/demo-scenarios.json")
    assert scenarios["source_kind"] == "scenario" and scenarios["is_example"]
    assert scenarios["runtime_drop_in"] is False
    baseline = scenarios["baseline"]
    cases = {c["id"]:c for c in scenarios["cases"]}
    assert len(cases) == len(scenarios["cases"])
    # All individual reference ranges still hold in the joint-change example.
    joint = baseline | cases["combined"]["changes"]
    assert 71 <= joint["coolant_c"] <= 91
    assert 1.94 <= joint["oil_bar"] <= 4.42
    assert joint["engine_rpm"] == 1800
    assert joint["load_pct"] == baseline["load_pct"]
    # Both explicitly coherent electrical cases obey the selected PF1 circuit.
    for name in ("normal", "rpm", "new_load", "output_trip"):
        s = baseline | cases[name]["changes"]
        assert abs(s["engine_rpm"] / 30 - s["frequency_hz"]) < 1e-9
        assert abs(s["active_power_kw"] - s["voltage_v"] * s["current_a"] / 1000) < 1e-9
        assert abs(s["load_pct"] - s["active_power_kw"] / 7.5 * 100) < 1e-9
    study = read("ai-study/results.json")
    splits = study["split"]
    fit, cal, test = (set(splits[k]) for k in ("fit_normal_runs", "calibration_normal_runs", "test_run_ids"))
    assert not (fit & cal or cal & test or fit & test)
    assert study["kind"] == "synthetic_design_study_not_generator_accuracy"
    assert study["host"]["not_uno_q_benchmark"]
    report = {"status":"passed", "json_files_parsed":len(files), "catalog_fields":len(ids),
              "army_audit_items":len(read("army-coverage.json")["items"]),
              "scenario_cases":len(cases), "model_study_test_runs":len(test),
              "validated":["mandatory monitoring field coverage", "separate model ratings",
                 "unresolved oil pressure conflict preserved", "inside-range joint example",
                 "coherent RPM-frequency and selected PF1 circuit", "distinct train/cal/test runs",
                 "simulation and deployment limitations retained"],
              "not_verified":["web integration", "UNO Q execution", "actual generator fault accuracy"]}
    (ROOT/"verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
