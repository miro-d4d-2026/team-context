"""Scenario-only inference bridge. Frozen model and rule files stay unchanged."""
from __future__ import annotations

from contextlib import closing
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from execution_info import execution_info

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "model_bundle"
sys.path.insert(0, str(BUNDLE / "runtime"))
from genguard.core import EdgeRuntime, canonical, digest, load_profile
from genguard.model import ALGORITHM

TIME_POLICY = {
    "kind": "simulated", "step_s": 2, "recommended_send_interval_ms": 2000,
    "note": "새 표본마다 시연 시간이 2초 진행됩니다. 실제 경과 시간이나 고장 예측 시간이 아닙니다.",
}


class BridgeError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


class RuleWorker:
    """Persistent worker with bounded reads; no state lives in the subprocess."""
    def __init__(self):
        self.process = None

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe:
                    pipe.close()
            self.process = None

    def request(self, payload):
        try:
            if not self.process or self.process.poll() is not None:
                self.close()
                node = shutil.which("node")
                if not node:
                    raise RuntimeError("Node.js is required for the unchanged fixed-rule engine")
                self.process = subprocess.Popen(
                    [node, str(ROOT / "rule_worker.mjs")], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                )
            self.process.stdin.write((canonical(payload) + "\n").encode())
            self.process.stdin.flush()
            deadline, line = time.monotonic() + 5, b""
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while not line.endswith(b"\n"):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(timeout=remaining):
                        raise RuntimeError("Rule worker timed out")
                    chunk = os.read(self.process.stdout.fileno(), 8192)
                    if not chunk:
                        raise RuntimeError("Rule worker exited")
                    line += chunk
                    if len(line) > 262144:
                        raise RuntimeError("Rule worker output exceeded limit")
            if not line or len(line) > 262144 or not line.endswith(b"\n"):
                raise RuntimeError("Rule worker returned incomplete output")
            result = json.loads(line)
            if not result.get("ok"):
                raise RuntimeError("Rule worker rejected request")
            return result
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.close()
            raise BridgeError(503, "rule_worker_unavailable", "고정 규칙 실행기를 사용할 수 없습니다. Node.js와 서버 로그를 확인한 뒤 같은 요청을 재시도하세요.") from exc


