from __future__ import annotations

import argparse
import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from qagent.execution.events import canonical_digest
from qagent.execution.paper_replay import (
    PAPER_EXECUTION_FACTS_PREFIX,
    MarketEvidence,
    MarketGranularity,
    PaperReplaySample,
    parse_execution_facts_payload,
    replay_paper_sample,
    summarize_paper_replays,
)
from qagent.execution.replay_evidence import (
    PAPER_REPLAY_EVIDENCE_NOTE_PREFIXES,
    PAPER_REPLAY_EVIDENCE_SCHEMA_VERSION_V2,
    PaperReplayEvidence,
)


REQUIRED_TABLES = frozenset({"paper_trades", "paper_trade_events", "market_bar_cache"})


class ReadOnlyReplayError(RuntimeError):
    pass


def open_read_only_sqlite(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ReadOnlyReplayError("database path is not a regular file")
    with resolved.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ReadOnlyReplayError("database file does not have a SQLite header")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        value = connection.execute("PRAGMA query_only").fetchone()[0]
        if value != 1:
            raise ReadOnlyReplayError("SQLite query_only could not be enforced")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
            )
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ReadOnlyReplayError(
                f"database is missing required tables: {', '.join(sorted(missing))}"
            )
        return connection
    except Exception:
        connection.close()
        raise


def load_replay_samples(
    connection: sqlite3.Connection, *, limit: int = 30
) -> tuple[PaperReplaySample, ...]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    rows = connection.execute(
        """
        WITH selected_trades AS (
            SELECT DISTINCT event.trade_id
            FROM paper_trade_events AS event
            WHERE instr(event.note, ?) > 0
            ORDER BY event.trade_id
            LIMIT ?
        )
        SELECT event.event_id, event.trade_id, event.sequence, event.note,
               trade.instrument_id, trade.status, trade.trigger_price,
               trade.initial_stop, trade.target_1
        FROM selected_trades AS selected
        JOIN paper_trade_events AS event ON event.trade_id = selected.trade_id
        JOIN paper_trades AS trade ON trade.trade_id = selected.trade_id
        WHERE instr(event.note, ?) > 0
        ORDER BY event.trade_id, event.sequence DESC, event.event_id DESC
        """,
        (PAPER_EXECUTION_FACTS_PREFIX, limit, PAPER_EXECUTION_FACTS_PREFIX),
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["trade_id"])].append(row)

    provider_modes = _provider_modes(connection)
    samples: list[PaperReplaySample] = []
    for trade_id in sorted(grouped):
        selected = _select_latest_facts(grouped[trade_id])
        row, facts, load_issues = selected
        evidences, evidence_issues = _select_latest_replay_evidence(grouped[trade_id])
        load_issues = load_issues + evidence_issues
        entry_replay_evidence = evidences.get("entry")
        exit_replay_evidence = evidences.get("exit")
        entry_market = (
            (_explicit_market_evidence(entry_replay_evidence),)
            if entry_replay_evidence is not None
            else _load_market_evidence(
                connection, str(row["instrument_id"]), facts.entry.trade_date, provider_modes
            )
            if facts is not None
            else ()
        )
        exit_market = (
            (_explicit_market_evidence(exit_replay_evidence),)
            if exit_replay_evidence is not None
            else _load_market_evidence(
                connection, str(row["instrument_id"]), facts.exit.trade_date, provider_modes
            )
            if facts is not None and facts.exit is not None
            else ()
        )
        samples.append(
            PaperReplaySample(
                sample_key=hashlib.sha256(str(trade_id).encode()).hexdigest()[:16],
                instrument_id=str(row["instrument_id"]),
                trade_status=str(row["status"]),
                trigger_price=row["trigger_price"],
                initial_stop=row["initial_stop"],
                target_1=row["target_1"],
                facts=facts,
                entry_market=entry_market,
                exit_market=exit_market,
                entry_replay_evidence=entry_replay_evidence,
                exit_replay_evidence=exit_replay_evidence,
                load_issues=load_issues,
            )
        )
    return tuple(samples)


def _select_latest_facts(rows):
    if not rows:
        raise ValueError("facts selection requires at least one event")
    sequences = sorted({int(row["sequence"]) for row in rows}, reverse=True)
    newest_invalid = False
    for sequence in sequences:
        same_sequence = [row for row in rows if int(row["sequence"]) == sequence]
        parsed = []
        for row in same_sequence:
            payloads = _facts_payloads(str(row["note"]))
            if len(payloads) != 1:
                parsed = []
                break
            try:
                facts = parse_execution_facts_payload(payloads[0])
            except (ValueError, TypeError):
                parsed = []
                break
            parsed.append((row, facts))
        conflict = len({canonical_digest(facts) for _, facts in parsed}) > 1
        if parsed and not conflict:
            issues = ("newest_execution_facts_invalid_fail_closed",) if newest_invalid else ()
            return parsed[0][0], parsed[0][1], issues
        newest_invalid = True
    return rows[0], None, ("execution_facts_invalid_no_valid_snapshot",)


