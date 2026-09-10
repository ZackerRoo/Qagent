"""Deterministic, read-only observation of persisted paper replay evidence."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from qagent.execution.paper_replay import replay_paper_evidence
from qagent.execution.paper_replay_sqlite import ReadOnlyReplayError, open_read_only_sqlite
from qagent.execution.replay_evidence import (
    PAPER_REPLAY_EVIDENCE_NOTE_PREFIXES,
    stable_replay_digest,
)
from qagent.paper_trading.replay_readiness import build_execution_replay_readiness
from qagent.storage.paper import (
    PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIXES,
    _replay_evidence_audit_records,
)
from qagent.storage.tables import PaperTradeEventRow


def observe_database(path: str | Path) -> dict:
    """Read one snapshot; never initialize tables or instantiate a repository.

    The existing audit parser owns deduplication and malformed/conflicting-note
    handling, keeping this observation consistent with the readiness API.
    """
    connection = open_read_only_sqlite(path)
    try:
        connection.execute("BEGIN")
        prefixes = (*PAPER_REPLAY_EVIDENCE_NOTE_PREFIXES,
                    *PAPER_REPLAY_EVIDENCE_STATUS_NOTE_PREFIXES)
        where = " OR ".join("instr(note, ?) > 0" for _ in prefixes)
        rows = connection.execute(
            "SELECT event_id, trade_id, occurred_at, note FROM paper_trade_events "
            f"WHERE {where} ORDER BY occurred_at, event_id", prefixes,
        ).fetchall()
        sources = [dict(row) for row in rows]
    finally:
        connection.close()

    records = []
    seen: set[str] = set()
    for row in sources:
        event = PaperTradeEventRow(**row)
        event.occurred_at = datetime.fromisoformat(event.occurred_at)
        records.extend(_replay_evidence_audit_records(event, seen_evidence_digests=seen))
    samples = []
    for record in records:
        # Status details can contain arbitrary upstream note text. Publish the
        # classification and digests, not that free-form payload.
        item = record.model_dump(mode="json", exclude={"evidence", "issue_detail"})
        item["input_digest"] = stable_replay_digest(record)
        item["evidence_digest"] = (
            record.evidence.evidence_digest if record.evidence is not None else None
        )
        item["verdict"] = "unknown_or_unreplayable"
        item["report"] = None
        if record.evidence is not None:
            try:
                report = replay_paper_evidence(record.evidence)
                item["report"] = report.model_dump(mode="json")
                item["verdict"] = report.verdict.value
            except (ValueError, AssertionError) as exc:
                item["issue_code"] = f"replay_failed:{type(exc).__name__}"
        item["sample_digest"] = stable_replay_digest(item)
        samples.append(item)
    readiness = build_execution_replay_readiness(
        records, generated_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
    ).model_dump(mode="json", exclude={"generated_at"})
    result = {
        "schema_version": "paper-execution-observation-v1",
        "read_only": {"sqlite_mode": "ro", "query_only": True},
        "automatic_promotion": False,
        "paper_ledger_mutated": False,
        "source_event_count": len(sources),
        "source_high_water": (
            {key: sources[-1][key] for key in ("occurred_at", "event_id")}
            if sources else None
        ),
        "source_digest": stable_replay_digest(sources),
        "sample_count": len(samples),
        "samples": samples,
        "summary": readiness,
    }
    result["observation_digest"] = stable_replay_digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="existing SQLite ledger or snapshot")
    args = parser.parse_args(argv)
    try:
        result = observe_database(args.db)
    except (OSError, ValueError, sqlite3.Error, ReadOnlyReplayError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 2 if result["summary"]["gate"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
