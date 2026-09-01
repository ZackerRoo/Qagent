from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from qagent.execution.models import (
    AShareExecutionRules,
    MarketEvent,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from qagent.execution.paper_replay import (
    PAPER_EXECUTION_FACTS_PREFIX,
    MarketEvidence,
    MarketGranularity,
    PaperReplayFacts,
    PaperReplayLeg,
    PaperReplaySample,
    ReplayVerdict,
    replay_paper_sample,
    summarize_paper_replays,
)
from qagent.execution.paper_replay_sqlite import (
    ReadOnlyReplayError,
    load_replay_samples,
    open_read_only_sqlite,
    run_read_only_replay,
)
from qagent.execution.replay_evidence import (
    PAPER_REPLAY_EVIDENCE_NOTE_PREFIX,
    PaperReplayEvidence,
    PaperReplayExpectedFill,
)


DAY_1 = date(2026, 8, 3)
DAY_2 = date(2026, 8, 4)


def _leg(side: OrderSide, day: date, price: str) -> PaperReplayLeg:
    value = Decimal(price)
    gross = value * 100
    commission = Decimal("5.00")
    stamp = Decimal("0.55") if side == OrderSide.SELL else Decimal("0")
    transfer = Decimal("0.01")
    fees = commission + stamp + transfer
    return PaperReplayLeg(
        market_event_id=f"paper-market:fixture:daily:{day}:{side.value}",
        side=side,
        trade_date=day,
        base_price=value,
        price=value,
        quantity=100,
        gross_amount=gross,
        commission=commission,
        stamp_duty=stamp,
        transfer_fee=transfer,
        slippage=Decimal("0"),
        cash_flow=-(gross + fees) if side == OrderSide.BUY else gross - fees,
    )


def _facts(*, closed: bool = True, exit_price: str = "11.00") -> PaperReplayFacts:
    return PaperReplayFacts(
        allocation=Decimal("2000"),
        rules=AShareExecutionRules(slippage_bps=Decimal("0")),
        entry=_leg(OrderSide.BUY, DAY_1, "10.00"),
        exit=_leg(OrderSide.SELL, DAY_2, exit_price) if closed else None,
    )


def _market(
    day: date,
    price: str,
    provider: str = "free",
    *,
    granularity: MarketGranularity = MarketGranularity.DAILY,
) -> MarketEvidence:
    return MarketEvidence(
        granularity=granularity,
        provider_mode=provider,
        source_provider=provider,
        cached_at="2026-08-05T00:00:00+00:00",
        trade_date=day,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=100_000,
    )


def _sample(*, facts: PaperReplayFacts | None = None) -> PaperReplaySample:
    execution_facts = facts or _facts()
    return PaperReplaySample(
        sample_key="sample-a",
        instrument_id="CN:600000",
        trade_status="time_exit" if execution_facts.exit else "open",
        trigger_price=Decimal("10.00"),
        facts=execution_facts,
        entry_market=(_market(DAY_1, "10.00"),),
        exit_market=(
            (_market(DAY_2, str(execution_facts.exit.base_price)),) if execution_facts.exit else ()
        ),
    )


def _explicit_evidence(
    leg: PaperReplayLeg,
    rules: AShareExecutionRules,
    *,
    phase: str,
) -> PaperReplayEvidence:
    occurred_at = datetime.combine(leg.trade_date, datetime.min.time(), tzinfo=timezone.utc)
    market = MarketEvent(
        event_id=leg.market_event_id,
        instrument_id="CN:600000",
        occurred_at=occurred_at,
        open=leg.base_price,
        high=leg.base_price,
        low=leg.base_price,
        close=leg.base_price,
        volume=100_000,
    )
    order_type = OrderType.MARKET
    order = Order(
        order_id=f"order-{phase}",
        intent_id=f"intent-{phase}",
        account_id="paper",
        instrument_id=market.instrument_id,
        side=leg.side,
        quantity=leg.quantity,
        submitted_at=occurred_at,
        order_type=order_type,
        estimated_price=leg.base_price,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.ACTIVE,
        updated_at=occurred_at,
        rules=rules,
    )
    return PaperReplayEvidence.create(
        phase=phase,
        market=market,
        order=order,
        rules=rules,
        expected_fill=PaperReplayExpectedFill(
            instrument_id=market.instrument_id,
            **leg.model_dump(exclude={"source"}),
        ),
    )


def _create_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            instrument_id TEXT NOT NULL,
            status TEXT NOT NULL,
            trigger_price NUMERIC,
            initial_stop NUMERIC,
            target_1 NUMERIC
        );
        CREATE TABLE paper_trade_events (
            event_id TEXT PRIMARY KEY,
            trade_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            note TEXT NOT NULL
        );
        CREATE TABLE market_bar_cache (
            provider_mode TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            source_provider TEXT NOT NULL,
            open NUMERIC NOT NULL,
            high NUMERIC NOT NULL,
            low NUMERIC NOT NULL,
            close NUMERIC NOT NULL,
            volume NUMERIC NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (provider_mode, instrument_id, trade_date)
        );
        """
    )
    return connection


def _insert_trade(
    connection: sqlite3.Connection,
    trade_id: str,
    facts: PaperReplayFacts,
    *,
    sequence: int = 1,
    event_id: str | None = None,
    note_payload: str | None = None,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO paper_trades VALUES (?, ?, ?, ?, ?, ?)",
        (trade_id, "CN:600000", "time_exit", "10.00", "9.00", "11.00"),
    )
    payload = note_payload or json.dumps(
        facts.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    connection.execute(
        "INSERT INTO paper_trade_events VALUES (?, ?, ?, ?)",
        (
            event_id or f"event-{trade_id}-{sequence}",
            trade_id,
            sequence,
            f"note\n{PAPER_EXECUTION_FACTS_PREFIX}{payload}",
        ),
    )


def _insert_markets(connection: sqlite3.Connection) -> None:
    for day, price in ((DAY_1, "10.00"), (DAY_2, "11.00")):
        connection.execute(
            "INSERT INTO market_bar_cache VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "free",
                "CN:600000",
                day.isoformat(),
                "fixture",
                price,
                price,
                price,
                price,
                100_000,
                "2026-08-05T00:00:00+00:00",
            ),
        )


def _append_evidence(
    connection: sqlite3.Connection,
    event_id: str,
    evidence: PaperReplayEvidence,
) -> None:
    payload = json.dumps(evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    connection.execute(
        "UPDATE paper_trade_events SET note = note || ? WHERE event_id = ?",
        (f"\n{PAPER_REPLAY_EVIDENCE_NOTE_PREFIX}{payload}", event_id),
    )


def test_replays_buy_and_sell_and_marks_cross_date_v1_rule_limit():
    report = replay_paper_sample(_sample())

    assert report.entry.verdict == ReplayVerdict.MATCHED
    assert report.exit is not None
    assert report.exit.verdict == ReplayVerdict.MATCHED
    assert report.verdict == ReplayVerdict.EXPLAINED_DIFFERENCE
    assert "v1_single_rules_snapshot_cross_date" in report.classifications


def test_open_trade_without_exit_is_never_counted_as_matched():
    report = replay_paper_sample(_sample(facts=_facts(closed=False)))

    assert report.entry.verdict == ReplayVerdict.MATCHED
    assert report.verdict == ReplayVerdict.UNREPLAYABLE
    assert "open_trade_exit_facts_absent" in report.classifications


@pytest.mark.parametrize(
    ("entry_market", "code"),
    [
        ((), "entry_market_evidence_missing"),
        (
            (_market(DAY_1, "10.00", "free"), _market(DAY_1, "10.00", "paid")),
            "entry_market_provider_ambiguous",
        ),
    ],
)
def test_missing_or_ambiguous_market_is_unreplayable(entry_market, code):
    sample = _sample().model_copy(update={"entry_market": entry_market})

    report = replay_paper_sample(sample)

    assert report.verdict == ReplayVerdict.UNREPLAYABLE
    assert report.classifications == (code,)


def test_market_base_price_mismatch_is_unexplained_and_unreplayable():
    sample = _sample().model_copy(update={"entry_market": (_market(DAY_1, "10.50"),)})

    report = replay_paper_sample(sample)

    assert report.verdict == ReplayVerdict.UNREPLAYABLE
    assert "execution_difference_unexplained" in report.classifications


def test_minute_fact_with_daily_evidence_fails_before_kernel_replay():
    facts = _facts(closed=False)
    facts = facts.model_copy(
        update={
            "entry": facts.entry.model_copy(
                update={"market_event_id": "paper-market:fixture:minute:2026-08-03T10:01:00:entry"}
            )
        }
    )

    report = replay_paper_sample(_sample(facts=facts))

    assert report.verdict == ReplayVerdict.UNREPLAYABLE
    assert report.entry is not None
    assert report.entry.replay_digest is None
    assert report.classifications == ("entry_market_granularity_insufficient",)


def test_minute_fact_requires_explicit_minute_evidence_for_kernel_replay():
    facts = _facts(closed=False)
    facts = facts.model_copy(
        update={
            "entry": facts.entry.model_copy(
                update={"market_event_id": "paper-market:fixture:minute:2026-08-03T10:01:00:entry"}
            )
        }
    )
    sample = _sample(facts=facts).model_copy(
        update={
            "entry_market": (
                _market(
                    DAY_1,
                    "10.00",
                    granularity=MarketGranularity.MINUTE,
                ),
            )
        }
    )

    report = replay_paper_sample(sample)

    assert report.entry is not None
    assert report.entry.verdict == ReplayVerdict.MATCHED
    assert report.entry.replay_digest is not None
    assert report.verdict == ReplayVerdict.UNREPLAYABLE
    assert report.classifications == ("open_trade_exit_facts_absent",)


def test_minute_exit_fact_with_daily_evidence_fails_before_exit_replay():
    facts = _facts()
    assert facts.exit is not None
    facts = facts.model_copy(
        update={
            "exit": facts.exit.model_copy(
                update={"market_event_id": "paper-market:fixture:minute:2026-08-04T10:01:00:exit"}
            )
        }
    )

    report = replay_paper_sample(_sample(facts=facts))

    assert report.entry is not None
    assert report.entry.verdict == ReplayVerdict.MATCHED
    assert report.exit is not None
    assert report.exit.verdict == ReplayVerdict.UNREPLAYABLE
    assert report.exit.replay_digest is None
    assert "exit_market_granularity_insufficient" in report.classifications


def test_legacy_entry_event_is_unreplayable_even_when_daily_bar_aligns():
    facts = _facts(closed=False)
    facts = facts.model_copy(
        update={
            "entry": facts.entry.model_copy(
                update={"market_event_id": "paper-market:fixture:legacy-entry"}
            )
        }
    )

    report = replay_paper_sample(_sample(facts=facts))

    assert report.verdict == ReplayVerdict.UNREPLAYABLE
    assert report.classifications == ("legacy_inferred_execution_unreplayable",)


def test_granularity_classification_summary_is_stable():
    facts = _facts(closed=False)
    facts = facts.model_copy(
        update={
            "entry": facts.entry.model_copy(
                update={"market_event_id": "paper-market:fixture:minute:stamp:entry"}
            )
        }
    )
    report = replay_paper_sample(_sample(facts=facts))

    first = summarize_paper_replays((report,))
    second = summarize_paper_replays((report,))

    assert first.classification_counts == {"entry_market_granularity_insufficient": 1}
    assert first.batch_digest == second.batch_digest


def test_digest_and_summary_are_deterministic():
    first = replay_paper_sample(_sample())
    second = replay_paper_sample(_sample())

    assert first.replay_digest == second.replay_digest
    assert (
        summarize_paper_replays((first,)).batch_digest
        == summarize_paper_replays((second,)).batch_digest
    )


def test_loader_selects_latest_facts_and_honors_limit(tmp_path: Path):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    _insert_trade(writer, "trade-a", _facts(closed=False), sequence=1)
    _insert_trade(writer, "trade-a", _facts(), sequence=2)
    _insert_trade(writer, "trade-b", _facts(), sequence=1)
    _insert_markets(writer)
    writer.commit()
    writer.close()

    reader = open_read_only_sqlite(path)
    try:
        samples = load_replay_samples(reader, limit=1)
    finally:
        reader.close()

    assert len(samples) == 1
    assert samples[0].facts.exit is not None
    assert samples[0].entry_market[0].granularity == MarketGranularity.DAILY
    assert samples[0].exit_market[0].granularity == MarketGranularity.DAILY


def test_loader_prefers_explicit_minute_evidence_without_daily_cache(tmp_path: Path):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    facts = _facts(closed=False)
    facts = facts.model_copy(
        update={
            "entry": facts.entry.model_copy(
                update={"market_event_id": "paper-market:fixture:minute:2026-08-03T10:01:00:entry"}
            )
        }
    )
    _insert_trade(writer, "trade-a", facts, event_id="explicit-entry")
    _append_evidence(
        writer,
        "explicit-entry",
        _explicit_evidence(facts.entry, facts.rules, phase="entry"),
    )
    writer.commit()
    writer.close()

    reader = open_read_only_sqlite(path)
    try:
        sample = load_replay_samples(reader, limit=1)[0]
    finally:
        reader.close()

    assert sample.entry_replay_evidence is not None
    assert sample.entry_market[0].granularity == MarketGranularity.MINUTE
    report = replay_paper_sample(sample)
    assert report.entry is not None and report.entry.replay_digest is not None
    assert report.entry.verdict == ReplayVerdict.MATCHED


def test_invalid_newest_replay_evidence_does_not_fallback_to_older_snapshot(
    tmp_path: Path,
):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    facts = _facts(closed=False)
    facts = facts.model_copy(
        update={
            "entry": facts.entry.model_copy(
                update={"market_event_id": "paper-market:fixture:minute:2026-08-03T10:01:00:entry"}
            )
        }
    )
    _insert_trade(writer, "trade-a", facts, sequence=1, event_id="older-valid")
    _append_evidence(
        writer,
        "older-valid",
        _explicit_evidence(facts.entry, facts.rules, phase="entry"),
    )
    _insert_trade(writer, "trade-a", facts, sequence=2, event_id="newest-invalid")
    writer.execute(
        "UPDATE paper_trade_events SET note = note || ? WHERE event_id = ?",
        (f"\n{PAPER_REPLAY_EVIDENCE_NOTE_PREFIX}{{broken", "newest-invalid"),
    )
    writer.commit()
    writer.close()

    reader = open_read_only_sqlite(path)
    try:
        sample = load_replay_samples(reader, limit=1)[0]
    finally:
        reader.close()

    assert sample.entry_replay_evidence is None
    assert sample.load_issues == ("newest_replay_evidence_invalid_fail_closed",)
    assert replay_paper_sample(sample).verdict == ReplayVerdict.UNREPLAYABLE


def test_invalid_newest_facts_is_fail_closed_not_replayed_from_old_event(tmp_path: Path):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    _insert_trade(writer, "trade-a", _facts(), sequence=1)
    _insert_trade(writer, "trade-a", _facts(), sequence=2, note_payload="{broken")
    _insert_markets(writer)
    writer.commit()
    writer.close()

    reader = open_read_only_sqlite(path)
    try:
        sample = load_replay_samples(reader, limit=1)[0]
    finally:
        reader.close()

    assert sample.load_issues == ("newest_execution_facts_invalid_fail_closed",)
    assert replay_paper_sample(sample).verdict == ReplayVerdict.UNREPLAYABLE


def test_invalid_only_facts_is_counted_as_unreplayable(tmp_path: Path):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    _insert_trade(writer, "trade-a", _facts(), note_payload="{broken")
    writer.commit()
    writer.close()

    summary = run_read_only_replay(path, limit=1)

    assert summary.sample_count == 1
    assert summary.unreplayable == 1
    assert summary.classification_counts == {"execution_facts_invalid_no_valid_snapshot": 1}


def test_conflicting_latest_events_are_fail_closed(tmp_path: Path):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    _insert_trade(writer, "trade-a", _facts(), sequence=1)
    _insert_trade(writer, "trade-a", _facts(), sequence=2, event_id="conflict-a")
    _insert_trade(
        writer,
        "trade-a",
        _facts(exit_price="12.00"),
        sequence=2,
        event_id="conflict-b",
    )
    _insert_markets(writer)
    writer.commit()
    writer.close()

    reader = open_read_only_sqlite(path)
    try:
        sample = load_replay_samples(reader, limit=1)[0]
    finally:
        reader.close()

    assert sample.load_issues
    assert replay_paper_sample(sample).verdict == ReplayVerdict.UNREPLAYABLE


def test_read_only_connection_rejects_write_and_runner_does_not_mutate(tmp_path: Path):
    path = tmp_path / "fixture.db"
    writer = _create_db(path)
    _insert_trade(writer, "trade-a", _facts())
    _insert_markets(writer)
    writer.commit()
    writer.close()
    before = path.read_bytes()

    reader = open_read_only_sqlite(path)
    try:
        assert reader.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("UPDATE paper_trades SET status = ?", ("changed",))
    finally:
        reader.close()
    summary = run_read_only_replay(path, limit=1)

    assert summary.sample_count == 1
    assert path.read_bytes() == before


def test_rejects_non_sqlite_file(tmp_path: Path):
    path = tmp_path / "not-sqlite.db"
    path.write_text("not sqlite")

    with pytest.raises(ReadOnlyReplayError):
        open_read_only_sqlite(path)


def test_production_replay_modules_have_no_forbidden_imports():
    root = Path(__file__).parents[1] / "qagent" / "execution"
    source = (root / "paper_replay.py").read_text() + (root / "paper_replay_sqlite.py").read_text()

    for forbidden in (
        "qagent.storage.paper",
        "qagent.paper_trading",
        "qagent.db",
        "create_db_engine",
        "PaperTradingRepository",
    ):
        assert forbidden not in source
