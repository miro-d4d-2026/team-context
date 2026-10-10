"""Loopback HTTP smoke check against the generated synthetic database."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "genguard.web", "--db", "verification/demo-final/fusion.sqlite",
                             "--profile", "profiles/fusion-demo.json", "--port", str(port)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    url = f"http://127.0.0.1:{port}"
    checks = []
    try:
        for attempt in range(40):
            try:
                with urlopen(url + "/status", timeout=1) as response:
                    status = json.load(response)
                break
            except URLError:
                if proc.poll() is not None:
                    raise RuntimeError(proc.communicate()[1])
                time.sleep(.1)
        else:
            raise RuntimeError("Server did not start")
        assert status["data_domain"] == "scenario" and status["active_model"]
        checks.append("status identifies synthetic domain and active model")
        request = Request(url + "/results?after=0&limit=2", headers={"Origin": "http://127.0.0.1:8765"})
        with urlopen(request, timeout=2) as response:
            assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8765"
            first = json.load(response)["records"]
        assert len(first) == 2 and first[0]["record_id"] < first[1]["record_id"]
        checks.append("bounded ordered results and exact allowed origin")
        request = Request(url + f"/results?after={first[-1]['record_id']}&limit=2", headers={"Origin": "http://example.invalid"})
        with urlopen(request, timeout=2) as response:
            assert response.headers.get("Access-Control-Allow-Origin") is None
            second = json.load(response)["records"]
        assert second[0]["record_id"] > first[-1]["record_id"]
        checks.append("cursor advances without duplicates; other origins receive no CORS permission")
        try:
            urlopen(url + "/results?limit=0", timeout=2)
            raise AssertionError("Invalid limit accepted")
        except HTTPError as exc:
            assert exc.code == 400
        checks.append("invalid limit rejected")
        report = {"passed": True, "checks": checks, "scope": "Mac loopback API with synthetic history, not UNO Q deployment"}
        (ROOT / "verification/web-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        proc.terminate()
        proc.communicate(timeout=5)


if __name__ == "__main__":
    main()
