"""Read-only loopback API for the existing web UI; no automatic remote upload."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .core import EdgeRuntime, utc_now


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--port", type=int, default=9878)
    p.add_argument("--allow-origin", default="http://127.0.0.1:8765")
    args = p.parse_args()
    probe = EdgeRuntime(args.db, args.profile)
    probe.close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urlparse(self.path)
            db = EdgeRuntime(args.db, args.profile)
            try:
                if query.path == "/results":
                    params = parse_qs(query.query)
                    after = int(params.get("after", ["0"])[0])
                    limit = int(params.get("limit", ["100"])[0])
                    result = {"served_at": utc_now(), "records": db.export_results(after, limit)}
                elif query.path == "/status":
                    result = {"served_at": utc_now(), "active_model": db.setting("active_model"),
                              "reference_model": db.setting("reference_model"),
                              "data_domain": db.profile["data_domain"], "sessions": db.sessions()}
                else:
                    self.send_error(404)
                    return
                payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(200)
                if self.headers.get("Origin") == args.allow_origin:
                    self.send_header("Access-Control-Allow-Origin", args.allow_origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except ValueError as exc:
                self.send_error(400, str(exc))
            finally:
                db.close()

    print(f"Read-only GenGuard results: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
