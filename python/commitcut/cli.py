from __future__ import annotations
import argparse
import sys
from .runner import Config, InvalidRun, run_matrix


def main():
    parser = argparse.ArgumentParser(description="Kill a webhook worker at PostgreSQL acknowledgement boundaries and check recovery.")
    parser.add_argument("config", help="Scenario TOML")
    parser.add_argument("--output", required=True, help="New directory for local evidence")
    parser.add_argument("--cut", type=int, help="Run one observed cycle instead of the full matrix")
    parser.add_argument("--phase", choices=["before", "after"], help="Restrict cuts to one side of the cycle")
    parser.add_argument("--include-sql", action="store_true", help="Include raw SQL in LOCAL traces; use synthetic fixtures")
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
