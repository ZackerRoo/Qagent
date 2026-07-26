from datetime import date
from decimal import Decimal

import pandas as pd

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.execution import HistoricalExecutionRule
from qagent.backtesting.portfolio import (
    CandidateOutcomeStatus,
    resolve_candidate_outcome_ledger,
)
from qagent.historical_evidence.models import HistoricalFeeRule


class FrameProvider:
    name = "candidate-ledger-fixture"

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars

    def get_daily_bars(self, instrument_ids, start, end):
        return self.bars.loc[
            self.bars["instrument_id"].isin(instrument_ids)
            & (self.bars["trade_date"] >= start)
            & (self.bars["trade_date"] <= end)
        ].copy()


class StaticExecutionResolver:
    def __init__(self, rule: HistoricalExecutionRule):
        self.rule = rule

    def resolve(self, instrument_id, trade_date, *, is_st=False):
        return self.rule.model_copy(
            update={"instrument_id": instrument_id, "trade_date": trade_date}
        )


def _signal(snapshot_id: str, instrument_id: str, *, trigger: str = "10"):
    return BacktestSignal(
        snapshot_id=snapshot_id,
        instrument_id=instrument_id,
        signal_date=date(2025, 1, 1),
        primary_strategy_id="trend_momentum_stage2",
        status="setup_ready",
        rank_score=Decimal("0.9"),
        trigger_price=Decimal(trigger),
        initial_stop=Decimal("9"),
        target_1=Decimal("11"),
        outcome_status="pending",
    )


def _resolved_bars(*instrument_ids: str) -> pd.DataFrame:
    rows = []
    for instrument_id in instrument_ids:
        rows.extend(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2025, 1, 1),
                    "open": 9.8,
                    "high": 9.9,
                    "low": 9.7,
                    "close": 9.8,
                    "volume": 1_000_000,
                },
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2025, 1, 2),
                    "open": 10,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1_000_000,
                },
                {
                    "instrument_id": instrument_id,
                    "trade_date": date(2025, 1, 3),
                    "open": 11,
                    "high": 11.2,
                    "low": 10.8,
                    "close": 11,
                    "volume": 1_000_000,
                },
            ]
        )
    return pd.DataFrame(rows)


def _fee(side: str) -> HistoricalFeeRule:
    return HistoricalFeeRule(
        fee_schedule_version="a-share-fees-v1",
        fee_rule_key="cn-stock",
        effective_from=date(2023, 8, 28),
        effective_to=date(2025, 12, 31),
        side=side,
        security_type="stock",
        exchange="ALL",
        commission_bps="3",
        minimum_commission="5",
        stamp_duty_bps="5" if side == "sell" else "0",
        transfer_fee_bps="0.1",
    )


def _execution_rule() -> HistoricalExecutionRule:
    return HistoricalExecutionRule(
        instrument_id="CN:000001",
        trade_date=date(2025, 1, 2),
        limit_pct="10",
        minimum_order_quantity=100,
        quantity_step=100,
        settlement_days=1,
        ipo_no_limit_sessions=0,
        buy_fee=_fee("buy"),
        sell_fee=_fee("sell"),
        rule_set_version="a-share-rules-v1",
        fee_schedule_version="a-share-fees-v1",
    )


def test_same_day_signals_resolve_without_competing_for_capital_or_positions():
    signals = [
        _signal("same-day-a", "CN:000001"),
        _signal("same-day-b", "CN:000002"),
    ]

    result = resolve_candidate_outcome_ledger(
        signals=signals,
        provider=FrameProvider(_resolved_bars("CN:000001", "CN:000002")),
        start=date(2025, 1, 1),
        end=date(2025, 1, 3),
        nominal_amount=Decimal("10000"),
        transaction_cost_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
    )

    assert [outcome.status for outcome in result.outcomes] == [
        CandidateOutcomeStatus.RESOLVED,
        CandidateOutcomeStatus.RESOLVED,
    ]
    assert all(outcome.shares == Decimal("1000") for outcome in result.outcomes)
    assert result.status_counts["resolved"] == 2
    assert result.data_health["independent_resolution"] == "no_capital_or_position_capacity"


