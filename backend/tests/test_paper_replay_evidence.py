import hashlib
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from qagent.execution.models import (
    AShareExecutionRules,
    MarketEvent,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from qagent.execution.replay_evidence import (
    PaperReplayEvidence,
    PaperReplayExpectedFill,
)
from qagent.execution.paper_replay import (
    PaperReplayFacts,
    PaperReplaySample,
    ReplayVerdict,
    replay_paper_sample,
)
from qagent.storage.paper import (
    PaperExecutionFacts,
    PaperExecutionLegFacts,
    PaperTradeEventMetadata,
    PaperTradingRepository,
    encode_paper_execution_facts,
    encode_paper_replay_evidence,
    parse_paper_execution_facts,
    parse_paper_replay_evidence,
    parse_paper_replay_evidences,
)

from test_state_repository import make_repo


OCCURRED_AT = datetime(2026, 8, 3, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))


def _facts() -> PaperExecutionFacts:
    return PaperExecutionFacts(
        allocation=Decimal("2000"),
        rules=AShareExecutionRules(slippage_bps=Decimal("0")),
        entry=PaperExecutionLegFacts(
            market_event_id="paper-market:fixture:minute:2026-08-03T10:01:00:entry",
            side=OrderSide.BUY,
            trade_date=date(2026, 8, 3),
            base_price=Decimal("10.00"),
            price=Decimal("10.00"),
            quantity=100,
            gross_amount=Decimal("1000.00"),
            commission=Decimal("5.00"),
            transfer_fee=Decimal("0.01"),
            cash_flow=Decimal("-1005.01"),
        ),
    )


def _evidence(
    *,
    phase: str = "entry",
    price: str = "10.00",
    occurred_at: datetime = OCCURRED_AT,
) -> PaperReplayEvidence:
    side = OrderSide.BUY if phase == "entry" else OrderSide.SELL
    rules = AShareExecutionRules(slippage_bps=Decimal("0"))
    market = MarketEvent(
        event_id=f"paper-market:fixture:minute:{occurred_at.isoformat()}:{phase}",
        instrument_id="CN:600000",
        occurred_at=occurred_at,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=100_000,
    )
    order = Order(
        order_id=f"order-{phase}",
        intent_id=f"intent-{phase}",
        account_id="paper",
        instrument_id=market.instrument_id,
        side=side,
        quantity=100,
        submitted_at=occurred_at,
        order_type=OrderType.MARKET,
        estimated_price=Decimal(price),
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.ACTIVE,
        updated_at=occurred_at,
        rules=rules,
    )
    gross = Decimal(price) * 100
    commission = Decimal("5.00")
    stamp_duty = (
        (gross * Decimal("0.0005")).quantize(Decimal("0.01"))
        if side == OrderSide.SELL
        else Decimal("0")
    )
    transfer_fee = Decimal("0.01")
    fees = commission + stamp_duty + transfer_fee
    return PaperReplayEvidence.create(
        phase=phase,
        market=market,
        order=order,
        rules=rules,
        expected_fill=PaperReplayExpectedFill(
            market_event_id=market.event_id,
            instrument_id=market.instrument_id,
            side=side,
            trade_date=market.trading_date,
            base_price=Decimal(price),
            price=Decimal(price),
            quantity=100,
            gross_amount=gross,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            cash_flow=-(gross + fees) if side == OrderSide.BUY else gross - fees,
        ),
    )


def test_v1_execution_facts_bytes_and_parser_are_unchanged_by_replay_line():
    facts = _facts()
    original = encode_paper_execution_facts("fixture note", facts)

    assert hashlib.sha256(original.encode()).hexdigest() == (
        "a375b1597e13f5a86fbd47c4d917122a6ccc643d5ed2f5b19b449aafe839712e"
    )
    combined = encode_paper_replay_evidence(original, _evidence())
    assert parse_paper_execution_facts(combined) == facts
    assert combined.startswith(original + "\n[paper_replay_evidence:v1]")


