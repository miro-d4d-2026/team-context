"""Write explicitly simulated controller values on the UNO Q local clock.

Use ONLY profiles/fusion-hybrid-demo.json with real IMU, never a live profile.
No values are read from or sent to a physical generator.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genguard.core import atomic_json
from genguard.demo import frames


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--condition", choices=["normal", "thermal_pressure", "stale_controller"], default="normal")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--windows", type=int, default=300)
    args = p.parse_args()
    print("SIMULATED controller only: use hybrid_demo; not physical generator data.", flush=True)
    try:
        for frame in frames("simulator", args.seed, args.condition, args.windows):
            readings = {k: v for k, v in frame["readings"].items() if k not in {"vibration_rms_g", "envelope_cv"}}
            atomic_json(args.output, {"monotonic_s": time.monotonic(), "phase": "running", "readings": readings})
            time.sleep(2)
    except KeyboardInterrupt:
        print("Stopped; snapshot is retained and becomes stale rather than reporting normal.")


if __name__ == "__main__":
    main()
