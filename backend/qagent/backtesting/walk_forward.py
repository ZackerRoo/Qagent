from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.backtesting.engine import BacktestSignal
from qagent.backtesting.execution import VersionedAshareExecutionResolver
from qagent.backtesting.portfolio import (
    PortfolioBacktestResult,
    run_signal_portfolio_backtest,
)
from qagent.backtesting.replay_provider import (
    ReplayMarketDataProvider,
    ReplayStrategyDataProvider,
)
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.astock_enhanced import EmptyAShareEnhancedDataProvider
from qagent.market.calendars import trading_sessions_in_range
from qagent.storage.replay_evidence import ReplayEvidenceRepository


EXCLUDED_STATUSES = frozenset(
    {"risk_elevated", "invalidated", "closed", "postmortem_done"}
)


class WalkForwardSelection(BaseModel):
    instrument_id: str
    status: str
    primary_strategy_id: str | None
    rank_score: Decimal
    trigger_price: Decimal | None
    initial_stop: Decimal | None
    target_1: Decimal | None


class WalkForwardSnapshot(BaseModel):
    decision_date: date
    historical_universe_size: int
    eligible_size: int
    suspended_count: int
    st_excluded_count: int
    missing_tradability_count: int
    top_5: list[WalkForwardSelection] = Field(default_factory=list)
    top_10: list[WalkForwardSelection] = Field(default_factory=list)


class WalkForwardSelectionResult(BaseModel):
    owner_run_id: str
    provider_mode: str
    dataset_revision: int
    start_date: date
    end_date: date
    rebalance_step_sessions: int
    snapshots: list[WalkForwardSnapshot]
    top_5_portfolio: PortfolioBacktestResult
    top_10_portfolio: PortfolioBacktestResult
    reproducibility_digest: str
    data_health: dict[str, str] = Field(default_factory=dict)


