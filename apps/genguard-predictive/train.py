"""Train synthetic multi-horizon telemetry forecasts and a separate normal baseline.

Training requires NumPy and PyTorch. The exported JSON needs NumPy only at
inference. Whole runs are split before feature windows are constructed. Test
results never choose parameters, early stopping or calibration widths.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch

from contract import build_features, normal_features, FEATURE_NAMES, NORMAL_FEATURE_NAMES
from simulator import SCENARIOS, generate_run

ROOT = Path(__file__).resolve().parent
TARGETS = ["coolant_c", "oil_pressure_kgf_cm2", "voltage_v", "frequency_hz", "engine_rpm",
           "current_a", "current_b", "current_c", "battery_v", "fuel_pct", "fuel_pressure_bar"]
CONTEXT = ["load_pct", "ambient_c"]
FIELDS = TARGETS + CONTEXT
HORIZONS = [30, 60, 120, 180]
NORMAL_TARGETS = ["coolant_c", "oil_pressure_kgf_cm2", "voltage_v", "frequency_hz", "engine_rpm", "battery_v", "fuel_pressure_bar"]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def predict_numpy(x, model):
    z = (np.asarray(x) - np.asarray(model["x_mean"])) / np.asarray(model["x_scale"])
    for layer in model["layers"]:
        z = z @ np.asarray(layer["weights"]) + np.asarray(layer["bias"])
        if layer["activation"] == "tanh": z = np.tanh(z)
    return z * np.asarray(model["y_scale"]) + np.asarray(model["y_mean"])


def make_split(name, runs_per_scenario, offset, output):
    runs=[]; manifest=[]
    path=output/"data"/f"telemetry-{name}.jsonl.gz"
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as handle:
        for si, scenario in enumerate(SCENARIOS):
            for ri in range(runs_per_scenario):
                seed=offset + si*100 + ri
                frames=generate_run(scenario,seed)
                runs.append(frames)
                manifest.append({"run_id":frames[0]["run_id"],"seed":seed,"scenario":scenario,"frames":len(frames)})
                for frame in frames: handle.write(json.dumps(frame,separators=(",",":"))+"\n")
    return runs,{"runs":manifest,"source_file":str(path.relative_to(output)),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def samples(runs):
    xs=[]; ys=[]; currents=[]; linear=[]; metadata=[]
    for frames in runs:
        # Every ten seconds; history180s and horizon180s remain wholly in one run.
        for i in range(90,len(frames)-90,5):
            history=frames[i-90:i+1]
            current=np.array([frames[i][k] for k in TARGETS])
            future=np.array([[frames[i+h//2][k] for k in TARGETS] for h in HORIZONS])
            xs.append(build_features(history)); ys.append((future-current).reshape(-1)); currents.append(current)
            slope=(current-np.array([frames[i-15][k] for k in TARGETS]))/30.0
            linear.append(np.array([slope*h for h in HORIZONS]).reshape(-1))
            metadata.append({"run_id":frames[i]["run_id"],"scenario":frames[i]["scenario"],"timestamp_s":frames[i]["timestamp_s"],
                             "future_contains_unannounced_intervention":bool(frames[i]["intervention_s"] and frames[i]["timestamp_s"]<frames[i]["intervention_s"]<=frames[i]["timestamp_s"]+max(HORIZONS))})
    return np.asarray(xs,dtype=np.float64),np.asarray(ys,dtype=np.float64),np.asarray(currents),np.asarray(linear),metadata


def normal_arrays(runs):
    x=[];y=[]
    for frames in runs:
        if frames[0]["scenario"] not in ("normal","load_transient"): continue
        for i in range(90,len(frames),5):
            x.append(normal_features(frames[i-90:i+1]))
            y.append([frames[i][k] for k in NORMAL_TARGETS])
    return np.array(x),np.array(y)


def fit_normal(train_runs,cal_runs):
    x,y=normal_arrays(train_runs);cx,cy=normal_arrays(cal_runs)
    penalty=np.eye(x.shape[1])*0.0001;penalty[0,0]=0
    weights=np.linalg.solve(x.T@x+penalty,x.T@y)
    residual=y-x@weights
    floors=np.array([0.25,0.025,0.15,0.015,0.45,0.015,0.018])
    scales=np.maximum(residual.std(axis=0),floors)
    cal_z=(cy-cx@weights)/scales
    scores=np.max(np.abs(cal_z),axis=1)
    threshold=float(max(3.0,np.quantile(scores,.995)*1.2))
    return {"targets":NORMAL_TARGETS,"feature_names":NORMAL_FEATURE_NAMES,
            "weights":weights.tolist(),"residual_mean":[0.0]*len(NORMAL_TARGETS),"residual_scale":scales.tolist(),"threshold":threshold,"score_method":"max_absolute_standardized_residual",
            "calibration_quantile":.995,"calibration_margin":1.2,"threshold_floor":3.0,
            "fit_rows":len(x),"calibration_rows":len(cx),"source_scenarios":["normal","load_transient"],
            "meaning":"Condition-aware normal expectation only; not learned manufacturer safety limits."}


def evaluate(y,pred,linear,metadata):
    errors={"learned_mlp":pred-y,"last_value":-y,"recent_linear_30s":linear-y}
    result={}
    for method,error in errors.items():
        e=error.reshape(-1,len(HORIZONS),len(TARGETS))
        result[method]={str(h):{k:{"mae":float(np.mean(np.abs(e[:,hi,ki]))),"rmse":float(np.sqrt(np.mean(e[:,hi,ki]**2)))} for ki,k in enumerate(TARGETS)} for hi,h in enumerate(HORIZONS)}
    by_scenario={}
    for scenario in SCENARIOS:
        mask=np.array([m["scenario"]==scenario for m in metadata])
        by_scenario[scenario]={method:{k:float(np.mean(np.abs(error[mask].reshape(-1,4,11)[:,:,ki]))) for ki,k in enumerate(TARGETS)} for method,error in errors.items()}
    return {"per_horizon":result,"by_scenario_mean_across_horizons":by_scenario}


def main(output=ROOT, epochs=150):
    output=Path(output);started=time.perf_counter()
    torch.set_num_threads(2); torch.manual_seed(20261011); rng=np.random.default_rng(20261011)
    configs={"fit":(12,10000),"validation":(3,20000),"calibration":(3,30000),"test":(4,40000)}
    runs={};manifests={};sets={}
    for name,(count,offset) in configs.items():
        runs[name],manifests[name]=make_split(name,count,offset,output)
        sets[name]=samples(runs[name])
        print(f"{name}: {len(runs[name])} whole runs, {len(sets[name][0])} forecast windows",flush=True)
    x,y,*_=sets["fit"];vx,vy,*_=sets["validation"]
    x_mean=x.mean(axis=0);x_scale=np.maximum(x.std(axis=0),1e-5)
    y_mean=y.mean(axis=0);y_scale=np.maximum(y.std(axis=0),1e-5)
    tx=torch.tensor((x-x_mean)/x_scale,dtype=torch.float32);ty=torch.tensor((y-y_mean)/y_scale,dtype=torch.float32)
    tvx=torch.tensor((vx-x_mean)/x_scale,dtype=torch.float32);tvy=torch.tensor((vy-y_mean)/y_scale,dtype=torch.float32)
    net=torch.nn.Sequential(torch.nn.Linear(x.shape[1],96),torch.nn.Tanh(),torch.nn.Linear(96,64),torch.nn.Tanh(),torch.nn.Linear(64,y.shape[1]))
    optimizer=torch.optim.AdamW(net.parameters(),lr=.0015,weight_decay=.001)
    best=float("inf");best_state=None;best_epoch=None;log=[]
    for epoch in range(1,epochs+1):
        net.train();order=rng.permutation(len(x));total=0.
        for begin in range(0,len(x),384):
            idx=order[begin:begin+384];pred=net(tx[idx]);loss=torch.mean((pred-ty[idx])**2)
            optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),5);optimizer.step();total+=float(loss.detach())*len(idx)
        net.eval()
        with torch.no_grad(): val=float(torch.mean((net(tvx)-tvy)**2))
        log.append({"epoch":epoch,"train_standardized_mse":total/len(x),"validation_standardized_mse":val})
        if val<best:
            best=val;best_epoch=epoch;best_state={k:v.detach().clone() for k,v in net.state_dict().items()}
        if epoch%25==0: print(f"epoch {epoch}: train={total/len(x):.4f}, validation={val:.4f}, best epoch={best_epoch}",flush=True)
    net.load_state_dict(best_state);net.eval()
    layers=[]
    for layer in net:
        if isinstance(layer,torch.nn.Linear):
            layers.append({"weights":layer.weight.detach().numpy().T.astype(float).tolist(),"bias":layer.bias.detach().numpy().astype(float).tolist(),"activation":"linear"})
        elif isinstance(layer,torch.nn.Tanh): layers[-1]["activation"]="tanh"
    model={"schema_version":1,"model_type":"mlp_multi_horizon_future_delta","targets":TARGETS,"horizons_s":HORIZONS,
           "feature_names":FEATURE_NAMES,"x_mean":x_mean.tolist(),"x_scale":x_scale.tolist(),"y_mean":y_mean.tolist(),"y_scale":y_scale.tolist(),"layers":layers,
           "normal_model":fit_normal(runs["fit"],runs["calibration"]),
           "domain":{"data_source":"synthetic","nominal_kw":10,"nominal_voltage_v":208,"voltage_measurement":"line_to_line","nominal_hz":60,"nominal_rpm":1800,
                     "phase":"running","dt_s":2,"history_s":180,"context_fields":CONTEXT,"context_limits":{"load_pct":[20,92],"ambient_c":[11,37]},
                     "warning":"Invented synthetic scenarios only. Not field validated, cross-generator qualified, or a protection controller."}}
    cx,cy,*_=sets["calibration"];cp=predict_numpy(cx,model)
    half_width=np.quantile(np.abs(cp-cy).reshape(-1,4,11),.95,axis=0)
    model["calibration"]={"half_width":half_width.tolist(),"quantile":.95,"method":"Per-target/horizon absolute residual quantile on disjoint calibration runs; empirical synthetic coverage, not safety confidence."}
    path=output/"models"/"telemetry.json";write_json(path,model)
    testx,testy,_,testlinear,testmeta=sets["test"];pred=predict_numpy(testx,model)
    evaluation=evaluate(testy,pred,testlinear,testmeta)
    error=(pred-testy).reshape(-1,4,11)
    evaluation.update({"scope":"Held-out synthetic whole runs from same simulator; no real hardware/fault validation.","test_runs":len(runs["test"]),"test_windows":len(testx),
                       "coverage":{str(h):{k:float(np.mean(np.abs(error[:,hi,ki])<=half_width[hi,ki])) for ki,k in enumerate(TARGETS)} for hi,h in enumerate(HORIZONS)},
                       "unannounced_intervention_windows":sum(m["future_contains_unannounced_intervention"] for m in testmeta),
                       "interpretation":"No model can know a new fault/intervention before evidence exists; scoring includes those uncertain windows. Test does not select model or thresholds."})
    # Export equivalence and inference timings are measured on this host, not UNO Q.
    with torch.no_grad(): torch_pred=net(torch.tensor((testx[:32]-x_mean)/x_scale,dtype=torch.float32)).numpy()*y_scale+y_mean
    parity=float(np.max(np.abs(torch_pred-pred[:32])))
    timings=[]
    for _ in range(200):
        tick=time.perf_counter();predict_numpy(testx[0],model);timings.append((time.perf_counter()-tick)*1000)
    training={"schema_version":1,"data_source":"synthetic","algorithm":"78-input / 96-tanh / 64-tanh / 44-linear MLP; future-minus-current targets",
              "feature_contract":"current values and differences at 10/30/60/120/180 seconds; no scenario labels or future data in features",
              "split_policy":"Whole independent generated runs; different seeds in fit/validation/calibration/test; overlapping windows never cross splits.",
              "splits":manifests,"epochs":epochs,"selected_epoch":best_epoch,"selection_criterion":"lowest validation standardized MSE only","training_log":log,
              "model_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"model_bytes":path.stat().st_size,"parameter_count":sum(p.numel() for p in net.parameters()),
              "export_max_abs_error_vs_torch":parity,"numpy_inference_ms":{"median":float(np.median(timings)),"p95":float(np.quantile(timings,.95)),"uno_q_measured":False},
              "elapsed_s":time.perf_counter()-started,"not_proven":["actual generator accuracy","UNO Q resource usage","failure cause","RUL","unseen generator generalization"]}
    write_json(output/"reports"/"training.json",training);write_json(output/"reports"/"evaluation.json",evaluation)
    print(json.dumps({"selected_epoch":best_epoch,"best_validation_mse":best,"export_parity":parity,"elapsed_s":training["elapsed_s"]},indent=2),flush=True)
    return model,evaluation


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=ROOT);parser.add_argument("--epochs",type=int,default=150)
    args=parser.parse_args();main(args.output,args.epochs)