def test_incomplete_future_and_untriggered_signals_have_explicit_statuses():
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:000003",
                "trade_date": trade_date,
                "open": 10,
                "high": 10.5,
                "low": 9.5,
                "close": 10,
                "volume": 1_000_000,
            }
            for trade_date in (date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3))
        ]
        + [
            {
                "instrument_id": "CN:000004",
                "trade_date": date(2025, 1, 1),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 1_000_000,
            }
        ]
    )

    result = resolve_candidate_outcome_ledger(
        signals=[
            _signal("not-triggered", "CN:000003", trigger="20").model_copy(
                update={
                    "initial_stop": Decimal("19"),
                    "target_1": Decimal("22"),
                }
            ),
            _signal("future-missing", "CN:000004"),
        ],
        provider=FrameProvider(bars),
        start=date(2025, 1, 1),
        end=date(2025, 1, 3),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
    )
    by_snapshot = {outcome.snapshot_id: outcome for outcome in result.outcomes}

    assert (
        by_snapshot["not-triggered"].status
        == CandidateOutcomeStatus.NOT_TRIGGERED_OR_UNFILLABLE
    )
    assert by_snapshot["not-triggered"].status_detail == (
        "entry_not_triggered_or_fill_blocked"
    )
    assert (
        by_snapshot["future-missing"].status
        == CandidateOutcomeStatus.INSUFFICIENT_FUTURE_DATA
    )
    assert by_snapshot["future-missing"].status_detail == "no_sessions_after_signal"


def test_resolved_outcome_uses_fixed_notional_and_actual_round_trip_fees():
    result = resolve_candidate_outcome_ledger(
        signals=[_signal("costed", "CN:000001")],
        provider=FrameProvider(_resolved_bars("CN:000001")),
        start=date(2025, 1, 1),
        end=date(2025, 1, 3),
        nominal_amount=Decimal("10000"),
        transaction_cost_bps=Decimal("99"),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
        execution_rule_resolver=StaticExecutionResolver(_execution_rule()),
    )

    outcome = result.outcomes[0]
    assert outcome.status == CandidateOutcomeStatus.RESOLVED
    assert outcome.entry_price == Decimal("10.00")
    assert outcome.exit_price == Decimal("11.00")
    assert outcome.exit_reason == "target_1_hit"
    assert outcome.shares == Decimal("1000")
    assert outcome.entry_value == Decimal("10000.00")
    assert outcome.exit_value == Decimal("11000.00")
    assert outcome.gross_pnl == Decimal("1000.00")
    assert outcome.entry_costs == Decimal("5.10")
    assert outcome.exit_costs == Decimal("10.61")
    assert outcome.costs == Decimal("15.71")
    assert outcome.net_pnl == Decimal("984.29")
    assert outcome.return_pct == 9.8429


def test_candidate_ledger_caps_fixed_notional_by_historical_liquidity():
    bars = _resolved_bars("CN:000001")
    bars.loc[:, "volume"] = 5_000

    result = resolve_candidate_outcome_ledger(
        signals=[_signal("liquidity-capped", "CN:000001")],
        provider=FrameProvider(bars),
        start=date(2025, 1, 1),
        end=date(2025, 1, 3),
        nominal_amount=Decimal("100000"),
        transaction_cost_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        max_entry_wait_days=2,
        max_holding_days=3,
        execution_rule_resolver=StaticExecutionResolver(_execution_rule()),
    )

    outcome = result.outcomes[0]
    assert outcome.status == CandidateOutcomeStatus.RESOLVED
    assert outcome.shares == Decimal("5000")
    assert outcome.entry_value == Decimal("50000.00")


def test_candidate_ledger_is_deterministic_for_input_order_and_repeated_runs():
    signals = [
        _signal("deterministic-b", "CN:000002"),
        _signal("deterministic-a", "CN:000001"),
    ]
    provider = FrameProvider(_resolved_bars("CN:000001", "CN:000002"))
    kwargs = {
        "provider": provider,
        "start": date(2025, 1, 1),
        "end": date(2025, 1, 3),
        "nominal_amount": Decimal("10000"),
        "transaction_cost_bps": Decimal("5"),
        "slippage_bps": Decimal("0"),
        "max_entry_wait_days": 2,
        "max_holding_days": 3,
    }

    first = resolve_candidate_outcome_ledger(signals=signals, **kwargs)
    second = resolve_candidate_outcome_ledger(signals=list(reversed(signals)), **kwargs)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert [outcome.snapshot_id for outcome in first.outcomes] == [
        "deterministic-a",
        "deterministic-b",
    ]