def test_replay_evidence_roundtrip_digest_and_privacy_snapshot_are_stable():
    evidence = _evidence()
    note = encode_paper_replay_evidence("audit", evidence)

    assert parse_paper_replay_evidence(note) == evidence
    assert _evidence().evidence_digest == evidence.evidence_digest
    assert "account_id" not in note
    assert "order_id" not in note
    assert "intent_id" not in note


def test_replay_evidence_corruption_and_same_phase_conflict_fail_closed():
    evidence = _evidence()
    encoded = encode_paper_replay_evidence("", evidence)
    corrupted = encoded.replace(evidence.evidence_digest, "0" * 64)
    conflict = encode_paper_replay_evidence(encoded, _evidence(price="10.01"))

    assert parse_paper_replay_evidence(corrupted) is None
    assert parse_paper_replay_evidences(corrupted) == ()
    assert parse_paper_replay_evidence(conflict) is None
    assert parse_paper_replay_evidences(conflict) == ()


def test_entry_and_exit_evidence_can_share_one_append_only_event_note():
    entry = _evidence(phase="entry")
    exit_evidence = _evidence(phase="exit")
    note = encode_paper_replay_evidence("", entry)
    note = encode_paper_replay_evidence(note, exit_evidence)

    assert parse_paper_replay_evidences(note) == (entry, exit_evidence)
    assert parse_paper_replay_evidence(note) is None


def test_event_preserves_successful_evidence_and_failed_leg_status(tmp_path):
    paper_repo = PaperTradingRepository(make_repo(tmp_path).session_factory)
    trade = paper_repo.create_trade(
        source_snapshot_id="mixed-replay-audit",
        provider="fixture",
        instrument_id="CN:600000",
        strategy_id="audit",
        signal_date=date(2026, 8, 2),
        trigger_price=Decimal("10.00"),
        initial_stop=Decimal("9.00"),
        target_1=Decimal("11.00"),
        rank_score=Decimal("0.80"),
    )
    evidence = _evidence(phase="entry")

    updated = paper_repo.update_trade(
        trade.trade_id,
        status="open",
        entry_date=date(2026, 8, 3),
        entry_price=Decimal("10.00"),
        event_metadata=PaperTradeEventMetadata(
            source="unified_execution",
            replay_evidence=evidence,
            replay_evidence_error="ValueError:evidence_build_failed",
        ),
    )

    event = paper_repo.list_trade_events(trade.trade_id)[-1]
    assert updated is not None and updated.status == "open"
    assert event.replay_evidence == (evidence,)
    assert parse_paper_replay_evidence(event.note) == evidence
    assert "[paper_replay_evidence:v1]" in event.note
    assert "[paper_replay_evidence_status:v1]" in event.note
    assert "build_failed_trade_continued" in event.note


def test_explicit_minute_evidence_replays_with_independent_cross_date_rules():
    entry = _evidence(phase="entry", price="10.00")
    exit_evidence = _evidence(
        phase="exit",
        price="11.00",
        occurred_at=datetime(2026, 8, 4, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    facts = PaperReplayFacts(
        allocation=Decimal("2000"),
        rules=entry.rules.model_copy(update={"rules_version": "v1-entry-only-legacy"}),
        entry=entry.expected_fill.model_dump(exclude={"instrument_id"}),
        exit=exit_evidence.expected_fill.model_dump(exclude={"instrument_id"}),
    )
    sample = PaperReplaySample(
        sample_key="explicit-minute",
        instrument_id="CN:600000",
        trade_status="time_exit",
        facts=facts,
        entry_replay_evidence=entry,
        exit_replay_evidence=exit_evidence,
    )

    report = replay_paper_sample(sample)

    assert report.entry.verdict == ReplayVerdict.MATCHED
    assert report.exit is not None and report.exit.verdict == ReplayVerdict.MATCHED
    assert report.verdict == ReplayVerdict.MATCHED
    assert "v1_single_rules_snapshot_cross_date" not in report.classifications
