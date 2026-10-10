"""On-board commands; training is explicit and only after a run closes."""
import argparse
import json
from pathlib import Path
import sys

from .core import EdgeRuntime, atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="GenGuard local multi-sensor monitor")
    parser.add_argument("--db", required=True)
    parser.add_argument("--profile", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import-run", help="Replay one run of feature windows (JSONL)")
    imp.add_argument("--input", required=True)
    close = commands.add_parser("close-run", help="Explicitly close a run left open after a crash")
    close.add_argument("session")
    review = commands.add_parser("review")
    review.add_argument("session")
    review.add_argument("--status", choices=["normal", "abnormal", "exclude"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", required=True)
    train = commands.add_parser("train")
    train.add_argument("--fit", nargs="+", required=True)
    train.add_argument("--calibration", nargs="+", required=True)
    train.add_argument("--validation", nargs="+", required=True)
    train.add_argument("--output", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--model-file", required=True, help="Candidate already trained in this DB")
    commands.add_parser("sessions")
    export = commands.add_parser("export")
    export.add_argument("--after", type=int, default=0)
    export.add_argument("--limit", type=int, default=1000)
    export.add_argument("--output")
    args = parser.parse_args(argv)
    runtime = EdgeRuntime(args.db, args.profile)
    try:
        if args.command == "import-run":
            session, count, started = None, 0, False
            try:
                with open(args.input) as source:
                    for line in source:
                        if not line.strip():
                            continue
                        frame = json.loads(line)
                        if session is None:
                            session = frame["session_id"]
                            runtime.start_session(session, frame["installation_id"])
                            started = True
                        if frame["session_id"] != session:
                            raise ValueError("One input file must contain one session")
                        runtime.ingest(frame)
                        count += 1
            finally:
                if started:
                    runtime.close_session(session)
            result = {"session_id": session, "windows": count, "review": "unreviewed"}
        elif args.command == "close-run":
            runtime.close_session(args.session)
            result = {"closed": args.session}
        elif args.command == "review":
            runtime.review_session(args.session, args.status, args.reviewer, args.note)
            result = {"session_id": args.session, "review": args.status}
        elif args.command == "train":
            model = runtime.train_candidate(args.fit, args.calibration, args.validation)
            atomic_json(args.output, model)
            result = {k: model[k] for k in ["model_id", "training_seconds", "validation", "data_domain"]}
            result["output"] = args.output
        elif args.command == "activate":
            model_id = json.loads(Path(args.model_file).read_text())["model_id"]
            runtime.activate(model_id)
            result = {"active_model_id": model_id, "effective": "next_run", "reference_model_id": runtime.setting("reference_model")}
        elif args.command == "sessions":
            result = runtime.sessions()
        else:
            rows = runtime.export_results(args.after, args.limit)
            content = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)
            if args.output:
                Path(args.output).write_text(content)
                result = {"records": len(rows), "last_record_id": rows[-1]["record_id"] if rows else args.after}
            else:
                print(content, end="")
                return 0
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"GenGuard: {exc}", file=sys.stderr)
        return 2
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
