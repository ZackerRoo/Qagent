#!/usr/bin/env python3
"""Seal and evaluate isolated financial forward research with immutable archives."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal as process_signal
import stat
import tempfile
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import evaluate_financial_challenger as forward

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_INPUT_FILES = 2000
# Existing ready G2 signals are about 56 MiB and are valid optional baselines.
MAX_INPUT_BYTES = 128 * 1024 * 1024


class RunBudgetExceeded(TimeoutError):
    pass


@contextmanager
def _alarm_timeout(seconds: float):
    if seconds <= 0:
        raise RunBudgetExceeded("run_budget_exhausted")

    def expired(signum, frame):
        raise RunBudgetExceeded("run_budget_exhausted")

    previous_handler = process_signal.getsignal(process_signal.SIGALRM)
    previous_timer = process_signal.getitimer(process_signal.ITIMER_REAL)
    process_signal.signal(process_signal.SIGALRM, expired)
    process_signal.setitimer(process_signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        process_signal.setitimer(process_signal.ITIMER_REAL, 0)
        process_signal.signal(process_signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            process_signal.setitimer(process_signal.ITIMER_REAL, *previous_timer)


def _evaluate_with_timeout(signal_value: dict, db: Path, provider_mode: str,
                           current: datetime, timeout_seconds: float) -> dict:
    # cron invokes this on the Linux main thread. SIGALRM interrupts the same
    # process and DB connection; unlike a worker thread, no query survives a timeout.
    with _alarm_timeout(timeout_seconds):
        return forward.evaluate(signal_value, db, provider_mode=provider_mode, as_of=current)


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("unsafe_or_oversized_artifact")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("artifact_object_required")
    return value


def _json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("unsafe_artifact_directory")
    files = sorted(directory.glob("*.json"))
    if len(files) > MAX_INPUT_FILES:
        raise ValueError("artifact_file_budget_exceeded")
    return files


def _publish(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                         allow_nan=False) + "\n"
    fd, pending = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(encoded)
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
        os.link(pending, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def _daily_candidates(directory: Path, day, now: datetime) -> tuple[list[tuple[datetime, Path, dict]], list[dict]]:
    valid, rejected = [], []
    for path in _json_files(directory):
        try:
            document = _load(path)
            if document.get("status") != "observed" or document.get("trade_date") != day.strftime("%Y%m%d"):
                continue
            # A baseline is deliberately absent here: this validates and orders
            # the financial evidence without allowing a later G2 file to choose
            # which daily artifact becomes the day's signal.
            forward.seal(document, now=now)
            valid.append((forward.timestamp(document["finished_at"]), path, document))
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            rejected.append({"path": str(path), "reason": type(exc).__name__})
    valid.sort(key=lambda item: (item[0], item[1].name))
    return valid, rejected


def _baseline(directory: Path | None, document: dict, now: datetime) -> tuple[dict | None, str | None]:
    if directory is None:
        return None, None
    candidates = []
    for path in _json_files(directory):
        try:
            value = _load(path)
            if value.get("status") != "ready" or value.get("signal_date") != now.date().isoformat():
                continue
            sealed = forward.seal(document, value, now=now)
            if sealed["baseline_status"] == "available":
                candidates.append((forward.timestamp(value["collected_at_utc"]), path, value))
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
            continue
    candidates.sort(key=lambda item: (item[0], item[1].name))
    return (candidates[0][2], str(candidates[0][1])) if candidates else (None, None)


def _validate_evaluation_archive(value: dict, signal: dict, horizon: int) -> None:
    forward.verify_digest(value)
    if (value.get("protocol") != "financial-rule-forward-horizon-v1"
            or value.get("signal_digest") != signal["result_digest"]
            or value.get("horizon_sessions") != horizon
            or value.get("evaluation", {}).get("status") != "complete"
            or not isinstance(value.get("runtime_identity"), dict)
            or not isinstance(value.get("evaluation_result_digest"), str)):
        raise ValueError("evaluation_archive_mismatch")


def _archive_horizon(signal: dict, result: dict, horizon: dict, output: Path) -> str:
    target = output / signal["signal_date"] / f"{horizon['horizon_sessions']}.json"
    if target.exists() or target.is_symlink():
        _validate_evaluation_archive(_load(target), signal, horizon["horizon_sessions"])
        return "already_archived"
    archive = {
        "protocol": "financial-rule-forward-horizon-v1",
        "signal_date": signal["signal_date"],
        "signal_digest": signal["result_digest"],
        "policy_digest": signal["policy_digest"],
        "provider_mode": result["provider_mode"],
        "evaluation_result_digest": result["result_digest"],
        "evaluated_at": result["as_of"],
        "runtime_identity": result["runtime_identity"],
        "horizon_sessions": horizon["horizon_sessions"],
        "evaluation": horizon,
        "decision_weight": False,
        "activation_allowed": False,
    }
    archive["result_digest"] = forward.digest(archive)
    _publish(target, archive)
    return "archived"


def run(daily_dir: Path, signal_dir: Path, evaluation_dir: Path, run_dir: Path,
        db: Path, *, baseline_dir: Path | None = None, provider_mode: str = "free",
        budget_seconds: float = 300, now: datetime | None = None) -> tuple[int, dict]:
    if (isinstance(budget_seconds, bool) or not math.isfinite(budget_seconds)
            or not 30 <= budget_seconds <= 900):
        raise ValueError("invalid_budget")
    current = now or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        raise ValueError("timezone_required")
    current = current.astimezone(SHANGHAI)
    started = time.monotonic()
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(run_dir / ".financial-forward.lock",
                      os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ValueError("invalid_lock")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            conflict = {"protocol": "financial-forward-automation-run-v1",
                        "started_at": current.isoformat(),
                        "finished_at": datetime.now(SHANGHAI).isoformat(),
                        "status": "skipped", "reason": "financial_forward_locked",
                        "budget_seconds": budget_seconds, "provider_mode": provider_mode,
                        "runtime_identity": forward.evaluation_runtime_identity(),
                        "decision_weight": False, "activation_allowed": False}
            conflict["result_digest"] = forward.digest(conflict)
            filename = current.strftime("%Y%m%dT%H%M%S.%f%z") + "-lock-" + uuid4().hex + ".json"
            _publish(run_dir / filename, conflict)
            return 75, conflict
        report = {
            "protocol": "financial-forward-automation-run-v1",
            "started_at": current.isoformat(),
            "budget_seconds": budget_seconds,
            "provider_mode": provider_mode,
            "runtime_identity": forward.evaluation_runtime_identity(),
            "seal": {},
            "evaluations": [],
            "errors": [],
            "decision_weight": False,
            "activation_allowed": False,
        }
        signal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = signal_dir / f"{current.date().isoformat()}.json"
        candidates, rejected = _daily_candidates(daily_dir, current.date(), current)
        report["seal"]["rejected_daily_artifacts"] = rejected
        try:
            if target.exists() or target.is_symlink():
                signal = _load(target)
                forward.validate_archive(signal)
                if signal.get("signal_date") != current.date().isoformat():
                    raise ValueError("existing_signal_date_mismatch")
                if candidates and signal.get("source_result_digest") != candidates[0][2].get("result_digest"):
                    raise ValueError("existing_signal_not_first_legal_daily_artifact")
                report["seal"].update(status="already_sealed", signal=str(target),
                                      signal_digest=signal["result_digest"])
            elif candidates:
                _, daily_path, document = candidates[0]
                baseline, baseline_path = _baseline(baseline_dir, document, current)
                signal = forward.seal(document, baseline, now=current)
                _publish(target, signal)
                report["seal"].update(status="sealed", daily=str(daily_path),
                                      baseline=baseline_path, signal=str(target),
                                      signal_digest=signal["result_digest"])
            else:
                report["seal"]["status"] = "blocked_no_legal_daily" if rejected else "waiting_for_daily"
                if rejected:
                    report["errors"].append({"stage": "seal", "reason": "no_legal_observed_daily"})
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            report["seal"]["status"] = "blocked"
            report["errors"].append({"stage": "seal", "reason": type(exc).__name__})

        try:
            resolved_db = db.resolve(strict=True)
        except OSError as exc:
            resolved_db = None
            report["errors"].append({"stage": "database", "reason": type(exc).__name__})
        try:
            signal_paths = _json_files(signal_dir)
        except (ValueError, OSError) as exc:
            signal_paths = []
            report["errors"].append({"stage": "signals", "reason": type(exc).__name__})
        budget_exhausted = False
        for signal_path in signal_paths:
            if time.monotonic() - started >= budget_seconds:
                report["errors"].append({"stage": "evaluate", "reason": "run_budget_exhausted"})
                break
            item = {"signal": str(signal_path), "horizons": []}
            try:
                signal = _load(signal_path)
                forward.validate_archive(signal)
                if resolved_db is None:
                    raise ValueError("database_unavailable")
                remaining = budget_seconds - (time.monotonic() - started)
                result = _evaluate_with_timeout(signal, resolved_db, provider_mode, current, remaining)
                for horizon in result["horizons"]:
                    state = horizon["status"]
                    archive_status = "waiting_for_maturity" if state == "waiting_for_maturity" else "retryable_partial"
                    if state == "complete":
                        archive_status = _archive_horizon(signal, result, horizon, evaluation_dir)
                    summary = {"horizon_sessions": horizon["horizon_sessions"],
                               "status": state, "archive_status": archive_status}
                    if state == "partial":
                        # Missing/unsafe-price evidence remains recoverable in each
                        # immutable run while the final complete path stays free.
                        summary["snapshot"] = horizon
                    item["horizons"].append(summary)
            except RunBudgetExceeded:
                item["error"] = "run_budget_exhausted"
                report["errors"].append({"stage": "evaluate", "signal": str(signal_path),
                                         "reason": "run_budget_exhausted"})
                budget_exhausted = True
            except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
                item["error"] = type(exc).__name__
                report["errors"].append({"stage": "evaluate", "signal": str(signal_path),
                                         "reason": type(exc).__name__})
            report["evaluations"].append(item)
            if budget_exhausted:
                break
        report["finished_at"] = datetime.now(SHANGHAI).isoformat()
        if report["errors"]:
            report["status"] = "incomplete"
        elif report["seal"].get("status") == "waiting_for_daily":
            report["status"] = "waiting_for_daily"
        else:
            report["status"] = "complete"
        report["result_digest"] = forward.digest(report)
        filename = current.strftime("%Y%m%dT%H%M%S.%f%z") + "-" + uuid4().hex + ".json"
        _publish(run_dir / filename, report)
        if report["status"] == "waiting_for_daily" and not report["evaluations"]:
            return 75, report
        return (0 if not report["errors"] else 1), report
    finally:
        os.close(lock_fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--signal-dir", required=True, type=Path)
    parser.add_argument("--evaluation-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--provider-mode", default="free")
    parser.add_argument("--budget-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        code, report = run(args.daily_dir, args.signal_dir, args.evaluation_dir,
                           args.run_dir, args.db, baseline_dir=args.baseline_dir,
                           provider_mode=args.provider_mode, budget_seconds=args.budget_seconds)
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_forward_automation_failed"}))
        return 2
    print(json.dumps({"status": report["status"], "seal": report.get("seal"),
                      "evaluated_signals": len(report.get("evaluations", []))}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
