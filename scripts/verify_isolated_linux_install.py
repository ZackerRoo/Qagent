#!/usr/bin/env python3
"""Exercise Qagent startup against an explicit disposable database only."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if os.environ.get("QAGENT_DATABASE_URL"):
        raise RuntimeError("refusing inherited QAGENT_DATABASE_URL during isolated verification")
    with tempfile.TemporaryDirectory(prefix="qagent-install-check-") as temp_dir:
        root = Path(temp_dir)
        database = root / "install-check.db"
        os.environ["QAGENT_DATABASE_URL"] = f"sqlite:///{database}"
        os.environ["QAGENT_DATA_DIR"] = str(root)

        from fastapi.testclient import TestClient
        from qagent.app import create_app

        with TestClient(create_app()) as client:
            response = client.get("/api/automation/scheduler")
            response.raise_for_status()
            state = response.json()
            if state.get("enabled") is not False:
                raise RuntimeError(f"disposable scheduler unexpectedly enabled: {state!r}")

        with sqlite3.connect(database) as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            scheduler = db.execute(
                "SELECT enabled FROM automation_scheduler_state LIMIT 1"
            ).fetchone()
        if result != ("ok",):
            raise RuntimeError(f"disposable database quick_check failed: {result!r}")
        if scheduler is not None and scheduler[0]:
            raise RuntimeError("disposable scheduler was persisted as enabled")
        print(f"isolated startup passed using disposable database: {database}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