def run_full_market_walk_forward_selection(
    repository: ReplayEvidenceRepository,
    *,
    owner_run_id: str,
    start: date,
    end: date,
    rebalance_step_sessions: int = 5,
    lookback_days: int = 400,
) -> WalkForwardSelectionResult:
    if start > end:
        raise ValueError("start must be on or before end")
    if rebalance_step_sessions <= 0:
        raise ValueError("rebalance_step_sessions must be positive")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    revision = repository.current_revision()
    if revision <= 0:
        raise ValueError("historical replay dataset is empty")
    owner_repository = ReplayEvidenceRepository(
        repository.session_factory,
        repository.provider_mode,
        owner_run_id=owner_run_id,
    )
    lease = owner_repository.acquire_dataset_lease()
    if lease.revision != revision:
        owner_repository.release_dataset_lease()
        raise RuntimeError("dataset revision changed while acquiring replay lease")
    market_provider = ReplayMarketDataProvider(owner_repository, revision)
    strategy_provider = ReplayStrategyDataProvider(owner_repository, revision)
    snapshots: list[WalkForwardSnapshot] = []
    scan_errors: list[str] = []
    try:
        sessions = trading_sessions_in_range(start, end)[::rebalance_step_sessions]
        for decision_date in sessions:
            owner_repository.renew_dataset_lease()
            members = owner_repository.universe_members_on(decision_date, revision)
            if not members:
                members = owner_repository.materialize_universe(
                    decision_date,
                    revision,
                ).members
            instrument_ids = [item.instrument_id for item in members if item.active]
            tradability = owner_repository.tradability_on(
                instrument_ids,
                decision_date,
                revision,
            )
            eligible = []
            suspended_count = 0
            st_excluded_count = 0
            missing_tradability_count = 0
            for instrument_id in instrument_ids:
                point = tradability.get(instrument_id)
                if point is None:
                    missing_tradability_count += 1
                    continue
                if point.trading_status != "trading":
                    suspended_count += 1
                    continue
                if point.is_st is True:
                    st_excluded_count += 1
                    continue
                eligible.append(instrument_id)
            scan = run_daily_scan(
                eligible,
                market_provider,
                mode="historical_replay",
                strategy_data_provider=strategy_provider,
                a_share_enhanced_provider=EmptyAShareEnhancedDataProvider(),
                start=decision_date - timedelta(days=lookback_days),
                end=decision_date,
            )
            scan_errors.extend(
                item.reason for item in scan.items if item.status == "error"
            )
            selections = [
                _selection(card)
                for card in scan.cards
                if card.status.value not in EXCLUDED_STATUSES
            ]
            snapshots.append(
                WalkForwardSnapshot(
                    decision_date=decision_date,
                    historical_universe_size=len(instrument_ids),
                    eligible_size=len(eligible),
                    suspended_count=suspended_count,
                    st_excluded_count=st_excluded_count,
                    missing_tradability_count=missing_tradability_count,
                    top_5=selections[:5],
                    top_10=selections[:10],
                )
            )
        execution_resolver = VersionedAshareExecutionResolver(
            owner_repository,
            dataset_revision=revision,
        )
        top_5_signals = _signals(snapshots, size=5)
        top_10_signals = _signals(snapshots, size=10)
        top_5_portfolio = run_signal_portfolio_backtest(
            signals=top_5_signals,
            instrument_ids=sorted(
                {item.instrument_id for item in top_5_signals}
            ),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=5,
            execution_rule_resolver=execution_resolver,
        )
        top_10_portfolio = run_signal_portfolio_backtest(
            signals=top_10_signals,
            instrument_ids=sorted(
                {item.instrument_id for item in top_10_signals}
            ),
            provider=market_provider,
            start=start,
            end=end,
            max_positions=10,
            execution_rule_resolver=execution_resolver,
        )
    finally:
        owner_repository.release_dataset_lease()
    digest = _selection_digest(
        snapshots,
        revision,
        top_5_portfolio,
        top_10_portfolio,
    )
    return WalkForwardSelectionResult(
        owner_run_id=owner_run_id,
        provider_mode=repository.provider_mode,
        dataset_revision=revision,
        start_date=start,
        end_date=end,
        rebalance_step_sessions=rebalance_step_sessions,
        snapshots=snapshots,
        top_5_portfolio=top_5_portfolio,
        top_10_portfolio=top_10_portfolio,
        reproducibility_digest=digest,
        data_health={
            "walk_forward_revision": str(revision),
            "walk_forward_snapshots": str(len(snapshots)),
            "walk_forward_scan_errors": str(len(scan_errors)),
            "walk_forward_future_data_guard": "revision_lease_and_decision_date_cutoff",
            "walk_forward_universe": "historical_lifecycle_per_rebalance_date",
            "walk_forward_st_policy": "excluded",
            "walk_forward_top_5_trades": str(
                top_5_portfolio.summary.trade_count
            ),
            "walk_forward_top_10_trades": str(
                top_10_portfolio.summary.trade_count
            ),
            "walk_forward_digest": digest,
            **(
                {"walk_forward_error_samples": " | ".join(scan_errors[:3])}
                if scan_errors
                else {}
            ),
        },
    )


def _selection(card) -> WalkForwardSelection:
    return WalkForwardSelection(
        instrument_id=card.instrument_id,
        status=card.status.value,
        primary_strategy_id=card.primary_strategy_id,
        rank_score=Decimal(str(card.rank_score)),
        trigger_price=card.entry_plan.trigger_price,
        initial_stop=card.exit_plan.initial_stop,
        target_1=card.exit_plan.target_1,
    )


def _signals(
    snapshots: list[WalkForwardSnapshot], *, size: int
) -> list[BacktestSignal]:
    result = []
    for snapshot in snapshots:
        selections = snapshot.top_5 if size == 5 else snapshot.top_10
        result.extend(
            BacktestSignal(
                snapshot_id=(
                    f"walk-forward-{size}-{snapshot.decision_date:%Y%m%d}:"
                    f"{item.instrument_id}"
                ),
                instrument_id=item.instrument_id,
                signal_date=snapshot.decision_date,
                primary_strategy_id=item.primary_strategy_id,
                status=item.status,
                rank_score=item.rank_score,
                trigger_price=item.trigger_price,
                initial_stop=item.initial_stop,
                target_1=item.target_1,
                outcome_status="pending",
            )
            for item in selections
        )
    return result


def _selection_digest(
    snapshots: list[WalkForwardSnapshot],
    revision: int,
    top_5_portfolio: PortfolioBacktestResult,
    top_10_portfolio: PortfolioBacktestResult,
) -> str:
    payload = {
        "dataset_revision": revision,
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "top_5_portfolio": top_5_portfolio.model_dump(mode="json"),
        "top_10_portfolio": top_10_portfolio.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
