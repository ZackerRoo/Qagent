#!/usr/bin/env python3
"""One-shot operator-reviewed rollout; run as root on the Qagent cloud host."""

import argparse
import shlex
import sys
import copy
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

DB = "/var/lib/qagent/qagent.db"
CURRENT = Path("/opt/qagent/current")
RESUME_COMMITTED = False
ENV = Path("/etc/qagent/qagent.env")
CAPTURE = "/var/lib/qagent-research/g2-forward-sources"
MODELS = Path("/var/lib/qagent-research/g2-frozen-v1")
MANIFEST_FILE_SHA256 = (
    "ac5f88ca79dd3509efcfcdb9167a975f3e07725d8380ecaa03e0e8a2c5692188"
)
TABLES = (
    "watchlist_items",
    "positions",
    "alert_rules",
    "universes",
    "paper_trades",
    "paper_trade_events",
    "paper_account_settings",
    "paper_research_baselines",
)
JOBS = (
    "full_market_scan_jobs",
    "historical_backfill_jobs",
    "walk_forward_jobs",
    "paper_dual_track_jobs",
    "automation_cycles",
    "automation_cycle_stages",
)


def read():
    c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    c.execute("PRAGMA query_only=ON")
    return c


def state():
    with read() as c:
        r = c.execute(
            "SELECT enabled,settings_json,revision FROM automation_scheduler_state WHERE state_id='default'"
        ).fetchone()
    assert r is not None
    return {"enabled": bool(r[0]), "payload": json.loads(r[1]), "revision": r[2]}


def idle():
    with read() as c:
        for t in JOBS:
            assert (
                c.execute(
                    "SELECT count(*) FROM "
                    + t
                    + " WHERE status IN ('queued','running')"
                ).fetchone()[0]
                == 0
            ), "active work: " + t
        for t, col in [
            ("runtime_leases", "expires_at"),
            ("historical_dataset_leases", "lease_expires_at"),
        ]:
            assert (
                c.execute(
                    "SELECT count(*) FROM "
                    + t
                    + " WHERE julianday("
                    + col
                    + ") > julianday('now')"
                ).fetchone()[0]
                == 0
            ), "active lease: " + t
    assert state()["payload"]["runtime"]["in_flight"] is False


def ledger(check_integrity=False):
    result = {}
    with read() as c:
        c.execute("BEGIN")
        if check_integrity:
            assert c.execute("PRAGMA quick_check").fetchone() == ("ok",)
        for t in TABLES:
            cols = [r[1] for r in c.execute('PRAGMA table_info("' + t + '")')]
            assert cols, "missing ledger " + t
            h = hashlib.sha256()
            n = 0
            for row in c.execute(
                'SELECT * FROM "'
                + t
                + '" ORDER BY '
                + ",".join('"' + x + '"' for x in cols)
            ):
                h.update(
                    (
                        json.dumps(
                            row, ensure_ascii=False, separators=(",", ":"), default=str
                        )
                        + "\n"
                    ).encode()
                )
                n += 1
            result[t] = {"rows": n, "sha256": h.hexdigest()}
    return result


def run(*args):
    subprocess.run(args, check=True)


def script(release, name, *args):
    run(str(release / "scripts" / name), *args)


def healthy():
    last = None
    for attempt in range(45):
        try:
            for url in ["http://127.0.0.1:8000/api/health", "http://127.0.0.1:5173/"]:
                with urllib.request.urlopen(url, timeout=3) as r:
                    assert r.status == 200
            return
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError("health failed: " + str(last))


def backend_down():
    run("sv", "-w", "45", "down", "/etc/service/qagent-backend")
    output = subprocess.check_output(["ss", "-ltnH", "sport = :8000"], text=True)
    assert not output.strip(), "backend port still listening"


