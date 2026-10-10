"""Causal operational evaluation on frozen, held-out synthetic telemetry runs.

No parameter/threshold tuning. The detector only receives each frame when its
time is reached; future records and simulator labels are used for scoring only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import time

from contract import FIELDS
from runtime import Monitor

ROOT = Path(__file__).resolve().parent
PRIMARY = {"cooling_decline":"coolant_c", "oil_decline":"oil_pressure_kgf_cm2",
           "voltage_drift":"voltage_v", "imbalance":"current_a", "charging_decline":"battery_v",
           "fuel_decline":"fuel_pressure_bar", "recovery":"coolant_c"}


def first(items):
    return next(iter(items),None)


def imbalance(frame):
    currents=[frame[k] for k in ("current_a","current_b","current_c")]
    mean=sum(currents)/3
    return 100*max(abs(x-mean) for x in currents)/max(mean,.1)


def replay_run(frames):
    monitor=Monitor(); scenario=frames[0]["scenario"];onset=frames[0]["injection_onset_s"]
    rows=[];errors=[]; transitions=[];previous=None
    for frame in frames:
        # Hidden labels, onset and intervention times never reach Monitor.
        sample={k:frame[k] for k in FIELDS+["timestamp_s","run_id","phase","data_source"]}
        try:
            output=monitor.update(sample)
            row={"t":frame["timestamp_s"],"state":output["state"],"alerts":output["alerts"],
                 "crossings":output["crossings"],"reason":output["reason"]}
        except Exception as exc:
            errors.append({"timestamp_s":frame["timestamp_s"],"type":type(exc).__name__,"message":str(exc)})
            row={"t":frame["timestamp_s"],"state":"error","alerts":[],"crossings":[],"reason":str(exc)}
        if row["state"]!=previous:
            transitions.append({"timestamp_s":row["t"],"from":previous,"to":row["state"],
                                "sources":sorted({a["source"] for a in row["alerts"]})})
        previous=row["state"];rows.append(row)
    raw_thresholds=[]
    for rule in monitor.profile["rules"]:
        key=rule["field"]
        for level in ("inspection","urgent"):
            if level not in rule:continue
            is_cross=lambda v: v>=rule[level] if rule["direction"]=="high" else v<=rule[level]
            crossing=first(f["timestamp_s"] for f in frames if is_cross(f[key]))
            fixed_alert=first(r["t"] for r in rows if any(a["source"]=="fixed_rule" and a["field"]==key and a["level"]==level for a in r["alerts"]))
            raw_thresholds.append({"field":key,"level":level,"direction":rule["direction"],"threshold":rule[level],
                                   "first_raw_crossing_s":crossing,"first_fixed_alert_s":fixed_alert})
    raw_thresholds.append({"field":"current_a","level":"inspection","direction":"high","threshold":10,
                           "derived":"phase_current_imbalance_pct","first_raw_crossing_s":first(f["timestamp_s"] for f in frames if imbalance(f)>10),
                           "first_fixed_alert_s":first(r["t"] for r in rows if any(a["source"]=="demo_imbalance_rule" for a in r["alerts"]))})
    leads=[]
    for boundary in raw_thresholds:
        crossing=boundary["first_raw_crossing_s"]
        # Earliest sustained learned warning for the same channel, not a generic warning on another channel.
        warning=first(r["t"] for r in rows if any(a["field"]==boundary["field"] and a["source"] in ("learned_forecast","learned_baseline") for a in r["alerts"]))
        if warning is not None or crossing is not None:
            leads.append({**boundary,"first_learned_warning_s":warning,
                          "realized_advance_warning_s":crossing-warning if crossing is not None and warning is not None and warning<crossing else None})
    first_forecasts=[]
    seen=set()
    for row in rows:
        for forecast in row["crossings"]:
            key=(forecast["field"],forecast["level"],forecast["direction"],forecast["kind"])
            if not forecast["sustained"] or key in seen:continue
            seen.add(key)
            boundary=next((b for b in raw_thresholds if b["field"]==forecast["field"] and b["level"]==forecast["level"] and b["direction"]==forecast["direction"]),None)
            actual=boundary["first_raw_crossing_s"] if boundary else None
            first_forecasts.append({"field":forecast["field"],"level":forecast["level"],"at_s":row["t"],"predicted_eta_s":forecast["eta_s"],
                                    "actual_first_raw_crossing_s":actual,"realized_lead_s":actual-row["t"] if actual is not None and actual>row["t"] else None,
                                    "crossed_within_180s":bool(actual is not None and row["t"]<actual<=row["t"]+180)})
    alerts=[r for r in rows if r["state"] in ("inspection","urgent")]
    after=[r for r in alerts if onset is None or r["t"]>=onset]
    normal_case=scenario in ("normal","load_transient")
    intervention=frames[0]["intervention_s"]
    recovered=None
    if intervention is not None:
        recovered=first(r["t"] for r in rows if r["t"]>=intervention and r["state"]=="normal" and any(a["t"]<r["t"] for a in alerts))
    return {"run_id":frames[0]["run_id"],"scenario":scenario,"frames":len(frames),"onset_s":onset,"state_counts":dict(Counter(r["state"] for r in rows)),
            "transitions":transitions,"normal_false_alert_run":bool(normal_case and alerts),
            "normal_false_inspection_events":sum(x["to"]=="inspection" for x in transitions) if normal_case else None,
            "normal_false_urgent_events":sum(x["to"]=="urgent" for x in transitions) if normal_case else None,
            "first_non_normal_alert_s":alerts[0]["t"] if alerts else None,
            "first_post_onset_alert_s":after[0]["t"] if after else None,
            "detection_delay_after_hidden_onset_s":after[0]["t"]-onset if after and onset is not None else None,
            "pre_onset_alert_windows":sum(r["t"]<onset for r in alerts) if onset is not None else None,
            "raw_threshold_crossings":raw_thresholds,"field_matched_warning_leads":leads,"first_sustained_forecasts":first_forecasts,
            "intervention_s":intervention,"first_recovered_normal_s":recovered,
            "recovery_delay_from_intervention_s":recovered-intervention if recovered is not None else None,
            "recovery_meaning":"Delay after start of hidden 150s simulated repair ramp; includes physical thermal relaxation and 20s normal hold, not merely detector latency." if intervention else None,
            "errors":errors}


def main():
    started=time.perf_counter();path=ROOT/"data/telemetry-test.jsonl.gz"
    grouped=defaultdict(list)
    with gzip.open(path,"rt",encoding="utf-8") as handle:
        for line in handle:
            frame=json.loads(line);grouped[frame["run_id"]].append(frame)
    results=[replay_run(frames) for frames in grouped.values()]
    normal=[r for r in results if r["scenario"] in ("normal","load_transient")]
    forecast_events=[event for run in results for event in run["first_sustained_forecasts"]]
    recovery_delays=[run["recovery_delay_from_intervention_s"] for run in results if run["recovery_delay_from_intervention_s"] is not None]
    summaries={}
    for scenario in sorted({r["scenario"] for r in results}):
        runs=[r for r in results if r["scenario"]==scenario]
        delays=[r["detection_delay_after_hidden_onset_s"] for r in runs if r["detection_delay_after_hidden_onset_s"] is not None]
        primary=PRIMARY.get(scenario)
        leads=[b["realized_advance_warning_s"] for r in runs for b in r["field_matched_warning_leads"] if b["field"]==primary and b["level"]=="inspection" and b["realized_advance_warning_s"] is not None]
        summaries[scenario]={"runs":len(runs),"alerted_runs":sum(r["first_non_normal_alert_s"] is not None for r in runs),
                             "pre_onset_alert_runs":sum(bool(r["pre_onset_alert_windows"]) for r in runs),
                             "detection_delay_min_s":min(delays) if delays else None,"detection_delay_max_s":max(delays) if delays else None,
                             "primary_field_realized_lead_count":len(leads),"primary_field_lead_min_s":min(leads) if leads else None,
                             "primary_field_lead_max_s":max(leads) if leads else None}
    report={"schema_version":1,"scope":"Frozen actual Monitor replay on all 36 held-out synthetic runs; same synthetic generation family, not real field accuracy.",
            "causality":"Monitor receives one frame at a time stripped of scenario/onset/intervention labels. Future ground truth is read only for post hoc scoring.",
            "model_sha256":hashlib.sha256((ROOT/"models/telemetry.json").read_bytes()).hexdigest(),
            "profile_sha256":hashlib.sha256((ROOT/"profile.json").read_bytes()).hexdigest(),
            "runtime_sha256":hashlib.sha256((ROOT/"runtime.py").read_bytes()).hexdigest(),
            "test_data_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"no_tuning":True,
            "normal":{"runs":len(normal),"false_alert_runs":sum(r["normal_false_alert_run"] for r in normal),
                      "false_inspection_events":sum(r["normal_false_inspection_events"] for r in normal),"false_urgent_events":sum(r["normal_false_urgent_events"] for r in normal)},
            "first_sustained_forecasts":{"events":len(forecast_events),"actual_crossing_within_180s":sum(event["crossed_within_180s"] for event in forecast_events),
                                         "no_crossing_within_run":sum(event["actual_first_raw_crossing_s"] is None for event in forecast_events),
                                         "meaning":"One first sustained prediction per run/field/level/direction; includes predictions that did not realize. Not calibrated shutdown probability."},
            "recovery":{"recovered_runs":len(recovery_delays),"delay_from_intervention_min_s":min(recovery_delays) if recovery_delays else None,
                        "delay_from_intervention_max_s":max(recovery_delays) if recovery_delays else None},
            "by_scenario":summaries,"errors":sum(len(r["errors"]) for r in results),"runs":results,
            "limitations":["No physical-generator faults or field maintenance outcomes.","Lead times reference demo thresholds, not OEM shutdown or failure.",
                           "Detection delay starts at hidden synthetic deterioration onset; it is not prediction before a new fault starts.",
                           "Field-matched advance warning includes learned normal-baseline deviations; a lead exceeding 180 seconds is not an extension of the forecast horizon.",
                           "A sudden spike may be a sensor artifact, load transient or equipment event; no cause classifier is present.",
                           "Recovery delay includes repair ramp and thermal settling; no independently labeled safe recovery endpoint.",
                           "Initial 180s collecting frames are neither false alarms nor confirmed normal; state_counts preserve them."],
            "elapsed_s":time.perf_counter()-started}
    out=ROOT/"reports/operational-evaluation.json";out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({k:report[k] for k in ("normal","by_scenario","errors","elapsed_s")},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
