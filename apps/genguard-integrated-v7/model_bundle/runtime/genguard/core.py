"""Validation, SQLite run history, frozen inference and post-run training."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone

import numpy as np

from .model import ALGORITHM, fit_phase, predict, score_phase

DOMAINS = {
    "live": {"live_imu", "live_controller"},
    "scenario": {"scenario"},
    "public_replay": {"public_replay"},
    "recorded_imu": {"recorded_imu"},
    "hybrid_demo": {"live_imu", "scenario"},
}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_profile(path):
    p = json.loads(Path(path).read_text()) if not isinstance(path, dict) else json.loads(canonical(path))
    if p.get("schema_version") != "genguard.edge.profile/1" or p.get("data_domain") not in DOMAINS:
        raise ValueError("Unsupported profile version/domain")
    required = set(p["context"]) - {"elapsed_s"} | set(p["targets"])
    if not p["targets"] or not p["context"] or not required <= p["fields"].keys():
        raise ValueError("Profile context/target fields missing")
    def finite(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    if not isinstance(p.get("asset_id"), str) or not p["asset_id"]:
        raise ValueError("Profile asset_id is required")
    if not finite(p["max_window_gap_s"]) or p["max_window_gap_s"] <= 0:
        raise ValueError("Window gap must be finite and positive")
    for key in p["context"]:
        value = p["context_tolerance"][key]
        if not finite(value) or value < 0:
            raise ValueError("Context tolerance must be finite and nonnegative")
    for key in required:
        spec = p["fields"][key]
        if not isinstance(spec["unit"], str) or not spec["unit"]:
            raise ValueError("Unit is required")
        if not finite(spec["max_age_s"]) or spec["max_age_s"] < 0:
            raise ValueError("Invalid measurement age limit")
        if not finite(spec["valid_min"]) or not finite(spec["valid_max"]) or spec["valid_min"] >= spec["valid_max"]:
            raise ValueError("Invalid measurement range")
        if spec.get("transform") not in (None, "log"):
            raise ValueError("Unsupported target transform")
        if p["data_domain"] == "hybrid_demo" and not spec.get("allowed_sources"):
            raise ValueError("Hybrid fields require explicit allowed_sources")
        if "allowed_sources" in spec and (not spec["allowed_sources"] or not set(spec["allowed_sources"]) <= DOMAINS[p["data_domain"]]):
            raise ValueError("Invalid field source policy")
    if set(p["context"]) & set(p["targets"]):
        raise ValueError("A target cannot also be its own context")
    if len(set(p["context"])) != len(p["context"]) or len(set(p["targets"])) != len(p["targets"]):
        raise ValueError("Duplicate model features")
    for key in p["targets"]:
        if not finite(p["fields"][key]["scale_floor"]) or p["fields"][key]["scale_floor"] <= 0:
            raise ValueError("Residual scale floor must be positive")
    m = p["model"]
    for key in ("persistence_windows", "min_rows_per_session", "max_rows_per_session", "max_sessions_per_training"):
        if type(m[key]) is not int or m[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if m["max_rows_per_session"] < m["min_rows_per_session"]:
        raise ValueError("Maximum rows cannot be below minimum rows")
    for key in ("ridge_alpha", "covariance_shrinkage", "calibration_quantile", "threshold_margin",
                "minimum_score_threshold", "max_validation_alert_fraction", "max_invalid_fraction"):
        if not finite(m[key]):
            raise ValueError(f"{key} must be finite")
    if m["minimum_score_threshold"] <= 0 or not 0 <= m["max_invalid_fraction"] < 1:
        raise ValueError("Invalid score threshold/invalid fraction")
    if not 0 < m["covariance_shrinkage"] <= 1 or m["ridge_alpha"] <= 0:
        raise ValueError("Invalid regularization")
    if not 0 < m["calibration_quantile"] < 1 or m["threshold_margin"] < 1:
        raise ValueError("Invalid calibration configuration")
    if not 0 <= m["max_validation_alert_fraction"] <= 1:
        raise ValueError("Invalid validation gate")
    if not p["phases"] or not set(p["phases"]) <= {"starting", "warming_up", "running", "stopped"}:
        raise ValueError("Unknown phase")
    canonical(p)
    return p


def validate_frame(frame, profile):
    required = {"schema_version", "asset_id", "session_id", "installation_id", "seq", "elapsed_s",
                "duration_s", "phase", "measured_at", "readings"}
    if not isinstance(frame, dict) or set(frame) != required:
        raise ValueError("Unexpected/missing window fields")
    if frame["schema_version"] != "genguard.edge.window/1" or frame["asset_id"] != profile["asset_id"]:
        raise ValueError("Schema/asset mismatch")
    for key in ("session_id", "installation_id"):
        if not isinstance(frame[key], str) or not frame[key] or len(frame[key]) > 150:
            raise ValueError(f"Invalid {key}")
    if type(frame["seq"]) is not int or frame["seq"] < 1:
        raise ValueError("seq must be a positive integer")
    for key in ("elapsed_s", "duration_s"):
        value = frame[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {key}")
    if frame["duration_s"] <= 0 or frame["duration_s"] > 60:
        raise ValueError("Window duration must be in (0,60] seconds")
    if frame["phase"] not in {"starting", "warming_up", "running", "stopped"}:
        raise ValueError("Unknown phase")
    if frame["measured_at"] is not None:
        if not isinstance(frame["measured_at"], str):
            raise ValueError("measured_at must be an ISO timestamp or null")
        stamp = datetime.fromisoformat(frame["measured_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("measured_at must include timezone")
    if not isinstance(frame["readings"], dict):
        raise ValueError("readings must be a mapping")
    canonical(frame)
    flags = []
    needed = (set(profile["context"]) - {"elapsed_s"}) | set(profile["targets"])
    for key in needed:
        value = frame["readings"].get(key)
        if not isinstance(value, dict):
            flags.append(f"missing:{key}")
            continue
        spec = profile["fields"][key]
        if value.get("source") not in DOMAINS[profile["data_domain"]]:
            flags.append(f"source_mismatch:{key}")
        if spec.get("allowed_sources") and value.get("source") not in spec["allowed_sources"]:
            flags.append(f"channel_source_mismatch:{key}")
        if value.get("unit") != spec["unit"]:
            flags.append(f"unit_mismatch:{key}")
        age, number = value.get("age_s"), value.get("value")
        if value.get("quality") != "ok":
            flags.append(f"quality:{key}")
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= spec["max_age_s"]:
            flags.append(f"stale_or_unknown_age:{key}")
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            flags.append(f"invalid_value:{key}")
        elif not spec["valid_min"] <= number <= spec["valid_max"]:
            flags.append(f"outside_measurement_range:{key}")
    return sorted(flags)


def _unknown(reason, flags=None):
    return {"state": "unknown", "reason": reason, "score": None, "threshold": None,
            "evidence": [], "quality_flags": flags or []}


class EdgeRuntime:
    """One database is bound to one immutable profile/asset/domain.

    A run snapshots its active AND original reference model. Training and model
    activation never change predictions in an already started run.
    """

    def __init__(self, db_path, profile_path):
        self.db_path = str(Path(db_path).resolve())
        self.profile = load_profile(profile_path)
        self.profile_hash = digest(self.profile)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, status TEXT NOT NULL,
          review TEXT NOT NULL DEFAULT 'unreviewed', reviewer TEXT, review_note TEXT,
          started_at TEXT NOT NULL, closed_at TEXT, active_model TEXT, reference_model TEXT,
          last_seq INTEGER DEFAULT 0, last_elapsed REAL DEFAULT -1, streak INTEGER DEFAULT 0,
          open_alert INTEGER DEFAULT 0, last_phase TEXT);
        CREATE TABLE IF NOT EXISTS windows(
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
          seq INTEGER NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL,
          UNIQUE(session_id,seq));
        CREATE INDEX IF NOT EXISTS windows_session ON windows(session_id,seq);
        """)
        with self.db:
            existing = self.db.execute("SELECT value FROM settings WHERE key='profile_hash'").fetchone()
            if existing and existing[0] != self.profile_hash:
                raise ValueError("Database belongs to another profile/configuration; use a new database")
            self.db.execute("INSERT OR IGNORE INTO settings VALUES('profile_hash',?)", (self.profile_hash,))

    def close(self):
        self.db.close()

    @contextmanager
    def activity_lock(self):
        # An OS lock disappears after process exit; a crashed training job cannot
        # leave a permanent 'busy' flag. Used on Linux/macOS, including UNO Q.
        with open(self.db_path + ".activity.lock", "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Training/session lifecycle operation is busy") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def setting(self, key):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def model(self, key):
        if not key:
            return None
        row = self.db.execute("SELECT payload FROM models WHERE id=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Referenced model is missing")
        return json.loads(row[0])

    def start_session(self, session_id, installation_id):
        with self.activity_lock():
            return self._start_session(session_id, installation_id)

    def _start_session(self, session_id, installation_id):
        if not session_id or not installation_id or len(session_id) > 150 or len(installation_id) > 150:
            raise ValueError("Invalid session/installation ID")
        with self.db:
            if self.db.execute("SELECT 1 FROM sessions WHERE status='open'").fetchone():
                raise ValueError("An open session exists; close it explicitly before starting another")
            active, reference = self.setting("active_model"), self.setting("reference_model")
            # A moved/replaced sensor must collect a new installation baseline.
            if active and self.model(active)["installation_id"] != installation_id:
                active = reference = None
            self.db.execute("INSERT INTO sessions(id,installation_id,status,started_at,active_model,reference_model) VALUES(?,?,'open',?,?,?)",
                            (session_id, installation_id, utc_now(), active, reference))

    def close_session(self, session_id):
        with self.db:
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown session")
            self.db.execute("UPDATE sessions SET status='closed',closed_at=COALESCE(closed_at,?) WHERE id=?", (utc_now(), session_id))

    def review_session(self, session_id, status, reviewer, note):
        if status not in {"normal", "abnormal", "exclude"} or not reviewer.strip() or not note.strip():
            raise ValueError("Explicit review status, reviewer and evidence note required")
        with self.db:
            row = self.db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row or row[0] != "closed":
                raise ValueError("Only closed sessions can be reviewed")
            self.db.execute("UPDATE sessions SET review=?,reviewer=?,review_note=? WHERE id=?", (status, reviewer, note, session_id))

    def reviews_current(self, bundle):
        ids = bundle["fit_sessions"] + bundle["calibration_sessions"] + bundle["validation_sessions"]
        placeholders = ",".join("?" for _ in ids)
        count = self.db.execute(f"SELECT COUNT(*) FROM sessions WHERE id IN ({placeholders}) AND status='closed' AND review='normal'", ids).fetchone()[0]
        return count == len(ids)

    def ingest(self, frame):
        flags = validate_frame(frame, self.profile)
        encoded = canonical(frame)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            duplicate = self.db.execute("SELECT payload,result FROM windows WHERE session_id=? AND seq=?", (frame["session_id"], frame["seq"])).fetchone()
            if duplicate:
                if duplicate["payload"] != encoded:
                    raise ValueError("Conflicting duplicate window")
                return json.loads(duplicate["result"])
            session = self.db.execute("SELECT * FROM sessions WHERE id=?", (frame["session_id"],)).fetchone()
            if session is None or session["status"] != "open":
                raise ValueError("Session must be started and open")
            if session["installation_id"] != frame["installation_id"]:
                raise ValueError("Installation changed within session")
            if frame["seq"] <= session["last_seq"] or frame["elapsed_s"] <= session["last_elapsed"]:
                raise ValueError("Sequence/time must increase")
            active, reference = self.model(session["active_model"]), self.model(session["reference_model"])
            if flags:
                current = _unknown("input_quality", flags)
                original = None
            elif active and (not self.reviews_current(active) or (reference and not self.reviews_current(reference))):
                current = _unknown("training_review_retracted")
                original = None
            elif frame["phase"] not in self.profile["phases"]:
                current = _unknown("phase_not_configured")
                original = None
            elif active is None:
                current = dict(_unknown("no_accepted_model"), state="baseline_collecting")
                original = None
            else:
                current = predict(frame, self.profile, active)
                original = predict(frame, self.profile, reference) if reference else None
            comparison = [r for r in [current, original] if r]
            changed = any(r["state"] == "change" for r in comparison)
            continuous = (frame["seq"] == session["last_seq"] + 1 and
                          frame["elapsed_s"] - session["last_elapsed"] <= self.profile["max_window_gap_s"] and
                          session["last_phase"] == frame["phase"])
            streak = (session["streak"] + 1 if continuous else 1) if changed else 0
            persistent = changed and streak >= self.profile["model"]["persistence_windows"]
            state = "inspection_suggested" if persistent else ("change_observed" if changed else current["state"])
            if not changed and any(r["state"] == "unknown" for r in comparison):
                state = "unknown"
            alert_open = bool(session["open_alert"] or persistent)
            selected = max((r for r in comparison if r["score"] is not None), key=lambda r: r["score"] / r["threshold"], default=current)
            actions = []
            if changed:
                for item in selected["evidence"]:
                    for text in self.profile["fields"][item["field"]].get("checks", []):
                        if text not in actions:
                            actions.append(text)
            result = {
                "schema_version": "genguard.edge.result/1", "asset_id": frame["asset_id"],
                "session_id": frame["session_id"], "seq": frame["seq"], "measured_at": frame["measured_at"],
                "elapsed_s": frame["elapsed_s"], "phase": frame["phase"],
                "data_domain": self.profile["data_domain"], "is_demonstration": self.profile["data_domain"] != "live",
                "input_sources": {k: v.get("source") if isinstance(v, dict) else None for k, v in frame["readings"].items()},
                "readings": frame["readings"], "state": state, "quality_flags": flags,
                "model_id": session["active_model"], "reference_model_id": session["reference_model"],
                "current_comparison": current, "initial_comparison": original,
                "persistent_change": persistent, "change_streak": streak, "open_alert_in_session": alert_open,
                "checks": actions[:5], "rul": None, "fault_diagnosis": None,
                "summary": {
                    "inspection_suggested": "과거 정상 운전과 다른 상태가 지속됩니다. 근거와 점검 항목을 확인하세요.",
                    "change_observed": "평소 패턴과 다른 변화가 관찰되었습니다. 지속 여부를 확인 중입니다.",
                    "within_baseline": "현재 비교 가능한 항목에서 학습 기준 내입니다. 모든 부품의 정상 보증은 아닙니다.",
                    "unknown": "입력 품질 또는 운전 조건 때문에 현재 모델로 판단할 수 없습니다.",
                    "baseline_collecting": "검증된 정상 기준이 없습니다. 점검 기록을 수집 중입니다.",
                }[state],
            }
            cur = self.db.execute("INSERT INTO windows(session_id,seq,payload,result) VALUES(?,?,?,?)", (frame["session_id"], frame["seq"], encoded, "{}"))
            result["record_id"] = cur.lastrowid
            self.db.execute("UPDATE windows SET result=? WHERE id=?", (canonical(result), cur.lastrowid))
            self.db.execute("UPDATE sessions SET last_seq=?,last_elapsed=?,streak=?,open_alert=?,last_phase=? WHERE id=?",
                            (frame["seq"], frame["elapsed_s"], streak, int(alert_open), frame["phase"], frame["session_id"]))
            return result

    def export_results(self, after=0, limit=1000):
        if not 1 <= limit <= 10000:
            raise ValueError("limit out of range")
        return [json.loads(r[0]) for r in self.db.execute("SELECT result FROM windows WHERE id>? ORDER BY id LIMIT ?", (after, limit))]

    def training_frames(self, session_ids):
        output, installation = [], None
        maximum = self.profile["model"]["max_rows_per_session"]
        for session_id in session_ids:
            session = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None or session["status"] != "closed" or session["review"] != "normal":
                raise ValueError(f"Training requires a closed, explicitly normal-reviewed session: {session_id}")
            if installation is not None and session["installation_id"] != installation:
                raise ValueError("Do not mix sensor installations")
            installation = session["installation_id"]
            count = self.db.execute("SELECT COUNT(*) FROM windows WHERE session_id=?", (session_id,)).fetchone()[0]
            # Evenly spaced, deterministic cap keeps matrix memory bounded. Whole-run IDs remain disjoint.
            stride = max(1, math.ceil(count / maximum))
            selected, invalid, eligible = [], 0, 0
            for i, row in enumerate(self.db.execute("SELECT payload FROM windows WHERE session_id=? ORDER BY seq", (session_id,))):
                frame = json.loads(row[0])
                if frame["phase"] not in self.profile["phases"]:
                    continue
                eligible += 1
                if validate_frame(frame, self.profile):
                    invalid += 1
                elif i % stride == 0:
                    selected.append(frame)
            if not eligible or invalid / eligible > self.profile["model"]["max_invalid_fraction"]:
                raise ValueError(f"Too many invalid/missing windows in {session_id}")
            if len(selected) < self.profile["model"]["min_rows_per_session"]:
                raise ValueError(f"Not enough usable history in {session_id}")
            output.extend(selected)
        return output, installation

    def train_candidate(self, fit_ids, calibration_ids, validation_ids):
        with self.activity_lock():
            return self._train_candidate(fit_ids, calibration_ids, validation_ids)

    def _train_candidate(self, fit_ids, calibration_ids, validation_ids):
        groups = [list(fit_ids), list(calibration_ids), list(validation_ids)]
        if len(groups[0]) < 2 or not groups[1] or not groups[2]:
            raise ValueError("Need >=2 fit runs, >=1 calibration run, >=1 separate validation run")
        flat = sum(groups, [])
        if len(flat) != len(set(flat)):
            raise ValueError("Fit/calibration/validation run IDs must be disjoint")
        if len(flat) > self.profile["model"]["max_sessions_per_training"]:
            raise ValueError("Training history cap exceeded")
        if self.db.execute("SELECT 1 FROM sessions WHERE status='open'").fetchone():
            raise ValueError("Close the active run before training")
        started = time.perf_counter()
        fit, inst = self.training_frames(groups[0])
        cal, cal_inst = self.training_frames(groups[1])
        validation, val_inst = self.training_frames(groups[2])
        if inst != cal_inst or inst != val_inst:
            raise ValueError("Installation mismatch across split")
        phases = {}
        for phase in self.profile["phases"]:
            f, c = [r for r in fit if r["phase"] == phase], [r for r in cal if r["phase"] == phase]
            if len(f) < self.profile["model"]["min_rows_per_session"] * 2 or len(c) < self.profile["model"]["min_rows_per_session"]:
                raise ValueError(f"Insufficient fit/calibration coverage for phase {phase}")
            fitted = fit_phase(f, self.profile)
            scores = [score_phase(r, self.profile, fitted) for r in c]
            if any(s["outside_context"] for s in scores):
                raise ValueError(f"Calibration contains unseen operating conditions for {phase}; collect comparable normal runs")
            fitted["threshold"] = max(self.profile["model"]["minimum_score_threshold"],
                                      float(np.quantile([s["score"] for s in scores], self.profile["model"]["calibration_quantile"])) * self.profile["model"]["threshold_margin"])
            fitted["calibration_count"] = len(c)
            phases[phase] = fitted
        bundle = {
            "schema_version": "genguard.edge.model/1", "algorithm": ALGORITHM,
            "profile_hash": self.profile_hash, "asset_id": self.profile["asset_id"],
            "installation_id": inst, "data_domain": self.profile["data_domain"],
            "created_at": utc_now(), "phases": phases,
            "fit_sessions": groups[0], "calibration_sessions": groups[1], "validation_sessions": groups[2],
            "training_fingerprint": digest({"fit": fit, "calibration": cal}),
            "scope": "상태 변화 탐지. 고장 원인 확정/잔여수명/제조사 보호장치 대체 아님.",
        }
        checks = []
        for sid in groups[2]:
            frames = [r for r in validation if r["session_id"] == sid]
            results = [predict(r, self.profile, bundle) for r in frames]
            unknown = sum(r["state"] == "unknown" for r in results)
            alerts = sum(r["state"] == "change" for r in results)
            fraction = alerts / len(results)
            checks.append({"session_id": sid, "windows": len(results), "unknown": unknown,
                           "change_windows": alerts, "change_fraction": fraction,
                           "passed": unknown == 0 and fraction <= self.profile["model"]["max_validation_alert_fraction"]})
        bundle["validation"] = {"passed": all(c["passed"] for c in checks), "runs": checks,
                                "meaning": "보류한 정상 기록의 오경보/조건범위 검사. 고장 탐지 성능 보증 아님."}
        bundle["training_seconds"] = time.perf_counter() - started
        bundle["model_id"] = "gg-" + digest(bundle)[:20]
        with self.db:
            self.db.execute("INSERT INTO models(id,payload) VALUES(?,?)", (bundle["model_id"], canonical(bundle)))
        return bundle

    def activate(self, model_id):
        with self.activity_lock():
            return self._activate(model_id)

    def _activate(self, model_id):
        bundle = self.model(model_id)
        if not bundle["validation"]["passed"]:
            raise ValueError("Candidate failed independent normal-run validation")
        if bundle["profile_hash"] != self.profile_hash or bundle["data_domain"] != self.profile["data_domain"]:
            raise ValueError("Model profile/domain mismatch")
        if not self.reviews_current(bundle):
            raise ValueError("Training/calibration/validation review was retracted")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM sessions WHERE status='open'").fetchone():
                raise ValueError("Only activate between runs")
            reference = self.model(self.setting("reference_model"))
            if reference and reference["installation_id"] != bundle["installation_id"]:
                raise ValueError("New installation requires a new database; original baseline is preserved")
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('active_model',?)", (model_id,))
            self.db.execute("INSERT OR IGNORE INTO settings VALUES('reference_model',?)", (model_id,))

    def sessions(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM sessions ORDER BY started_at,id")]
