#!/usr/bin/env python3
"""Run with the backend environment; all inputs must already exist offline."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from qagent.backtesting.matched_control import run_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--isolated-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_files(args.source, args.isolated_db, args.output)
    print(json.dumps({"output": str(args.output), "configs_identical": report["configs_identical"],
                      "arms": {key: value["summary"] for key, value in report["arms"].items()}}, ensure_ascii=False))