def _facts_payloads(note: str) -> tuple[str, ...]:
    return tuple(
        line[len(PAPER_EXECUTION_FACTS_PREFIX) :]
        for line in note.splitlines()
        if line.startswith(PAPER_EXECUTION_FACTS_PREFIX)
    )


def _select_latest_replay_evidence(
    rows: list[sqlite3.Row],
) -> tuple[dict[str, PaperReplayEvidence], tuple[str, ...]]:
    selected: dict[str, PaperReplayEvidence] = {}
    sequences = sorted({int(row["sequence"]) for row in rows}, reverse=True)
    for sequence in sequences:
        payloads = tuple(
            versioned_payload
            for row in rows
            if int(row["sequence"]) == sequence
            for versioned_payload in _replay_evidence_payloads(str(row["note"]))
        )
        if not payloads:
            continue
        parsed: list[PaperReplayEvidence] = []
        try:
            for version, payload in payloads:
                evidence = PaperReplayEvidence.model_validate_json(payload)
                parsed_version = (
                    "v2"
                    if evidence.schema_version == PAPER_REPLAY_EVIDENCE_SCHEMA_VERSION_V2
                    else "v1"
                )
                if parsed_version != version:
                    raise ValueError("note prefix and schema version disagree")
                parsed.append(evidence)
        except (ValueError, TypeError):
            return {}, ("newest_replay_evidence_invalid_fail_closed",)
        for phase in ("entry", "exit"):
            if phase in selected:
                continue
            candidates = [item for item in parsed if item.phase == phase]
            if not candidates:
                continue
            if len({item.evidence_digest for item in candidates}) != 1:
                return {}, (f"{phase}_replay_evidence_conflict_fail_closed",)
            selected[phase] = candidates[0]
        if len(selected) == 2:
            break
    return selected, ()


def _replay_evidence_payloads(note: str) -> tuple[tuple[str, str], ...]:
    payloads: list[tuple[str, str]] = []
    for line in note.splitlines():
        for prefix in PAPER_REPLAY_EVIDENCE_NOTE_PREFIXES:
            if line.startswith(prefix):
                payloads.append(("v2" if ":v2]" in prefix else "v1", line[len(prefix) :]))
                break
    return tuple(payloads)


def _explicit_market_evidence(evidence: PaperReplayEvidence) -> MarketEvidence:
    market = evidence.market
    return MarketEvidence(
        granularity=(
            MarketGranularity.MINUTE if ":minute:" in market.event_id else MarketGranularity.DAILY
        ),
        provider_mode="paper_replay_evidence",
        source_provider="unified_execution",
        cached_at=market.occurred_at.isoformat(),
        trade_date=market.trading_date,
        open=market.open,
        high=market.high,
        low=market.low,
        close=market.close,
        volume=market.volume,
    )


def _load_market_evidence(
    connection: sqlite3.Connection,
    instrument_id: str,
    trade_date,
    provider_modes: tuple[str, ...],
) -> tuple[MarketEvidence, ...]:
    if not provider_modes:
        return ()
    placeholders = ", ".join("?" for _ in provider_modes)
    rows = connection.execute(
        f"""
        SELECT provider_mode, source_provider, cached_at, trade_date,
               open, high, low, close, volume
        FROM market_bar_cache
        WHERE provider_mode IN ({placeholders})
          AND instrument_id = ? AND trade_date = ?
        ORDER BY provider_mode, source_provider, cached_at
        """,
        (*provider_modes, instrument_id, trade_date.isoformat()),
    ).fetchall()
    return tuple(
        MarketEvidence(
            granularity=MarketGranularity.DAILY,
            provider_mode=str(row["provider_mode"]),
            source_provider=str(row["source_provider"]),
            cached_at=str(row["cached_at"]),
            trade_date=row["trade_date"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=int(row["volume"]),
        )
        for row in rows
    )


def _provider_modes(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Enumerate leading primary-key values using bounded index seeks."""

    modes: list[str] = []
    after = ""
    while True:
        row = connection.execute(
            """
            SELECT provider_mode
            FROM market_bar_cache
            WHERE provider_mode > ?
            ORDER BY provider_mode
            LIMIT 1
            """,
            (after,),
        ).fetchone()
        if row is None:
            return tuple(modes)
        after = str(row["provider_mode"])
        modes.append(after)


def run_read_only_replay(path: str | Path, *, limit: int = 30):
    connection = open_read_only_sqlite(path)
    try:
        samples = load_replay_samples(connection, limit=limit)
        reports = tuple(replay_paper_sample(sample) for sample in samples)
        return summarize_paper_replays(reports)
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only offline paper execution replay")
    parser.add_argument("--db", required=True, help="existing SQLite ledger path")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(argv)
    summary = run_read_only_replay(args.db, limit=args.limit)
    print("read_only=mode=ro,query_only=1")
    print(f"samples={summary.sample_count}")
    print(f"matched={summary.matched}")
    print(f"explained={summary.explained_difference}")
    print(f"unreplayable={summary.unreplayable}")
    print(f"classifications={summary.classification_counts}")
    print(f"batch_digest={summary.batch_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