class BridgeService:
    def __init__(self, state_dir):
        self.execution = execution_info()
        self.runtime = None
        self.worker = RuleWorker()
        self.lock = None
        self.state_dir = Path(state_dir).resolve()
        self.profile = load_profile(BUNDLE / "profile.json")
        self.bundle = json.loads((BUNDLE / "model.json").read_text(encoding="utf-8"))
        self.rule_profile = json.loads((BUNDLE / "rules/profile.json").read_text(encoding="utf-8"))
        self.fields = {item["key"]: item for item in self.rule_profile["fields"]}
        self.active_session = None
        self._verify_bundle()
        try:
            self._prepare_database()
            self.runtime = EdgeRuntime(self.state_dir / "bridge.sqlite", BUNDLE / "profile.json")
            self._verify_runtime()
            self.runtime.db.executescript("""
                CREATE TABLE IF NOT EXISTS bridge_sessions (
                  session_id TEXT PRIMARY KEY REFERENCES sessions(id), request_id TEXT UNIQUE NOT NULL,
                  rule_state TEXT, active_alert_ids TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS bridge_samples (
                  session_id TEXT NOT NULL REFERENCES bridge_sessions(session_id), seq INTEGER NOT NULL,
                  fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(session_id, seq));
                CREATE TABLE IF NOT EXISTS bridge_pending (
                  session_id TEXT NOT NULL REFERENCES bridge_sessions(session_id), seq INTEGER NOT NULL,
                  fingerprint TEXT NOT NULL, PRIMARY KEY(session_id, seq));
            """)
            # Only our bridge session IDs can be closed automatically on restart.
            open_rows = self.runtime.db.execute("SELECT id FROM sessions WHERE status='open'").fetchall()
            for row in open_rows:
                owned = self.runtime.db.execute("SELECT 1 FROM bridge_sessions WHERE session_id=?", (row[0],)).fetchone()
                if not owned:
                    raise ValueError("State DB has a foreign open session; use a fresh --state-dir")
                self.runtime.close_session(row[0])
            self.worker.request({"action": "ping"})
        except Exception:
            self.close()
            raise

    def _verify_bundle(self):
        if self.bundle.get("algorithm") != ALGORITHM or self.bundle.get("profile_hash") != digest(self.profile):
            raise ValueError("Frozen model/runtime/profile mismatch; copy the complete original package")
        if self.bundle.get("data_domain") != "scenario" or self.profile["data_domain"] != "scenario":
            raise ValueError("Only the scenario model is supported")
        if not self.bundle.get("validation", {}).get("passed"):
            raise ValueError("Model normal-validation gate was not passed")
        with closing(sqlite3.connect((BUNDLE / "baseline.sqlite").as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Frozen baseline DB integrity check failed")
            row = source.execute("SELECT payload FROM models WHERE id=?", (self.bundle["model_id"],)).fetchone()
            if not row or json.loads(row[0]) != self.bundle:
                raise ValueError("Frozen model.json and baseline.sqlite differ")
            settings = dict(source.execute("SELECT key,value FROM settings"))
            if settings.get("active_model") != self.bundle["model_id"] or settings.get("profile_hash") != digest(self.profile):
                raise ValueError("Frozen database does not activate the delivered model/profile")

    def _prepare_database(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.lock = (self.state_dir / "bridge.process.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another server owns this --state-dir") from exc
        marker_path, db_path = self.state_dir / "bridge-owner.json", self.state_dir / "bridge.sqlite"
        marker = {"schema": "genguard.bridge-owned-state/1", "model_id": self.bundle["model_id"],
                  "model_hash": digest(self.bundle), "profile_hash": digest(self.profile)}
        if db_path.exists():
            if not marker_path.is_file() or json.loads(marker_path.read_text()) != marker:
                raise ValueError("Existing database is not owned by this bridge/model; use a fresh --state-dir")
        else:
            if marker_path.exists() and json.loads(marker_path.read_text()) != marker:
                raise ValueError("State directory belongs to another model")
            temp = self.state_dir / (".baseline-" + uuid.uuid4().hex + ".sqlite")
            try:
                with closing(sqlite3.connect((BUNDLE / "baseline.sqlite").as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(temp)) as destination:
                        source.backup(destination)
                marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
                temp.replace(db_path)
            finally:
                if temp.exists():
                    temp.unlink()

    def _verify_runtime(self):
        if any(self.runtime.setting(key) != self.bundle["model_id"] for key in ("active_model", "reference_model")):
            raise ValueError("State database active model changed; use a fresh --state-dir")
        if self.runtime.model(self.bundle["model_id"]) != self.bundle or not self.runtime.reviews_current(self.bundle):
            raise ValueError("State DB model or normal-review history changed")

    def close(self):
        self.worker.close()
        if self.runtime:
            if self.active_session:
                self.runtime.close_session(self.active_session)
            self.runtime.close()
            self.runtime = None
        if self.lock:
            self.lock.close()
            self.lock = None

    def health(self):
        try:
            self.worker.request({"action": "ping"})
            ready = self.runtime is not None
        except BridgeError:
            ready = False
        return {"ready": ready, "model_id": self.bundle["model_id"], "data_domain": "scenario",
                "execution": self.execution,
                "is_demonstration": True, "active_session": self.active_session,
                "inference_location": "api_host", "time_policy": TIME_POLICY}

    def config(self):
        return {"fields": list(self.fields.values()), "default_values": {k: f["default"] for k, f in self.fields.items()},
                "model_id": self.bundle["model_id"], "time_policy": TIME_POLICY,
                "data_domain": "scenario", "is_demonstration": True}

    def start(self, body):
        if not isinstance(body, dict) or set(body) != {"request_id"}:
            raise BridgeError(400, "invalid_request", "request_id 하나를 포함한 객체가 필요합니다.")
        key = body["request_id"]
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise BridgeError(400, "invalid_request_id", "request_id는 1~128자의 문자열이어야 합니다.")
        existing = self.runtime.db.execute("SELECT b.session_id,s.status,s.last_seq FROM bridge_sessions b JOIN sessions s ON s.id=b.session_id WHERE request_id=?", (key,)).fetchone()
        if existing:
            if existing["status"] != "open":
                raise BridgeError(409, "session_closed", "이 시작 요청의 세션은 종료됐습니다. 새 request_id로 시작하세요.")
            accepted = self.runtime.db.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_samples WHERE session_id=?", (existing["session_id"],)).fetchone()[0]
            return {"session_id": existing["session_id"], "next_seq": accepted + 1, "time_policy": TIME_POLICY}
        if self.active_session:
            raise BridgeError(409, "active_session_exists", "실행 중인 세션을 먼저 종료하세요.")
        sid = "bridge-" + uuid.uuid4().hex
        self.runtime.start_session(sid, self.bundle["installation_id"])
        with self.runtime.db:
            self.runtime.db.execute("INSERT INTO bridge_sessions(session_id,request_id) VALUES (?,?)", (sid, key))
        self.active_session = sid
        return {"session_id": sid, "next_seq": 1, "time_policy": TIME_POLICY}

    def close_session(self, sid, body):
        if body != {}:
            raise BridgeError(400, "invalid_request", "종료 요청 본문은 빈 객체여야 합니다.")
        if not self.runtime.db.execute("SELECT 1 FROM bridge_sessions WHERE session_id=?", (sid,)).fetchone():
            raise BridgeError(404, "session_not_found", "해당 시연 세션이 없습니다.")
        self.runtime.close_session(sid)
        if self.active_session == sid:
            self.active_session = None
        return {"session_id": sid, "closed": True}

    def validate_sample(self, body):
        expected = {"seq", "source", "phase", "values"}
        if not isinstance(body, dict) or not expected <= set(body) or set(body) - expected - {"quality"}:
            raise BridgeError(400, "invalid_sample", "seq, source, phase, values 및 선택 quality만 사용할 수 있습니다.")
        if type(body["seq"]) is not int or not 1 <= body["seq"] <= 1_000_000_000:
            raise BridgeError(400, "invalid_seq", "seq는 1 이상의 정수여야 합니다.")
        if body["source"] != "scenario":
            raise BridgeError(422, "unsupported_source", "이 모델은 모의 데이터 전용입니다. 실제 센서 데이터는 별도 정상 학습이 필요합니다.")
        if body["phase"] not in ("running", "warming_up", "starting", "stopped"):
            raise BridgeError(400, "invalid_phase", "phase는 running, warming_up, starting, stopped 중 하나여야 합니다.")
        values, quality = body["values"], body.get("quality", {})
        if not isinstance(values, dict) or set(values) - set(self.fields):
            raise BridgeError(400, "invalid_values", "values에 지원하지 않는 항목이 있습니다.")
        if any(value is not None and not finite(value) for value in values.values()):
            raise BridgeError(400, "invalid_number", "측정값은 유한한 숫자 또는 null이어야 합니다.")
        if not isinstance(quality, dict) or set(quality) - set(self.fields):
            raise BridgeError(400, "invalid_quality", "quality 항목을 확인하세요.")
        for value in quality.values():
            if not isinstance(value, dict) or set(value) - {"age_s", "quality"}:
                raise BridgeError(400, "invalid_quality", "quality는 age_s와 quality만 포함해야 합니다.")
            if "age_s" in value and (not finite(value["age_s"]) or value["age_s"] < 0):
                raise BridgeError(400, "invalid_age", "age_s는 0 이상의 유한한 숫자여야 합니다.")
            if "quality" in value and value["quality"] not in ("ok", "missing", "invalid", "stale"):
                raise BridgeError(400, "invalid_quality", "quality 값은 ok, missing, invalid, stale 중 하나여야 합니다.")

    def sample(self, sid, body):
        self.validate_sample(body)
        seq, fingerprint = body["seq"], digest(body)
        row = self.runtime.db.execute("SELECT b.*,s.status,s.last_seq FROM bridge_sessions b JOIN sessions s ON s.id=b.session_id WHERE b.session_id=?", (sid,)).fetchone()
        if not row:
            raise BridgeError(404, "session_not_found", "해당 시연 세션이 없습니다.")
        duplicate = self.runtime.db.execute("SELECT fingerprint,result FROM bridge_samples WHERE session_id=? AND seq=?", (sid, seq)).fetchone()
        if duplicate:
            if duplicate["fingerprint"] != fingerprint:
                raise BridgeError(409, "sequence_conflict", "같은 seq에 다른 값이 있습니다. 재시도는 원래 요청 그대로 보내세요.")
            result = json.loads(duplicate["result"])
            result.update(duplicate=True, notification_events=[])
            return result
        if row["status"] != "open" or sid != self.active_session:
            raise BridgeError(409, "session_closed", "세션이 종료됐습니다. 새 세션을 시작하세요.")
        accepted = self.runtime.db.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_samples WHERE session_id=?", (sid,)).fetchone()[0]
        if seq != accepted + 1:
            raise BridgeError(409, "sequence_conflict", f"다음 seq는 {accepted + 1}입니다.")
        pending = self.runtime.db.execute("SELECT fingerprint FROM bridge_pending WHERE session_id=? AND seq=?", (sid, seq)).fetchone()
        if pending and pending[0] != fingerprint:
            raise BridgeError(409, "sequence_conflict", "저장 복구 중인 요청과 값이 다릅니다. 원래 요청 그대로 재시도하세요.")
        elapsed = seq * TIME_POLICY["step_s"]
        readings = {}
        for key, spec in self.fields.items():
            value = body["values"].get(key)
            quality = body.get("quality", {}).get(key, {})
            readings[key] = {"value": value, "unit": spec["unit"], "source": "scenario",
                             "quality": quality.get("quality", "missing" if value is None else "ok"),
                             "age_s": quality.get("age_s", 0)}
        snapshot = {"schema_version": "genguard.alarm-snapshot/1", "scenario_id": sid,
                    "t_s": elapsed, "phase": body["phase"], "readings": readings, "events": {}, "normal_review": False}
        # Evaluate rules first: worker failure cannot increment model streak or seq.
        rule_response = self.worker.request({"action": "evaluate", "snapshot": snapshot,
                                             "state": json.loads(row["rule_state"]) if row["rule_state"] else None})
        frame_readings = {}
        for key in self.profile["fields"]:
            ui_key = {"oil_bar": "oil_kgf_cm2", "voltage_v": "voltage_ll_v"}.get(key, key)
            item = dict(readings[ui_key])
            if key == "oil_bar" and item["value"] is not None:
                item["value"] *= 0.980665
            item["unit"] = self.profile["fields"][key]["unit"]
            frame_readings[key] = item
        frame = {"schema_version": "genguard.edge.window/1", "asset_id": self.profile["asset_id"],
                 "session_id": sid, "installation_id": self.bundle["installation_id"], "seq": seq,
                 "elapsed_s": elapsed, "duration_s": 2, "phase": body["phase"], "measured_at": None,
                 "readings": frame_readings}
        # Persist the FULL request identity before the model's independent commit.
        # This also covers UI-only fields that are not part of the model frame.
        with self.runtime.db:
            self.runtime.db.execute("INSERT OR IGNORE INTO bridge_pending VALUES (?,?,?)", (sid, seq, fingerprint))
        ai = self.runtime.ingest(frame)
        fixed = rule_response["result"]
        running = body["phase"] == "running"
        alerts = [dict(alert, kind="fixed") for alert in fixed["alerts"] if not alert.get("held") and alert["id"] != "pattern_change"] if running else []
        if running and ai["persistent_change"]:
            alerts.append({"id": "ai_pattern_change", "kind": "ai", "severity": "watch", "title": "평소 패턴과 다른 상태 지속",
                           "fields": [item["field"] for item in ai["current_comparison"].get("evidence", [])],
                           "checks": ai["checks"], "reason": ai["summary"], "model_id": ai["model_id"], "control_action": "none"})
        if not running:
            status = "inactive"
        elif any(a["severity"] == "urgent" for a in alerts):
            status = "urgent"
        elif alerts:
            status = "watch"
        elif not fixed["coverage"]["complete"] or fixed["coverage"]["derived_unknown_fields"] or ai["state"] in ("unknown", "baseline_collecting"):
            status = "unknown"
        else:
            status = "normal"
        previous = set(json.loads(row["active_alert_ids"]))
        current = {a["id"] for a in alerts}
        notifications = [{"id": f"{sid}:{seq}:{a['id']}", "title": a["title"], "severity": a["severity"], "kind": a["kind"]}
                         for a in alerts if a["id"] not in previous]
        result = {"schema_version": "genguard.bridge.result/1", "session_id": sid, "seq": seq,
                  "simulated_elapsed_s": elapsed, "time_policy": TIME_POLICY, "is_demonstration": True,
                  "data_domain": "scenario", "duplicate": False, "status": status, "ai": ai,
                  "fixed": fixed, "alerts": alerts, "notification_events": notifications}
        with self.runtime.db:
            self.runtime.db.execute("INSERT INTO bridge_samples VALUES (?,?,?,?)", (sid, seq, fingerprint, canonical(result)))
            self.runtime.db.execute("UPDATE bridge_sessions SET rule_state=?,active_alert_ids=? WHERE session_id=?",
                                    (canonical(rule_response["next_state"]), canonical(sorted(current)), sid))
            self.runtime.db.execute("DELETE FROM bridge_pending WHERE session_id=? AND seq=?", (sid, seq))
        return result
