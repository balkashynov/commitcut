#!/usr/bin/env python3
"""Assert failure AND recovery controls; expected findings are a successful test."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from commitcut.runner import Config, run_matrix

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--upstream", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = Path(args.output).resolve()
scenarios = [("stripe-credits", "claim-first", 2), ("stripe-credits", "effect-first", 2), ("stripe-credits", "atomic", 0)]
if args.upstream:
    scenarios += [("opensaas", "upstream", 4), ("opensaas", "transactional-control", 0)]
summary = []
for folder, mode, expected in scenarios:
    print(f"\nVerifying {folder}/{mode}", flush=True)
    report = run_matrix(Config.load(root / "examples" / folder / (mode + ".toml"), include_sql=True), output / mode)
    assert report["baseline"]["status"] == "pass", report["baseline"].get("error")
    coverage = report["coverage"]
    assert coverage["inconclusive"] == 0, report
    assert coverage["tested_cuts"] == coverage["possible_cuts"], coverage
    assert coverage["violations"] == expected, coverage
    failures = [a["actual"] for c in report["cases"] if c["status"] == "violation" for a in c["assertions"] if not a["passed"]]
    if mode in ("claim-first", "upstream"):
        assert set(failures) == {"0"}, failures
    if mode == "effect-first":
        assert set(failures) == {"20"}, failures
    summary.append({"scenario": f"{folder}/{mode}", **coverage, "elapsed_seconds": report["elapsed_seconds"]})
(output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print("All expected failure and recovery controls verified.")
