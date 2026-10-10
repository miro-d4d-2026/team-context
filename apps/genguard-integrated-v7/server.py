#!/usr/bin/env python3
"""Run on UNO Q Linux (or a Mac for preflight): python server.py --port 9879."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
from urllib.parse import urlsplit, parse_qs

from bridge_service import BridgeError, ROOT
from operation_service import OperationService as HybridBridgeService

MAX_BODY = 64 * 1024
STATIC = {
    "/lab": ("web/index.html", "text/html; charset=utf-8"),
    "/inspection.css": ("web/inspection.css", "text/css; charset=utf-8"),
    "/inspection.mjs": ("web/inspection.mjs", "text/javascript; charset=utf-8"),
    "/": ("web/operation.html", "text/html; charset=utf-8"),
    "/comparison": ("web/operation.html", "text/html; charset=utf-8"),
    "/records": ("web/operation.html", "text/html; charset=utf-8"),
    "/operation.css": ("web/operation.css", "text/css; charset=utf-8"),
    "/operation.mjs": ("web/operation.mjs", "text/javascript; charset=utf-8"),
    "/learning.mjs": ("web/learning.mjs", "text/javascript; charset=utf-8"),
    "/inspection-v5": ("web/inspection.html", "text/html; charset=utf-8"),
    "/index.html": ("web/index.html", "text/html; charset=utf-8"),
    "/web/index.html": ("web/index.html", "text/html; charset=utf-8"),
    "/web/client.mjs": ("web/client.mjs", "text/javascript; charset=utf-8"),
    "/web/demo.mjs": ("web/demo.mjs", "text/javascript; charset=utf-8"),
    "/web/style.css": ("web/style.css", "text/css; charset=utf-8"),
    "/client.mjs": ("web/client.mjs", "text/javascript; charset=utf-8"),
    "/demo.mjs": ("web/demo.mjs", "text/javascript; charset=utf-8"),
    "/style.css": ("web/style.css", "text/css; charset=utf-8"),
    "/trend.mjs": ("web/trend.mjs", "text/javascript; charset=utf-8"),
    "/web/trend.mjs": ("web/trend.mjs", "text/javascript; charset=utf-8"),
}
SESSION_ROUTE = re.compile(r"/api/sessions/(bridge-[0-9a-f]{32})/(samples|close)\Z")


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_origin(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Allowed origins must be exact http(s) origins, e.g. http://localhost:5173")
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "GenGuardBridge/1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt, *args):
        # Avoid request URLs, bearer tokens, or request body data in access logs.
        sys.stderr.write(f"[HTTP] {self.command} {getattr(self, 'response_status', '-')}\n")

    def send_response(self, code, message=None):
        self.response_status = code
        super().send_response(code, message)

    def _security(self, authorize=True):
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://" + host)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            raise BridgeError(400, "invalid_host", "Host 헤더를 확인하세요.")
        if not hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise BridgeError(400, "invalid_host", "Host 헤더를 확인하세요.")
        if self.server.loopback_only and not is_loopback(hostname):
            raise BridgeError(403, "host_not_allowed", "로컬 서버에는 localhost 또는 루프백 IP로 접근하세요.")
        origin = self.headers.get("Origin")
        own_origin = "http://" + host
        if origin and origin != own_origin and origin not in self.server.allowed_origins:
            raise BridgeError(403, "origin_not_allowed", "허용되지 않은 웹 출처입니다. --allow-origin에 정확한 주소를 추가하세요.")
        if authorize and self.server.token:
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + self.server.token
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise BridgeError(401, "unauthorized", "API 토큰을 확인하세요.")

    def _headers(self, status, content_type, size):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        origin = self.headers.get("Origin")
        if origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        self._headers(status, "application/json; charset=utf-8", len(encoded))
        self.wfile.write(encoded)

    def _body(self):
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
            raise BridgeError(400, "unsupported_encoding", "압축 또는 chunked 요청은 지원하지 않습니다.")
        if self.headers.get_content_type() != "application/json":
            raise BridgeError(415, "content_type", "Content-Type: application/json이 필요합니다.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise BridgeError(411, "content_length", "올바른 Content-Length가 필요합니다.")
        length = int(lengths[0])
        if length > MAX_BODY:
            raise BridgeError(413, "request_too_large", "요청 본문은 64 KiB 이하여야 합니다.")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BridgeError(400, "incomplete_body", "요청 본문 수신이 완료되지 않았습니다.")
        try:
            return json.loads(raw.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise BridgeError(400, "invalid_json", "유효한 JSON을 보내세요. NaN, Infinity, 중복 키는 허용하지 않습니다.")

    def _run(self, fn):
        try:
            fn()
        except BridgeError as exc:
            self._json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception as exc:
            # No filesystem paths, request bodies, or credentials in responses.
            sys.stderr.write(f"[server error] {type(exc).__name__}\n")
            self._json(500, {"error": {"code": "internal_error", "message": "서버 처리에 실패했습니다. 서버 로그를 확인하세요."}})

    def do_GET(self):
        def run():
            self._security(authorize=self.path not in STATIC and self.path != "/api/health")
            parsed = urlsplit(self.path)
            if self.path == "/ui-api/state":
                self._json(200, self.server.service.learning_state())
            elif parsed.path in ("/ui-api/session", "/ui-api/raw"):
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) != {"id"} or len(query["id"]) != 1:
                    raise BridgeError(422, "invalid_session", "조회할 기록 ID를 하나 선택하세요.")
                if parsed.path == "/ui-api/raw":
                    payload = self.server.service.learning_raw(query["id"][0])
                    self._headers(200, payload['content_type'], len(payload['bytes']))
                    self.wfile.write(payload['bytes'])
                else:
                    self._json(200, self.server.service.learning_session(query["id"][0]))
            elif self.path == "/api/health":
                result = self.server.service.health()
                self._json(200 if result["ready"] else 503, result)
            elif self.path == "/api/inspections/overview":
                self._json(200, self.server.service.inspection_overview())
            elif self.path == "/api/operation/overview":
                self._json(200, self.server.service.operation_overview())
            elif self.path == "/api/vibration/catalog":
                self._json(200, self.server.service.vibration_catalog())
            elif self.path == "/api/config":
                self._json(200, self.server.service.config())
            elif self.path == "/api/imu":
                self._json(200, self.server.service.imu_status())
            elif self.path == "/api/imu/history":
                self._json(200, self.server.service.imu_history())
            elif self.path == "/api/trend/catalog":
                self._json(200, self.server.service.trend_catalog())
            elif self.path in STATIC:
                filename, kind = STATIC[self.path]
                target = ROOT / filename
                if not target.is_file():
                    raise BridgeError(404, "file_not_found", "시연 화면 파일이 없습니다. 패키지 전체를 복사하세요.")
                data = target.read_bytes()
                self._headers(200, kind, len(data))
                self.wfile.write(data)
            else:
                raise BridgeError(404, "not_found", "지원하지 않는 경로입니다.")
        self._run(run)

    def do_POST(self):
        def run():
            self._security()
            body = self._body()
            if self.path.startswith("/ui-api/"):
                action = self.path[len("/ui-api/"):]
                if action not in {"start", "stop", "review", "label", "revoke-label", "training-plan", "train", "activate", "note", "demo", "import-recording"}:
                    raise BridgeError(404, "not_found", "지원하지 않는 학습 작업입니다.")
                self._json(200, self.server.service.learning_dispatch(action, body))
                return
            operation_routes = {"/api/operation/evaluate": "operation_evaluate", "/api/operation/save": "operation_save", "/api/operation/retrain": "operation_retrain", "/api/operation/linked-simulation": "operation_linked_simulation"}
            if self.path in operation_routes:
                self._json(200, getattr(self.server.service, operation_routes[self.path])(body))
                return
            if self.path == "/api/vibration/replay":
                self._json(200, self.server.service.vibration_replay(body))
                return
            inspection_routes = {"/api/inspections/compare": "inspection_compare", "/api/inspections/save": "inspection_save", "/api/inspections/retrain": "inspection_retrain"}
            if self.path in inspection_routes:
                self._json(200, getattr(self.server.service, inspection_routes[self.path])(body))
                return
            if self.path == "/api/trend/experiment":
                self._json(200, self.server.service.trend_experiment(body))
                return
            if self.path == "/api/imu/windows":
                self._json(200, self.server.service.receive_imu(body))
                return
            if self.path == "/api/imu/calibration/start":
                self._json(200, self.server.service.begin_calibration(body))
                return
            if self.path == "/api/sessions":
                self._json(200, self.server.service.start(body))
                return
            match = SESSION_ROUTE.fullmatch(self.path)
            if not match:
                raise BridgeError(404, "not_found", "지원하지 않는 경로입니다.")
            sid, action = match.groups()
            result = self.server.service.sample(sid, body) if action == "samples" else self.server.service.close_session(sid, body)
            self._json(200, result)
        self._run(run)

    def do_OPTIONS(self):
        def run():
            self._security(authorize=False)
            if not self.path.startswith(("/api/", "/ui-api/")):
                raise BridgeError(404, "not_found", "지원하지 않는 경로입니다.")
            method = self.headers.get("Access-Control-Request-Method", "")
            headers = {item.strip().lower() for item in self.headers.get("Access-Control-Request-Headers", "").split(",") if item.strip()}
            if method not in ("GET", "POST") or headers - {"authorization", "content-type"}:
                raise BridgeError(403, "cors_not_allowed", "요청한 메서드 또는 헤더가 허용되지 않습니다.")
            self._headers(204, "application/json", 0)
        self._run(run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9879)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "state")
    parser.add_argument("--imu-baseline-file", type=Path,
                        help="Explicitly reuse an exported normal IMU baseline after sensor identity verification")
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument("--token", default=os.environ.get("GENGUARD_API_TOKEN", ""))
    args = parser.parse_args()
    def stop_cleanly(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_cleanly)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")
    local = is_loopback(args.host)
    if not local and not args.token:
        parser.error("LAN binding requires --token or GENGUARD_API_TOKEN")
    if args.token and (len(args.token) < 16 or not args.token.isascii() or any(c.isspace() for c in args.token)):
        parser.error("Token must be at least 16 ASCII characters with no whitespace")
    try:
        allowed = {validate_origin(value) for value in args.allow_origin}
    except ValueError as exc:
        parser.error(str(exc))
    service, server = None, None
    try:
        service = HybridBridgeService(args.state_dir, imu_baseline_file=args.imu_baseline_file)
        server_cls = HTTPServer
        if ":" in args.host:
            class IPv6Server(HTTPServer):
                address_family = socket.AF_INET6
            server_cls = IPv6Server
        server = server_cls((args.host, args.port), Handler)
        server.service, server.token, server.allowed_origins, server.loopback_only = service, args.token, allowed, local
        display_host = f"[{args.host}]" if ":" in args.host else args.host
        print(f"GenGuard hybrid bridge ready: http://{display_host}:{args.port} | model={service.bundle['model_id']} | IMU + mock generator", flush=True)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if server:
            server.server_close()
        if service:
            service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
