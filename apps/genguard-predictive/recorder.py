"""Append-only local observations and separate model analyses for later review.

Files are created lazily, paired by record_id, flushed after each append, and
rotated together before either exceeds max_file_bytes. No automatic deletion or
training occurs. Flush is not an fsync/power-loss guarantee; the two files are
not an atomic transaction. Partial writes are surfaced and a new part is used
after an I/O error, so later records never append to a possibly truncated line.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from numbers import Integral, Real
from pathlib import Path
import threading
import uuid


def _safe_json(value, count):
    """JSON-safe copy, preserving input ownership and marking nonfinite values."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        if not math.isfinite(value):
            count[0] += 1
            return None
        return float(value)
    if isinstance(value, dict):
        return {str(k): _safe_json(v, count) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v, count) for v in value]
    raise TypeError("unsupported recording value: " + type(value).__name__)


class Recorder:
    """Recorder(directory, max_file_bytes=20MiB); record() never raises on I/O.

    Metadata keys record_id/received_at_utc/model_id/schema_version are owned by
    this recorder. Telemetry contains a flat copy of the incoming observation;
    no prediction or diagnostic target is added to it. Analyses are in another
    file and must not be treated as independently verified training labels.
    Total disk use is not capped: operators choose retention outside this class.
    """
    def __init__(self, directory, max_file_bytes=20 * 1024 * 1024):
        if (not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool)
                or max_file_bytes <= 0):
            raise ValueError("max_file_bytes must be a positive integer")
        self.directory = Path(directory)
        self.max_file_bytes = max_file_bytes
        self.lock = threading.RLock()
        self.session_id = None
        self.part = 0
        self.sequence = 0
        self.records_written = 0
        self.telemetry_records_written = 0
        self.analysis_records_written = 0
        self.failed_records = 0
        self._telemetry = self._analyses = None
        self._telemetry_path = self._analyses_path = None
        self._telemetry_bytes = self._analysis_bytes = 0
        self._closed = False
        self._last_result = {"ok": True, "error": None, "partial": False,
                             "telemetry_written": False, "analysis_written": False,
                             "write_outcome_uncertain": False}

    def status(self):
        with self.lock:
            return dict(self._last_result, session_id=self.session_id, part=self.part,
                        directory=str(self.directory), records_written=self.records_written,
                        telemetry_records_written=self.telemetry_records_written,
                        analysis_records_written=self.analysis_records_written,
                        failed_records=self.failed_records,
                        telemetry_path=str(self._telemetry_path) if self._telemetry_path else None,
                        analyses_path=str(self._analyses_path) if self._analyses_path else None,
                        telemetry_part_bytes=self._telemetry_bytes,
                        analyses_part_bytes=self._analysis_bytes,
                        max_file_bytes=self.max_file_bytes, closed=self._closed,
                        online_training=False, automated_retention=False)

    def _close_handles(self):
        errors = []
        for name in ("_telemetry", "_analyses"):
            handle = getattr(self, name)
            setattr(self, name, None)
            if handle is not None:
                try:
                    handle.close()
                except OSError as exc:
                    errors.append(str(exc))
        return errors

    def _open_next_part(self):
        errors = self._close_handles()
        if errors:
            raise OSError("closing previous recording part: " + "; ".join(errors))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.part += 1
        prefix = f"{self.session_id}-part{self.part:04d}"
        self._telemetry_path = self.directory / f"{prefix}-telemetry.jsonl"
        self._analyses_path = self.directory / f"{prefix}-analyses.jsonl"
        self._telemetry_bytes = self._analysis_bytes = 0
        try:
            # Exclusive creation: never truncate a previous run or collision.
            self._telemetry = self._telemetry_path.open("xb")
            self._analyses = self._analyses_path.open("xb")
        except OSError:
            self._close_handles()
            raise

    @staticmethod
    def _write_line(handle, blob):
        if handle.write(blob) != len(blob):
            raise OSError("short recording write")
        handle.flush()

    def record(self, sample, analysis, model_id):
        """Append one observation/result pair; return counters and visible error.

        Written flags mean write+flush completed, not durable fsync. On failure,
        an orphan telemetry record or truncated last line can exist in the old
        part; record_id permits pairing, and partial/uncertain flags expose it.
        """
        with self.lock:
            result = {"ok": False, "error": None, "partial": False,
                      "telemetry_written": False, "analysis_written": False,
                      "write_outcome_uncertain": False}
            writing = False
            try:
                if self._closed:
                    raise ValueError("recorder is closed")
                if not isinstance(sample, dict) or not isinstance(analysis, dict):
                    raise TypeError("sample and analysis must be dictionaries")
                if not isinstance(model_id, str) or not model_id:
                    raise ValueError("model_id must be a nonempty string")
                now = datetime.now(timezone.utc)
                if self.session_id is None:
                    self.session_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
                self.sequence += 1
                record_id = f"{self.session_id}:{self.sequence:08d}"
                received = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
                input_count, analysis_count = [0], [0]
                telemetry = _safe_json(sample, input_count)
                prediction = _safe_json(analysis, analysis_count)
                telemetry.update(schema_version="genguard.recorded-telemetry/1", record_id=record_id,
                                 received_at_utc=received, model_id=model_id,
                                 recorder_nonfinite_values_replaced=input_count[0])
                prediction.update(schema_version="genguard.recorded-analysis/1", record_id=record_id,
                                  received_at_utc=received, model_id=model_id,
                                  timestamp_s=telemetry.get("timestamp_s"), run_id=telemetry.get("run_id"),
                                  recorder_nonfinite_values_replaced=analysis_count[0],
                                  label_status="model_output_not_verified_ground_truth")
                blobs = [json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
                         for data in (telemetry, prediction)]
                if any(len(blob) > self.max_file_bytes for blob in blobs):
                    raise ValueError("single recording exceeds max_file_bytes; no append performed")
                if (self._telemetry is None or self._analyses is None
                        or self._telemetry_bytes + len(blobs[0]) > self.max_file_bytes
                        or self._analysis_bytes + len(blobs[1]) > self.max_file_bytes):
                    self._open_next_part()
                writing = True
                self._write_line(self._telemetry, blobs[0])
                result["telemetry_written"] = True
                self._telemetry_bytes += len(blobs[0])
                self.telemetry_records_written += 1
                self._write_line(self._analyses, blobs[1])
                result["analysis_written"] = True
                self._analysis_bytes += len(blobs[1])
                self.analysis_records_written += 1
                self.records_written += 1
                result.update(ok=True, record_id=record_id, nonfinite_values_replaced=input_count[0] + analysis_count[0])
            except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
                self.failed_records += 1
                result.update(error=f"{type(exc).__name__}: {exc}",
                              partial=result["telemetry_written"] != result["analysis_written"],
                              write_outcome_uncertain=writing)
                # A failed write may leave a partial line. Do not append to it.
                self._close_handles()
            self._last_result = result
            return self.status()

    def close(self):
        with self.lock:
            errors = self._close_handles()
            self._closed = True
            if errors:
                self._last_result.update(ok=False, error="OSError: " + "; ".join(errors),
                                         write_outcome_uncertain=True)
            return self.status()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

