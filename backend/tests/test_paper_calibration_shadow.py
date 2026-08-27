from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from qagent.backtesting.baseline_challenger import BaselineCandidate
from qagent.research.paper_calibration_shadow import (
    build_paper_calibration_shadow_report,
)
from qagent.storage.paper import PaperTradeRecord, PaperTradeSourceContext


DECISION_DATE = date(2026, 8, 20)
COHORT_ID = "current-cohort"


def test_paper_calibration_shadow_is_not_ready_below_minimum_samples():
    trades, contexts, cohorts = _training_records(39)

    report = build_paper_calibration_shadow_report(
        candidates=_candidates(),
        trades=trades,
        cohort_id_by_snapshot=cohorts,
        current_cohort_id=COHORT_ID,
        source_context_by_trade=contexts,
        benchmark_bars=_benchmark_bars(),
        decision_date=DECISION_DATE,
        current_market_regime="mixed",
    )

    assert report.model_ready is False
    assert report.benchmark_matched_trade_count == 39
    assert report.reason == "training_samples_below_minimum:matched=39,required=40"


def test_paper_calibration_shadow_ignores_future_exits_and_does_not_mutate_candidates():
    trades, contexts, cohorts = _training_records(40)
    candidates = _candidates()
    candidate_order = [item.instrument_id for item in candidates]
    baseline = build_paper_calibration_shadow_report(
        candidates=candidates,
        trades=trades,
        cohort_id_by_snapshot=cohorts,
        current_cohort_id=COHORT_ID,
        source_context_by_trade=contexts,
        benchmark_bars=_benchmark_bars(),
        decision_date=DECISION_DATE,
        current_market_regime="mixed",
    )
    future = _trade(
        99,
        strategy_id="negative",
        realized_return_pct=100.0,
        exit_date=DECISION_DATE,
    )
    future_context = _context(future, strategy_id="negative")
    with_future = build_paper_calibration_shadow_report(
        candidates=candidates,
        trades=[*trades, future],
        cohort_id_by_snapshot={**cohorts, future.source_snapshot_id: COHORT_ID},
        current_cohort_id=COHORT_ID,
        source_context_by_trade={**contexts, future.trade_id: future_context},
        benchmark_bars=_benchmark_bars(),
        decision_date=DECISION_DATE,
        current_market_regime="mixed",
    )

    assert with_future.excluded_future_trade_count == 1
    assert with_future.decision == baseline.decision
    assert [item.instrument_id for item in candidates] == candidate_order


def test_paper_calibration_shadow_reranks_positive_and_negative_strategies():
    trades, contexts, cohorts = _training_records(40)

    report = build_paper_calibration_shadow_report(
        candidates=_candidates(),
        trades=trades,
        cohort_id_by_snapshot=cohorts,
        current_cohort_id=COHORT_ID,
        source_context_by_trade=contexts,
        benchmark_bars=_benchmark_bars(),
        decision_date=DECISION_DATE,
        current_market_regime="mixed",
    )
    by_instrument = {
        item.instrument_id: item for item in report.decision.candidates
    }

    assert report.model_ready is True
    assert report.reason == "ready"
    assert by_instrument["CN:POSITIVE"].challenger_position == 1
    assert by_instrument["CN:NEGATIVE"].challenger_position == 2
    assert (
        by_instrument["CN:POSITIVE"].expected_excess_return_pct
        > by_instrument["CN:NEGATIVE"].expected_excess_return_pct
    )
    assert report.data_health["paper_calibration_shadow_selection_effect"] == "none"
    assert report.data_health["paper_calibration_shadow_weight_effect"] == "none"


def _training_records(
    count: int,
) -> tuple[
    list[PaperTradeRecord],
    dict[str, PaperTradeSourceContext],
    dict[str, str | None],
]:
    trades = []
    contexts = {}
    cohorts = {}
    for index in range(count):
        positive = index % 2 == 0
        strategy_id = "positive" if positive else "negative"
        trade = _trade(
            index,
            strategy_id=strategy_id,
            realized_return_pct=5.0 if positive else -5.0,
            exit_date=DECISION_DATE - timedelta(days=2),
        )
        trades.append(trade)
        contexts[trade.trade_id] = _context(trade, strategy_id=strategy_id)
        cohorts[trade.source_snapshot_id] = COHORT_ID
    return trades, contexts, cohorts


def _trade(
    index: int,
    *,
    strategy_id: str,
    realized_return_pct: float,
    exit_date: date,
) -> PaperTradeRecord:
    entry_date = DECISION_DATE - timedelta(days=10)
    return PaperTradeRecord(
        trade_id=f"trade-{index}",
        source_snapshot_id=f"snapshot-{index}",
        provider="free",
        instrument_id=f"CN:{index:06d}",
        strategy_id=strategy_id,
        status="target_1_hit" if realized_return_pct > 0 else "stopped",
        signal_date=entry_date - timedelta(days=1),
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9"),
        target_1=Decimal("11"),
        rank_score=Decimal("0.8"),
        entry_date=entry_date,
        entry_price=Decimal("10"),
        exit_date=exit_date,
        exit_price=Decimal("10.5"),
        latest_date=exit_date,
        latest_price=Decimal("10.5"),
        unrealized_return_pct=None,
        realized_return_pct=realized_return_pct,
        holding_days=8,
        notes="",
    )


def _context(
    trade: PaperTradeRecord,
    *,
    strategy_id: str,
) -> PaperTradeSourceContext:
    positive = strategy_id == "positive"
    return PaperTradeSourceContext(
        source_snapshot_id=trade.source_snapshot_id,
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        signal_date=trade.signal_date,
        industry="机器人" if positive else "银行",
        market_regime="mixed",
        factor_ids=["quality" if positive else "overextended"],
        source_status="frozen",
        card={"asset_type": "stock"},
    )


def _candidates() -> list[BaselineCandidate]:
    return [
        BaselineCandidate(
            instrument_id="CN:NEGATIVE",
            baseline_rank_score=0.9,
            primary_strategy_id="negative",
            factor_signals=["overextended"],
            market_regime="mixed",
            industry="银行",
            asset_type="stock",
        ),
        BaselineCandidate(
            instrument_id="CN:POSITIVE",
            baseline_rank_score=0.8,
            primary_strategy_id="positive",
            factor_signals=["quality"],
            market_regime="mixed",
            industry="机器人",
            asset_type="stock",
        ),
    ]


def _benchmark_bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "instrument_id": "CN:000300.IDX",
                "trade_date": trade_date,
                "close": 100.0,
                "adjusted_close": 100.0,
            }
            for trade_date in (
                DECISION_DATE - timedelta(days=10),
                DECISION_DATE - timedelta(days=2),
            )
        ]
    )
