from __future__ import annotations
import argparse
import sys
from .runner import Config, InvalidRun, run_matrix


def main():
    parser = argparse.ArgumentParser(description="Stop your webhook app during a PostgreSQL request, restart it, retry the webhook, and check the saved data.")
    parser.add_argument("config", help="TOML file with the app command, test webhook, and SQL checks")
    parser.add_argument("--output", required=True, help="New folder for test results and app logs")
    parser.add_argument("--cut", type=int, help="Test only this database request number")
    parser.add_argument("--phase", choices=["before", "after"], help="Stop the app only before or only after PostgreSQL handles the request")
    parser.add_argument("--include-sql", action="store_true", help="Save SQL text in the report; use made-up test data")
    args = parser.parse_args()
    if args.cut is not None and args.cut < 1:
        parser.error("--cut must be positive")
    try:
        config = Config.load(args.config, args.include_sql)
        print(f"CommitCut: {config.data['name']}", flush=True)
        report = run_matrix(config, args.output, selected=args.cut, phase=args.phase)
        print(f"{report['status'].upper()}: {args.output}/report.json")
        if report["baseline"].get("error"):
            print(report["baseline"]["error"], file=sys.stderr)
        return {"pass": 0, "violation": 1, "inconclusive": 2}[report["status"]]
    except (InvalidRun, OSError, ValueError, KeyError) as exc:
        print(f"INCONCLUSIVE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
