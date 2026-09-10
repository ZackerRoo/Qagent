"""Run the frozen G2 collector on current-day captures; never fetch or trade."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from qagent.research import g2_risk_feature_forward as forward
from qagent.research.g2_forward_source import atomic_archive, digest


def run(source_dir: Path, frozen_dir: Path, output_dir: Path) -> tuple[int, dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / ".schedule.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75, {"status": "already_running"}
        started = datetime.now(timezone.utc)
        day = started.astimezone(ZoneInfo("Asia/Shanghai")).date()
        report = {"protocol": "g2-forward-schedule-v1", "started_at_utc": started.isoformat(),
                  "signal_date": str(day), "attempts": [], "errors": [],
                  "decision_weight": False, "activation_allowed": False}
        code = 0
        try:
            sessions = forward.trading_sessions_in_range(forward.START, forward.END)
            target = output_dir / "signals" / f"{day}.json"
            if day not in sessions[::10]:
                report["status"] = "skipped_unscheduled_day"
            elif target.exists():
                existing = json.loads(target.read_text())
                unsigned = {key: value for key, value in existing.items() if key != "result_digest"}
                if (existing.get("status") != "ready" or existing.get("signal_date") != str(day)
                        or digest(unsigned) != existing.get("result_digest")):
                    raise ValueError("existing signal archive invalid")
                report.update(status="already_collected", result_digest=existing["result_digest"])
            else:
                candidates = []
                for path in sorted(source_dir.glob(f"{day}-*.json")):
                    try:
                        source = json.loads(path.read_text())
                        if source["signal_date"] != str(day):
                            raise ValueError("source filename and signal_date differ")
                        captured = forward.timestamp(source["captured_at_utc"])
                        candidates.append((captured, path.name, path))
                    except Exception as exc:
                        report["errors"].append({"source": str(path), "error": str(exc)})
                report["status"] = "waiting_for_source"
                for _, _, path in sorted(candidates):
                    try:
                        result = forward.collect(path, frozen_dir, output_dir)
                        report["attempts"].append({"source": str(path), "status": result["status"],
                                                   "reasons": result.get("reasons", [])})
                        report["status"] = result["status"]
                        if result["status"] == "ready":
                            report["result_digest"] = result["result_digest"]
                            break
                    except Exception as exc:
                        report["errors"].append({"source": str(path), "error": str(exc)})
                if report["status"] != "ready":
                    if report["errors"]:
                        code, report["status"] = 1, "source_or_collection_errors"
                    elif report["attempts"]:
                        code = 2  # Non-ready results remain collector diagnostics only.
        except Exception as exc:
            code, report["status"] = 1, "error"
            report["errors"].append({"error": str(exc)})
        report.update(exit_code=code, finished_at_utc=datetime.now(timezone.utc).isoformat())
        filename = started.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex + ".json"
        atomic_archive(output_dir / "runs" / filename, report)
        return code, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--frozen-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    code, report = run(args.source_dir, args.frozen_dir, args.output_dir)
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
