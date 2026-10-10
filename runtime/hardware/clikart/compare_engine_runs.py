import csv, json, sqlite3, collections
base = "/home/arduino/genguard-edge-v1/runs/"
a = list(csv.DictReader(open(base + "numpy/traces.csv")))
b = list(csv.DictReader(open(base + "clikart/traces.csv")))
assert len(a) == len(b), (len(a), len(b))
state_same = sum(x["state"] == y["state"] for x, y in zip(a, b))
diffs = [abs(float(x["score"]) - float(y["score"])) for x, y in zip(a, b) if x["score"] and y["score"]]
by = collections.Counter((y["scenario"], y["model"], y["state"]) for y in b)
engines = collections.Counter()
for db in ("fusion", "vibration"):
    con = sqlite3.connect(f"file:{base}clikart/{db}.sqlite?mode=ro", uri=True)
    for (r,) in con.execute("select result from windows"):
        cc = json.loads(r).get("current_comparison") or {}
        engines[cc.get("engine", "none:" + str(cc.get("reason")))] += 1
ra, rb = json.load(open(base + "numpy/report.json")), json.load(open(base + "clikart/report.json"))
out = {"rows": len(a), "state_agreement": f"{state_same}/{len(a)}",
       "scored_rows": len(diffs), "max_abs_score_diff": max(diffs) if diffs else None,
       "window_engines_in_clikart_db": dict(engines),
       "clikart_states": {f"{k[0]}/{k[1]}/{k[2]}": v for k, v in sorted(by.items())},
       "report_keys_equal": sorted(ra) == sorted(rb)}
print(json.dumps(out, ensure_ascii=False, indent=1))
json.dump(out, open(base + "engine-compare.json", "w"), ensure_ascii=False, indent=1)