def restore(saved, settings, expected_ledger):
    global RESUME_COMMITTED
    backend_down()
    idle()
    assert ledger() == expected_ledger, (
        "ledger changed before restore; leaving disabled"
    )
    now = state()
    assert now["payload"]["settings"] == settings
    payload = copy.deepcopy(now["payload"])
    payload["runtime"] = saved["payload"]["runtime"]
    with sqlite3.connect(DB, timeout=30) as c:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            "UPDATE automation_scheduler_state SET enabled=?,settings_json=?,revision=revision+1,updated_at=? WHERE state_id='default' AND revision=? AND enabled=0",
            (
                int(saved["enabled"]),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(" "),
                now["revision"],
            ),
        )
        assert cur.rowcount == 1, "scheduler compare-and-swap failed"
    RESUME_COMMITTED = True
    assert state()["payload"]["settings"] == settings
    assert ledger() == expected_ledger
    run("sv", "-w", "45", "up", "/etc/service/qagent-backend")
    healthy()
    assert state()["payload"]["settings"] == settings


def relay_environment(original, key):
    """Replace only Relay variables, preserving all other environment bytes."""
    if not key or len(key) > 4096 or any(ord(ch) < 32 or ord(ch) > 126 for ch in key):
        raise ValueError("invalid Relay credential")
    names = rb"(?:QAGENT_TUSHARE_RELAY_MARKET_ENABLED|QAGENT_TUSHARE_RELAY_KEY)"
    kept = [
        line
        for line in original.splitlines(keepends=True)
        if not re.match(rb"^\s*(?:export\s+)?" + names + rb"\s*=", line)
    ]
    base = b"".join(kept)
    return (
        base
        + (b"" if not base or base.endswith(b"\n") else b"\n")
        + (
            "QAGENT_TUSHARE_RELAY_MARKET_ENABLED=true\n"
            "QAGENT_TUSHARE_RELAY_KEY=" + shlex.quote(key) + "\n"
        ).encode()
    )


