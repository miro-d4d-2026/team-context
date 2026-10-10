"""Live/replayed IMU plus mock generator inference; never relabel one as the other."""
from __future__ import annotations

from datetime import datetime
import copy
import json
from pathlib import Path
import time

from bridge_service import BridgeService, BridgeError, canonical, digest, finite
from hybrid_model import HybridAnalyzer, fit_vibration, score_vibration
from baseline_transfer import load_transfer, sensor_identity

IMU_FIELDS = {"vibration_rms_g", "envelope_cv"}
REQUIRED_WINDOWS = 15
MAX_AGE = 5.0
TIME_POLICY = {"kind": "server_elapsed", "recommended_send_interval_ms": 2000,
               "note": "서버의 실제 경과 시간. 진동 지속 판단은 서로 다른 센서 창으로만 진행합니다."}


def unknown(reason):
    return {"state": "unknown", "reason": reason, "score": None, "threshold": None,
            "evidence": [], "checks": [], "change_streak": 0, "persistent_change": False}


class HybridBridgeService(BridgeService):
    def __init__(self, state_dir, clock=time.time, monotonic=time.monotonic, imu_baseline_file=None):
        # Validate before opening a state DB. Reuse is an explicit deployment action.
        transfer = load_transfer(imu_baseline_file) if imu_baseline_file is not None else None
        # A v1 DB cannot silently become a mixed-source experiment.
        state_dir = Path(state_dir)
        owner = state_dir / "hybrid-owner.json"
        if state_dir.exists() and any(state_dir.iterdir()) and not owner.is_file():
            raise ValueError("Use a fresh hybrid state directory; v1 state is not reusable")
        if owner.is_file() and json.loads(owner.read_text()) != {"schema": "genguard.hybrid-owned-state/2"}:
            raise ValueError("Hybrid state owner mismatch")
        super().__init__(state_dir)
        owner.write_text('{"schema":"genguard.hybrid-owned-state/2"}\n')
        self.clock, self.monotonic = clock, monotonic
        self.analyzer = HybridAnalyzer(self.profile, self.bundle)
        self.latest = None
        self.started_at = None
        self.baseline = None
        self.calibration_rows = []
        self.calibration = {"state": "untrained", "count": 0, "required": REQUIRED_WINDOWS,
                            "baseline_id": None, "error": None}
        self.pending_baseline_transfer = transfer
        self.baseline_transfer = None
        self.runtime.db.executescript("""
            CREATE TABLE IF NOT EXISTS hybrid_imu_windows (
                id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, seq INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, captured REAL NOT NULL, received REAL NOT NULL,
                payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hybrid_calibrations (
                id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL,
                window_ids TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hybrid_baseline_transfer_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL,
                payload TEXT NOT NULL);
        """)
        # Restart normally requires calibration. An explicit transfer still waits
        # for a fresh packet with the exact original installation and sensor setup.
        if transfer is not None:
            self.baseline_transfer = {
                "state": "pending", "baseline_id": transfer["baseline"]["baseline_id"],
                "baseline_sha256": transfer["baseline_sha256"],
                "transfer_sha256": transfer["transfer_sha256"],
                "source_stream_id": transfer["source_calibration"]["source_stream_id"],
                "sensor_identity": copy.deepcopy(transfer["sensor_identity"]),
                "coefficients_unchanged": True, "retrained": False,
            }
            self._transfer_event("requested")

    def health(self):
        data = super().health()
        data.update(data_domain="hybrid_demo", time_policy=TIME_POLICY)
        return data

    def config(self):
        fields = []
        for key, spec in self.fields.items():
            item = dict(spec, editable=key not in IMU_FIELDS,
                        source="live_imu" if key in IMU_FIELDS else "scenario")
            if key in IMU_FIELDS:
                item.update(default=None, normal_display="현 부착 상태에서 등록한 IMU 기준",
                            provenance="실측 또는 명시적으로 구분한 기록 재생; 출처는 /api/imu 확인")
            fields.append(item)
        return {"fields": fields, "default_values": {k: f["default"] for k, f in self.fields.items() if k not in IMU_FIELDS},
                "model_id": self.bundle["model_id"], "time_policy": TIME_POLICY,
                "data_domain": "hybrid_demo", "is_demonstration": True,
                "analysis_policy": "모의 발전기 4항목 모델과 현장 진동 기준의 결과 결합. 두 출처 사이 상관관계는 학습하지 않음."}

    def start(self, body):
        previous = self.active_session
        result = super().start(body)
        if not previous:
            self.started_at = self.monotonic()
        result["time_policy"] = TIME_POLICY
        return result

    def _fail_calibration(self, reason):
        if self.baseline_transfer and self.baseline_transfer["state"] in ("pending", "active"):
            self.baseline_transfer.update(state="invalidated", reason=reason)
            self.pending_baseline_transfer = None
            self._transfer_event("invalidated")
        self.baseline = None
        self.calibration.update(state="failed", baseline_id=None, error=reason)
        self.calibration_rows = []

    def imu_status(self):
        latest = self.latest
        result = {"state": "waiting", "source": None, "stream_id": None,
                  "installation_id": None, "window_id": None, "age_s": None, "features": None}
        if latest:
            packet = latest["packet"]
            age = max(0, latest["capture_age"] + self.monotonic() - latest["received_mono"])
            state = "stale" if age > MAX_AGE else "fresh" if packet["quality"]["valid"] else "invalid"
            if state != "fresh" and self.calibration["state"] == "collecting":
                self._fail_calibration("수집 중 센서 품질 또는 연결이 끊겼습니다. 고정 후 기준 등록을 다시 시작하세요.")
            result.update(state=state, source=packet["source"], stream_id=packet["stream_id"],
                          installation_id=packet["installation_id"], window_id=latest["id"], age_s=round(age, 3),
                          features=packet["features"], quality=packet["quality"], measured_at=packet["measured_at"],
                          sample_rate_hz=packet["sample_rate_hz"], sample_count=packet["sample_count"],
                          configured_sample_rate_hz=packet["configured_sample_rate_hz"], accel_range_g=packet["accel_range_g"])
        result["calibration"] = copy.deepcopy(self.calibration)
        if self.baseline_transfer is not None:
            result["baseline_transfer"] = copy.deepcopy(self.baseline_transfer)
        return result

    def _transfer_event(self, event):
        payload = dict(copy.deepcopy(self.baseline_transfer), event=event)
        with self.runtime.db:
            self.runtime.db.execute(
                "INSERT INTO hybrid_baseline_transfer_events(created,payload) VALUES (?,?)",
                (self.clock(), canonical(payload)))

    def _activate_baseline_transfer(self, packet, window_id):
        transfer = self.pending_baseline_transfer
        if transfer is None or not packet["quality"]["valid"]:
            return
        self.pending_baseline_transfer = None  # One attempt; never reactivate on another stream.
        differences = [key for key, value in transfer["sensor_identity"].items()
                       if sensor_identity(packet)[key] != value]
        self.baseline_transfer.update(target_stream_id=packet["stream_id"], activation_window_id=window_id)
        if differences:
            reason = "이전 정상 기준과 센서 조건이 다릅니다: " + ", ".join(differences)
            self.baseline_transfer.update(state="rejected", reason=reason, mismatched_fields=differences)
            self._transfer_event("rejected")
            self._fail_calibration(reason)
            return
        self.baseline = copy.deepcopy(transfer["baseline"])
        self.calibration.update(state="ready", count=REQUIRED_WINDOWS,
                                baseline_id=self.baseline["baseline_id"], error=None)
        self.baseline_transfer.update(state="active", activated_at=self.clock())
        # Preserve original calibration evidence; imported windows are provenance,
        # not new live packets and cannot be replayed through the receive path.
        with self.runtime.db:
            self.runtime.db.execute("INSERT OR REPLACE INTO hybrid_calibrations VALUES (?,?,?,?)",
                (self.baseline["baseline_id"], transfer["source_calibration"]["created_epoch"],
                 canonical(self.baseline), canonical(transfer["source_calibration"]["window_ids"])))
        self._transfer_event("activated")

    def begin_calibration(self, body):
        if body != {"confirmed_normal": True}:
            raise BridgeError(400, "normal_confirmation", "현재 고정된 진동을 비교 기준으로 쓸지 확인해야 합니다.")
        if self.imu_status()["state"] != "fresh":
            raise BridgeError(409, "imu_not_ready", "먼저 유효한 IMU 창을 수신하세요.")
        if self.baseline_transfer and self.baseline_transfer["state"] in ("pending", "active"):
            self.baseline_transfer.update(state="superseded", reason="새 정상 기준 등록을 사용자가 요청했습니다.")
            self.pending_baseline_transfer = None
            self._transfer_event("superseded")
        self.baseline = None
        self.calibration_rows = []
        self.calibration = {"state": "collecting", "count": 0, "required": REQUIRED_WINDOWS,
                            "baseline_id": None, "error": None}
        self.calibration_identity = self._sensor_identity(self.latest["packet"])
        self.calibration_start = self.monotonic()
        self.calibration_last_id = self.latest["id"]
        return self.imu_status()

    @staticmethod
    def _sensor_identity(packet):
        return tuple(packet[k] for k in ("source", "stream_id", "installation_id", "configured_sample_rate_hz", "accel_range_g"))

    def receive_imu(self, body):
        required = {"schema_version", "source", "stream_id", "installation_id", "window_seq", "duration_s",
                    "sample_count", "sample_rate_hz", "configured_sample_rate_hz", "accel_range_g",
                    "measured_at", "features", "quality"}
        if not isinstance(body, dict) or set(body) != required or body["schema_version"] != "genguard.imu.window/1":
            raise BridgeError(400, "invalid_imu", "IMU 창 메시지 형식을 확인하세요.")
        if body["source"] not in ("live_imu", "recorded_imu"):
            raise BridgeError(422, "invalid_imu_source", "실시간 IMU와 기록 재생 출처를 구분하세요.")
        for key in ("stream_id", "installation_id"):
            if not isinstance(body[key], str) or not body[key] or len(body[key]) > 128:
                raise BridgeError(400, "invalid_imu_identity", "stream_id와 installation_id를 확인하세요.")
        if type(body["window_seq"]) is not int or not 1 <= body["window_seq"] <= 1_000_000_000:
            raise BridgeError(400, "invalid_imu_seq", "window_seq는 1 이상의 정수입니다.")
        if type(body["sample_count"]) is not int or not 0 <= body["sample_count"] <= 100000:
            raise BridgeError(400, "invalid_sample_count", "IMU sample_count를 확인하세요.")
        for key in ("duration_s", "sample_rate_hz", "configured_sample_rate_hz", "accel_range_g"):
            if not finite(body[key]):
                raise BridgeError(400, "invalid_imu_number", "IMU 계측 조건은 유한한 숫자여야 합니다.")
        if abs(body["duration_s"] - 2) > .01 or not 0 <= body["sample_rate_hz"] <= 10000 or not 0 < body["configured_sample_rate_hz"] <= 10000 or body["accel_range_g"] not in (2, 4, 8, 16):
            raise BridgeError(400, "invalid_imu_metadata", "2초 창과 센서 출력률/범위를 확인하세요.")
        quality, features = body["quality"], body["features"]
        if not isinstance(quality, dict) or set(quality) != {"valid", "flags"} or type(quality["valid"]) is not bool or not isinstance(quality["flags"], list) or len(quality["flags"]) > 30 or any(not isinstance(f, str) or len(f) > 120 for f in quality["flags"]):
            raise BridgeError(400, "invalid_imu_quality", "IMU quality.valid/flags를 확인하세요.")
        if not isinstance(features, dict) or set(features) != IMU_FIELDS or any(v is not None and (not finite(v) or v < 0) for v in features.values()):
            raise BridgeError(400, "invalid_imu_features", "IMU 특징은 0 이상의 숫자 또는 무효 시 null입니다.")
        if quality["valid"] and (any(v is None for v in features.values()) or quality["flags"] or body["sample_count"] < 32 or body["sample_rate_hz"] <= 0):
            raise BridgeError(400, "contradictory_imu_quality", "유효한 창은 충분한 표본과 특징값이 필요합니다.")
        if features["vibration_rms_g"] is not None and features["vibration_rms_g"] > body["accel_range_g"]:
            raise BridgeError(400, "invalid_imu_features", "RMS가 센서 측정 범위를 벗어났습니다.")
        if features["envelope_cv"] is not None and features["envelope_cv"] > 100:
            raise BridgeError(400, "invalid_imu_features", "간헐성 특징값이 지원 범위를 벗어났습니다.")
        try:
            parsed = datetime.fromisoformat(body["measured_at"].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
            captured = parsed.timestamp()
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise BridgeError(400, "invalid_capture_time", "measured_at에 시간대가 있는 ISO 시각이 필요합니다.")
        identity = body["stream_id"] + ":" + str(body["window_seq"])
        fingerprint = digest(body)
        duplicate = self.runtime.db.execute("SELECT fingerprint FROM hybrid_imu_windows WHERE id=?", (identity,)).fetchone()
        if duplicate:
            if duplicate[0] != fingerprint:
                raise BridgeError(409, "imu_conflict", "같은 IMU 창의 값이 달라졌습니다.")
            return {"id": identity, "accepted": True, "duplicate": True}
        capture_age = self.clock() - captured
        if not -2 <= capture_age <= MAX_AGE:
            raise BridgeError(422, "imu_capture_expired", "오래됐거나 미래 시각인 IMU 창입니다. 장치 시계와 전송 지연을 확인하세요.")
        previous = self.runtime.db.execute("SELECT seq,captured,payload FROM hybrid_imu_windows WHERE stream_id=? ORDER BY seq DESC LIMIT 1", (body["stream_id"],)).fetchone()
        if previous and (body["window_seq"] <= previous["seq"] or captured <= previous["captured"]):
            raise BridgeError(409, "imu_out_of_order", "이전 IMU 창을 다시 현재값으로 사용할 수 없습니다.")
        if previous and self._sensor_identity(json.loads(previous["payload"])) != self._sensor_identity(body):
            raise BridgeError(409, "imu_metadata_changed", "부착/출력률/출처가 바뀌면 새 stream_id로 연결하세요.")
        changed = self.latest and self._sensor_identity(self.latest["packet"]) != self._sensor_identity(body)
        if changed and self.pending_baseline_transfer is None:
            self._fail_calibration("IMU 연결 또는 부착 조건이 변경되었습니다. 새 기준을 등록하세요.")
        if self.calibration["state"] == "collecting" and previous and (body["window_seq"] != previous["seq"] + 1 or captured - previous["captured"] > MAX_AGE):
            self._fail_calibration("기준 수집 중 창 누락이 발생했습니다. 다시 등록하세요.")
        with self.runtime.db:
            self.runtime.db.execute("INSERT INTO hybrid_imu_windows VALUES (?,?,?,?,?,?,?)",
                                    (identity, body["stream_id"], body["window_seq"], fingerprint, captured, self.clock(), canonical(body)))
        self.latest = {"id": identity, "packet": copy.deepcopy(body), "capture_age": max(0, capture_age),
                       "received_mono": self.monotonic()}
        self._activate_baseline_transfer(body, identity)
        if self.calibration["state"] == "collecting":
            if not quality["valid"]:
                self._fail_calibration("기준 수집 중 무효 진동 창이 발생했습니다. 부착·누락·포화를 확인하세요.")
            elif identity != self.calibration_last_id:
                self.calibration_rows.append(dict(copy.deepcopy(body), window_id=identity))
                self.calibration["count"] = len(self.calibration_rows)
                if len(self.calibration_rows) == REQUIRED_WINDOWS:
                    try:
                        span = self.monotonic() - self.calibration_start
                        if span < 28:
                            raise ValueError("기준 창은 실제 약 30초 동안 수집해야 합니다.")
                        self.baseline = fit_vibration(self.calibration_rows)
                        baseline_id = self.baseline.get("baseline_id") or self.baseline.get("id") or digest(self.baseline)[:24]
                        self.baseline["baseline_id"] = baseline_id
                        self.calibration.update(state="ready", baseline_id=baseline_id, error=None)
                        with self.runtime.db:
                            self.runtime.db.execute("INSERT OR REPLACE INTO hybrid_calibrations VALUES (?,?,?,?)", (baseline_id, self.clock(), canonical(self.baseline), canonical([w["window_id"] for w in self.calibration_rows])))
                    except ValueError as exc:
                        self._fail_calibration(str(exc))
        return {"id": identity, "accepted": True, "duplicate": False}

    def validate_hybrid_sample(self, body):
        if not isinstance(body, dict) or body.get("source") != "hybrid_demo":
            raise BridgeError(422, "unsupported_source", "이 API는 실측/재생 IMU + 모의 발전기 혼합 시연용입니다.")
        if isinstance(body.get("values"), dict) and IMU_FIELDS & set(body["values"]):
            raise BridgeError(422, "imu_override_forbidden", "웹에서 진동값을 덮어쓸 수 없습니다. IMU 어댑터가 보낸 창을 사용합니다.")
        if isinstance(body.get("quality"), dict) and IMU_FIELDS & set(body["quality"]):
            raise BridgeError(422, "imu_override_forbidden", "웹에서 IMU 품질을 덮어쓸 수 없습니다.")
        super().validate_sample(dict(body, source="scenario"))

    @staticmethod
    def _persist(component, previous, identity, now, available=True):
        component = copy.deepcopy(component)
        if not available or component["state"] == "unknown":
            component.update(change_streak=0, persistent_change=False, last_identity=identity, last_time=now)
            return component
        same = previous.get("last_identity") == identity
        gap = now - previous.get("last_time", now) > MAX_AGE
        if component["state"] == "within_baseline":
            streak = 0
        elif same:
            streak = previous.get("change_streak", 0)
        elif component["state"] == "change":
            streak = (0 if gap else previous.get("change_streak", 0)) + 1
        else:
            streak = 0
        component.update(change_streak=streak, persistent_change=streak >= 3,
                         last_identity=identity, last_time=previous.get("last_time", now) if same else now)
        return component

    def sample(self, sid, body):
        self.validate_hybrid_sample(body)
        seq, fingerprint = body["seq"], digest(body)
        row = self.runtime.db.execute("SELECT b.*,s.status FROM bridge_sessions b JOIN sessions s ON s.id=b.session_id WHERE b.session_id=?", (sid,)).fetchone()
        if not row:
            raise BridgeError(404, "session_not_found", "해당 시험이 없습니다.")
        duplicate = self.runtime.db.execute("SELECT fingerprint,result FROM bridge_samples WHERE session_id=? AND seq=?", (sid, seq)).fetchone()
        if duplicate:
            if duplicate["fingerprint"] != fingerprint:
                raise BridgeError(409, "sequence_conflict", "원래 요청 그대로 재시도하세요.")
            result = json.loads(duplicate["result"])
            result.update(duplicate=True, notification_events=[],
                          analysis_age_s=max(0, self.clock() - result["analyzed_at_epoch_s"]))
            return result
        if row["status"] != "open" or sid != self.active_session:
            raise BridgeError(409, "session_closed", "시험이 종료됐습니다.")
        accepted = self.runtime.db.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_samples WHERE session_id=?", (sid,)).fetchone()[0]
        if seq != accepted + 1:
            raise BridgeError(409, "sequence_conflict", f"다음 seq는 {accepted + 1}입니다.")
        state = json.loads(row["rule_state"]) if row["rule_state"] else {}
        elapsed = max(0, self.monotonic() - self.started_at)
        imu = self.imu_status()
        readings = {}
        for key, spec in self.fields.items():
            if key in IMU_FIELDS:
                value = (imu["features"] or {}).get(key)
                quality = "ok" if imu["state"] == "fresh" else "stale" if imu["state"] == "stale" else "missing" if value is None else "invalid"
                readings[key] = {"value": value, "unit": spec["unit"], "quality": quality,
                                 "source": "imu", "age_s": imu["age_s"] or 0}
            else:
                value = body["values"].get(key)
                q = body.get("quality", {}).get(key, {})
                readings[key] = {"value": value, "unit": spec["unit"], "source": "scenario",
                                 "quality": q.get("quality", "missing" if value is None else "ok"), "age_s": q.get("age_s", 0)}
        snapshot = {"schema_version": "genguard.alarm-snapshot/1", "scenario_id": sid,
                    "t_s": elapsed, "phase": body["phase"], "readings": readings, "events": {}, "normal_review": False}
        rule_response = self.worker.request({"action": "evaluate", "snapshot": snapshot, "state": state.get("rules")})
        fixed = rule_response["result"]
        running = body["phase"] == "running"
        generator = self.analyzer.score_generator(body["values"], body.get("quality", {})) if running else unknown("inactive_phase")
        previous_gen = state.get("generator", {})
        # Even rapid button clicks cannot manufacture three 2-second comparison windows.
        tick = int(elapsed / 2)
        generator = self._persist(generator, previous_gen, f"gen:{tick}", elapsed, running)
        sensor_ready = imu["state"] == "fresh" and self.baseline is not None and self.calibration["state"] == "ready"
        vibration = score_vibration(imu["features"], self.baseline) if running and sensor_ready else unknown("imu_" + imu["state"] if imu["state"] != "fresh" else "baseline_" + self.calibration["state"])
        previous_vib = state.get("vibration", {})
        identity = (self.calibration.get("baseline_id") or "untrained") + ":" + (imu.get("window_id") or "missing")
        if previous_vib.get("baseline_id") != self.calibration.get("baseline_id"):
            previous_vib = {}
        vibration = self._persist(vibration, previous_vib, identity, elapsed, running and sensor_ready)
        vibration["baseline_id"] = self.calibration.get("baseline_id")
        vibration["source"] = imu["source"]
        for item in vibration.get("evidence", []):
            item["source"] = imu["source"]
        generator["source"] = "scenario"
        # The old .045g mock ratio never assesses an IMU channel. Its explicit
        # source mismatch is replaced only by the independent calibrated verdict.
        for key in IMU_FIELDS:
            metric = fixed["metrics"][key]
            metric["source"] = imu["source"] or "missing"
            metric["basis"] = {"kind": "installation_baseline", "baseline_id": self.calibration.get("baseline_id")}
            # Mock UI min/max cannot invalidate a real sensor that uses another range.
            metric["valid"] = imu["state"] == "fresh" and readings[key]["value"] is not None
            metric["value"] = readings[key]["value"]
            metric["issue"] = None if metric["valid"] else "imu_" + imu["state"]
            metric["status"] = "inactive" if not running else "unknown" if vibration["state"] == "unknown" else "watch" if vibration["persistent_change"] else "normal"
            metric["rule_ids"] = []
        unknown_fields = [k for k in self.fields if not fixed["metrics"][k]["valid"] or fixed["metrics"][k]["status"] == "unknown"]
        provenance = {k: {"source": (imu["source"] or "missing") if k in IMU_FIELDS else "scenario", "quality": readings[k]["quality"], "age_s": readings[k]["age_s"]} for k in self.fields}
        fixed["coverage"].update(unknown_fields=unknown_fields, valid=len(self.fields)-len(unknown_fields), complete=not unknown_fields,
                                 source={k: v["source"] for k, v in provenance.items()}, imu_baseline_available=sensor_ready)
        for metric in fixed["metrics"].values():
            if metric.get("source") == "derived":
                metric["provenance"] = "모의 입력에서 계산"
        alerts = [dict(a, kind="fixed") for a in fixed["alerts"] if not a.get("held") and a["id"] not in ("pattern_change", "vibration_relative")] if running else []
        for name, component, title in (("generator", generator, "모의 발전기 패턴 변화 지속"), ("vibration", vibration, "IMU 진동 기준 대비 변화 지속")):
            if component["persistent_change"]:
                alerts.append({"id": name+"_pattern_change", "kind": "ai", "severity": "watch", "title": title,
                               "source": component["source"], "checks": component["checks"], "control_action": "none"})
        missing = bool(unknown_fields or fixed["coverage"]["derived_unknown_fields"] or generator["state"] == "unknown" or vibration["state"] == "unknown")
        status = "inactive" if not running else "urgent" if any(a["severity"] == "urgent" for a in alerts) else "watch" if alerts else "unknown" if missing else "normal"
        persistent = generator["persistent_change"] or vibration["persistent_change"]
        ai_state = "persistent_change" if persistent else "unknown" if missing else "change_observed" if any(c["state"] == "change" for c in (generator, vibration)) else "within_baseline"
        summary = {"persistent_change": "평소 기준과 다른 변화가 지속됩니다. 각 근거의 실측·모의 출처를 확인하세요.",
                   "unknown": "센서 수신·기준 등록 또는 모의 입력이 부족해 전체 상태를 판단할 수 없습니다.",
                   "change_observed": "변화가 관찰되었습니다. 서로 다른 분석 창에서 지속 여부를 확인 중입니다.",
                   "within_baseline": "실측/재생 진동 기준과 모의 발전기 패턴이 각각 비교 기준 내입니다."}[ai_state]
        evidence = generator.get("evidence", []) + vibration.get("evidence", [])
        checks = list(dict.fromkeys(generator.get("checks", []) + vibration.get("checks", [])))
        ai = {"state": ai_state, "persistent_change": persistent, "summary": summary, "checks": checks,
              "model_id": self.bundle["model_id"], "components": {"generator": generator, "vibration": vibration},
              "current_comparison": {"score": None, "threshold": None, "evidence": evidence},
              "scope": "독립 분석 결과 결합; 실제 진동과 모의 발전기 사이의 고장 상관관계를 학습한 모델이 아님"}
        previous = set(json.loads(row["active_alert_ids"]))
        current = {a["id"] for a in alerts}
        notifications = [{"id": f"{sid}:{seq}:{a['id']}", "title": a["title"], "severity": a["severity"], "kind": a["kind"], "source": a.get("source")} for a in alerts if a["id"] not in previous]
        result = {"schema_version": "genguard.bridge.result/2", "session_id": sid, "seq": seq,
                  "analyzed_at_epoch_s": self.clock(), "analysis_age_s": 0,
                  "elapsed_s": round(elapsed, 3), "time_policy": TIME_POLICY, "is_demonstration": True,
                  "data_domain": "hybrid_demo", "duplicate": False, "status": status, "ai": ai,
                  "fixed": fixed, "imu": imu, "provenance": provenance, "alerts": alerts, "notification_events": notifications}
        state = {"rules": rule_response["next_state"], "generator": generator, "vibration": vibration}
        with self.runtime.db:
            self.runtime.db.execute("INSERT INTO bridge_samples VALUES (?,?,?,?)", (sid, seq, fingerprint, canonical(result)))
            self.runtime.db.execute("UPDATE bridge_sessions SET rule_state=?,active_alert_ids=? WHERE session_id=?", (canonical(state), canonical(sorted(current)), sid))
        return result
