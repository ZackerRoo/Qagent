#!/usr/bin/env python3
"""Read two offline JSONs and write a new descriptive report; no database access."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from qagent.research.selection_segments import build_selection_segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    replay_raw = args.replay.read_bytes()
    replay = json.loads(replay_raw)
    if replay.get("source_file_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("source file does not match replay")
    report = build_selection_segments(json.loads(raw), replay)
    report["input_sha256"] = {"source": hashlib.sha256(raw).hexdigest(),
                              "replay": hashlib.sha256(replay_raw).hexdigest()}
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: v["total"] for k, v in report["cohorts"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