def atomic_environment(expected, replacement):
    """Replace atomically, retaining owner/mode and rejecting concurrent changes."""
    assert ENV.read_bytes() == expected, "environment changed concurrently"
    metadata = ENV.stat()
    descriptor, temporary = tempfile.mkstemp(prefix=".qagent-env-", dir=ENV.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), metadata.st_mode & 0o777)
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        assert ENV.read_bytes() == expected, "environment changed concurrently"
        os.replace(temporary, ENV)
        directory = os.open(ENV.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if not __debug__:
        raise RuntimeError("optimized Python is not supported")
    ap = argparse.ArgumentParser()
    ap.add_argument("release")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--expected-sha", required=True)
    args = ap.parse_args()
    release = Path(args.release).resolve()
    old = CURRENT.resolve()
    assert release.parent == Path("/opt/qagent/releases") and release != old
    expected = args.expected_sha
    assert re.fullmatch("[0-9a-f]{40}", expected)
    actual = subprocess.check_output(
        [
            "git",
            "-c",
            "safe.directory=" + str(release),
            "-C",
            str(release),
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()
    assert release.name == expected and actual == expected, "unexpected release commit"
    assert not subprocess.check_output(
        [
            "git",
            "-c",
            "safe.directory=" + str(release),
            "-C",
            str(release),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ],
        text=True,
    ).strip(), "release has uncommitted files"
    assert (release / "backend/.venv/bin/python").exists() and (
        release / "frontend/dist/index.html"
    ).exists()
    manifest_bytes = (MODELS / "manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == MANIFEST_FILE_SHA256
    manifest = json.loads(manifest_bytes)
    models = [m for v in manifest["variants"].values() for m in v["models"]]
    assert len(models) == 6
    for model in models:
        assert Path(model["file"]).name == model["file"]
        assert (
            hashlib.sha256((MODELS / model["file"]).read_bytes()).hexdigest()
            == model["model_digest"]
        )
    assert Path(CAPTURE).is_dir(), "prepare service-owned capture dir first"
    env_before = ENV.read_bytes()
    env_after = None
    if args.execute:
        assert not sys.stdin.isatty(), "provide credential via private stdin pipe"
        env_after = relay_environment(env_before, sys.stdin.read(4098).rstrip("\n"))
    idle()
    saved = state()
    settings = saved["payload"]["settings"]
    assert len(settings) == 16
    print(
        json.dumps(
            {
                "old": str(old),
                "new": str(release),
                "enabled": saved["enabled"],
                "settings_count": len(settings),
                "preflight": "passed",
            }
        ),
        flush=True,
    )
    if not args.execute:
        return
    assert os.geteuid() == 0
    evidence = Path(tempfile.mkdtemp(prefix="qagent-rollout-relay-", dir="/var/tmp"))

    def save(name, value):
        (evidence / name).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )

    os.chmod(evidence, 0o700)
    (evidence / "qagent.env.before").write_bytes(env_before)
    os.chmod(evidence / "qagent.env.before", 0o600)
    save("scheduler-before.json", saved)
    frozen = ledger()
    save("ledger-before-stop.json", frozen)
    idle()
    saved = state()
    assert saved["payload"]["settings"] == settings
    save("scheduler-before.json", saved)
    switched = False
    restored = False
    env_changed = False
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                "http://127.0.0.1:8000/api/automation/scheduler/stop", method="POST"
            ),
            timeout=10,
        ) as r:
            assert r.status == 200
        idle()
        stopped = state()
        assert not stopped["enabled"]
        assert stopped["payload"]["settings"] == settings
        # A natural cycle can race with the stop request. Preserve its completed
        # result and derive the usual next slot, never rewind that completion.
        before_rt = saved["payload"]["runtime"]
        after_rt = stopped["payload"]["runtime"]
        if after_rt.get("run_count") != before_rt.get("run_count"):
            assert after_rt.get("last_completed_at"), "racing cycle lacks completion"
            repaired = copy.deepcopy(after_rt)
            completed = dt.datetime.fromisoformat(
                repaired["last_completed_at"].replace("Z", "+00:00")
            )
            due = (
                completed + dt.timedelta(seconds=settings["interval_seconds"])
            ).isoformat()
            repaired["next_run_at"] = due
            repaired["cycle_due_at"] = due
            saved["payload"]["runtime"] = repaired
        save("scheduler-resume.json", saved)
        script(old, "disable_linux_runit.sh")
        idle()
        frozen = ledger(check_integrity=True)
        save("ledger-before.json", frozen)
        assert ENV.read_bytes() == env_before, "environment changed concurrently"
        atomic_environment(env_before, env_after)
        env_changed = True
        script(old, "switch_linux_release.sh", str(release))
        switched = True
        script(release, "enable_linux_runit.sh", "--confirm-local-writers-stopped")
        healthy()
        idle()
        assert not state()["enabled"] and state()["payload"]["settings"] == settings
        assert ENV.read_bytes() == env_after
        after = ledger()
        save("ledger-after.json", after)
        assert after == frozen
        restore(saved, settings, frozen)
        restored = True
        save(
            "result.json",
            {
                "release": str(CURRENT.resolve()),
                "ledger_equal": True,
                "settings_equal": True,
                "scheduler_enabled": state()["enabled"],
            },
        )
        print("SUCCESS evidence=" + str(evidence), flush=True)
    except BaseException:
        if restored or RESUME_COMMITTED or state()["enabled"]:
            print(
                "Scheduler enabled; no automatic shutdown/rollback after resume. Evidence="
                + str(evidence),
                flush=True,
            )
            raise
        # Restore application code, never restore or overwrite ledger tables.
        if frozen is not None:
            idle()
            script(CURRENT.resolve(), "disable_linux_runit.sh")
            assert ledger() == frozen, "ledger differs; automatic resume blocked"
            if env_changed or ENV.read_bytes() == env_after:
                assert ENV.read_bytes() == env_after, (
                    "environment changed concurrently; inspect manually"
                )
                atomic_environment(env_after, env_before)
            if switched or CURRENT.resolve() == release:
                script(old, "switch_linux_release.sh", str(old))
            script(old, "enable_linux_runit.sh", "--confirm-local-writers-stopped")
            healthy()
            restore(saved, settings, frozen)
            save(
                "rollback.json",
                {
                    "release": str(CURRENT.resolve()),
                    "settings_equal": True,
                    "ledger_equal": True,
                },
            )
        else:
            # Freeze may fail before obtaining a ledger baseline. Do not kill
            # newly active work or invent a restoration checkpoint.
            print(
                "EARLY FAILURE: inspect saved scheduler snapshot at " + str(evidence),
                flush=True,
            )
        raise


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Never print exception text or traceback: environment may contain secrets.
        print(
            "Relay rollout failed; inspect private evidence and service state.",
            file=sys.stderr,
        )
        raise SystemExit(1)
