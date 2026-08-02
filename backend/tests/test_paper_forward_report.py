from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qagent.paper_trading.engine import build_paper_ledger, build_paper_validation
from qagent.research.paper_forward_report import (
    build_paper_forward_comparison,
    build_paper_research_baseline_definition,
)
from qagent.storage.paper import (
    PaperAccountSettings,
    PaperResearchBaseline,
    PaperTradeRecord,
    PaperTradeSourceContext,
    PaperTradingRepository,
)
from qagent.storage.repository import WalkForwardRunRecord

from test_state_repository import make_repo


def _trade(
    trade_id: str,
    signal_date: date,
    *,
    status: str,
    realized_return_pct: float | None,
) -> PaperTradeRecord:
    entered = status != "pending"
    terminal = status in {"target_1_hit", "stopped", "time_exit"}
    return PaperTradeRecord(
        trade_id=trade_id,
        source_snapshot_id=f"snapshot-{trade_id}",
        provider="free",
        instrument_id=f"CN:{trade_id[-6:]}",
        strategy_id="trend_momentum_stage2",
        status=status,
        signal_date=signal_date,
        trigger_price=Decimal("10"),
        initial_stop=Decimal("9.5"),
        target_1=Decimal("11"),
        rank_score=Decimal("0.8"),
        entry_date=signal_date if entered else None,
        entry_price=Decimal("10") if entered else None,
        exit_date=signal_date + timedelta(days=2) if terminal else None,
        exit_price=(
            Decimal(str(10 * (1 + realized_return_pct / 100)))
            if terminal and realized_return_pct is not None
            else None
        ),
        latest_date=signal_date + timedelta(days=2),
        latest_price=Decimal("10"),
        unrealized_return_pct=None,
        realized_return_pct=realized_return_pct,
        holding_days=2 if entered else 0,
        notes="",
    )


def _walk_forward_run() -> WalkForwardRunRecord:
    now = datetime.now(timezone.utc)
    return WalkForwardRunRecord(
        run_id="walk-forward-baseline",
        provider="free",
        status="succeeded",
        start_date=date(2022, 1, 1),
        end_date=date(2025, 12, 31),
        dataset_revision=42,
        rebalance_step_sessions=10,
        lookback_days=400,
        snapshot_count=100,
        top_5_trade_count=80,
        top_10_trade_count=120,
        top_5_return_pct=4.5,
        top_10_return_pct=3.2,
        top_5_oos_trades=30,
        top_10_oos_trades=45,
        top_5_oos_gate="ready",
        top_10_oos_gate="ready",
        reproducibility_digest="v2baseline",
        payload={
            "top_5_metrics": {
                "trade_count": 80,
                "total_return_pct": 4.5,
                "win_rate": 0.55,
                "max_drawdown_pct": -6.0,
                "turnover_pct": 220.0,
                "total_costs": "1800",
            },
            "top_5_temporal_validation": {
                "out_of_sample": {
                    "sample_count": 30,
                    "avg_return_pct": 0.6,
                }
            },
            "benchmarks": [
                {
                    "benchmark_id": "CN:EQUAL_WEIGHT_ELIGIBLE",
                    "top_5_excess_return_pct": 1.2,
                }
            ],
            "cost_sensitivity": [{"key": "stress", "top_5_return_pct": 1.1}],
            "experiment_manifest": {
                "code_revision": "a" * 40,
                "code_dirty": False,
                "selection_algorithm_version": "test-selection",
                "strategy_registry_digest": "b" * 64,
                "ranking_v4_protocol_digest": "c" * 64,
            },
        },
        data_health={},
        created_at=now,
        updated_at=now,
    )


def _account() -> PaperAccountSettings:
    return PaperAccountSettings(
        account_id="default",
        session_id="paper-session-test",
        label="Research",
        status="active",
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        max_positions=5,
        transaction_cost_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
        take_profit_pct=Decimal("50"),
        started_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_research_baseline_is_idempotent_and_rejects_definition_changes(tmp_path):
    repo = make_repo(tmp_path)
    paper_repo = PaperTradingRepository(repo.session_factory)
    definition = {"schema_version": "paper-research-baseline-v1", "value": 1}

    first = paper_repo.freeze_research_baseline(
        baseline_id="paper-research-test",
        provider="free",
        paper_session_id="paper-session-test",
        walk_forward_run_id="walk-forward-test",
        start_date=date(2026, 7, 1),
        definition=definition,
    )
    repeated = paper_repo.freeze_research_baseline(
        baseline_id="paper-research-test",
        provider="free",
        paper_session_id="paper-session-test",
        walk_forward_run_id="walk-forward-test",
        start_date=date(2026, 7, 1),
        definition=definition,
    )

    assert repeated.definition_digest == first.definition_digest
    assert paper_repo.get_research_baseline(
        provider="free",
        paper_session_id="paper-session-test",
    ) == first
    with pytest.raises(ValueError, match="different definition"):
        paper_repo.freeze_research_baseline(
            baseline_id="paper-research-test",
            provider="free",
            paper_session_id="paper-session-test",
            walk_forward_run_id="walk-forward-test",
            start_date=date(2026, 7, 1),
            definition={**definition, "value": 2},
        )


def test_forward_report_builds_historical_comparison_and_checkpoints():
    start = date(2026, 7, 1)
    trades = [
        _trade("trade-000001", start, status="target_1_hit", realized_return_pct=4.0),
        _trade("trade-000002", start + timedelta(days=5), status="stopped", realized_return_pct=-2.0),
        _trade("trade-000003", start + timedelta(days=10), status="open", realized_return_pct=None),
    ]
    account = _account()
    baseline_start, definition = build_paper_research_baseline_definition(
        account=account,
        walk_forward_run=_walk_forward_run(),
        trades=trades,
    )
    baseline = PaperResearchBaseline(
        baseline_id="paper-research-test",
        provider="free",
        paper_session_id=account.session_id,
        walk_forward_run_id="walk-forward-baseline",
        start_date=baseline_start,
        definition_digest="d" * 64,
        definition=definition,
        created_at=datetime.now(timezone.utc),
    )
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
        reporting_scope="legacy",
    )
    validation = build_paper_validation(trades, ledger)
    contexts = {
        trade.trade_id: PaperTradeSourceContext(
            source_snapshot_id=trade.source_snapshot_id,
            created_at=datetime.now(timezone.utc),
            signal_date=trade.signal_date,
            market_regime="risk_on" if index < 2 else "neutral",
            factor_ids=["momentum", "quality"],
            source_status="frozen",
            card={},
        )
        for index, trade in enumerate(trades)
    }
    market_sessions = [start + timedelta(days=index) for index in range(25)]

    report = build_paper_forward_comparison(
        baseline=baseline,
        ledger=ledger,
        validation=validation,
        trades=trades,
        market_sessions=market_sessions,
        source_contexts=contexts,
    )

    assert report.scope == "research_shadow"
    assert report.observed_sessions == 13
    assert report.metrics[0].historical == 80
    assert report.metrics[0].forward == 3
    assert report.checkpoints[0].status == "tracking"
    assert report.forward_factors[0].completed_count == 2
    assert report.market_regimes
