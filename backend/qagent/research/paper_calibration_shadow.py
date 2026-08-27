from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

import pandas as pd
from pydantic import BaseModel, Field

from qagent.backtesting.baseline_challenger import (
    BASELINE_CHALLENGER_VERSION,
    MIN_BASELINE_TRAINING_SAMPLES,
    BaselineCandidate,
    BaselineDecision,
    ResolvedBaselineObservation,
    score_baseline_candidates,
)
from qagent.market.benchmarks import CN_BENCHMARKS, benchmark_frames_from_bars
from qagent.storage.paper import (
    PAPER_TRADE_EXECUTED_TERMINAL_STATUSES,
    PaperTradeRecord,
    PaperTradeSourceContext,
)


class PaperCalibrationShadowReport(BaseModel):
    schema_version: str = "paper-calibration-shadow-v1"
    scope: str = "current_model_cohort_closed_trades"
    mode: str = "shadow_only"
    model_version: str = BASELINE_CHALLENGER_VERSION
    cohort_id: str | None
    decision_date: date
    current_market_regime: str
    model_ready: bool
    minimum_training_samples: int = MIN_BASELINE_TRAINING_SAMPLES
    current_cohort_trade_count: int
    eligible_closed_trade_count: int
    excluded_future_trade_count: int
    benchmark_matched_trade_count: int
    benchmark_missing_trade_count: int
    reason: str
    decision: BaselineDecision
    data_health: dict[str, str] = Field(default_factory=dict)


def build_paper_calibration_shadow_report(
    *,
    candidates: list[BaselineCandidate],
    trades: Sequence[PaperTradeRecord],
    cohort_id_by_snapshot: Mapping[str, str | None],
    current_cohort_id: str | None,
    source_context_by_trade: Mapping[str, PaperTradeSourceContext],
    benchmark_bars: pd.DataFrame,
    decision_date: date,
    current_market_regime: str,
) -> PaperCalibrationShadowReport:
    current_trades = [
        trade
        for trade in trades
        if current_cohort_id is not None
        and cohort_id_by_snapshot.get(trade.source_snapshot_id) == current_cohort_id
    ]
    closed = [
        trade
        for trade in current_trades
        if trade.status in PAPER_TRADE_EXECUTED_TERMINAL_STATUSES
        and trade.entry_date is not None
        and trade.exit_date is not None
        and trade.realized_return_pct is not None
    ]
    future = [trade for trade in closed if trade.exit_date >= decision_date]
    eligible = [trade for trade in closed if trade.exit_date < decision_date]
    benchmark_id = CN_BENCHMARKS[0].benchmark_id
    benchmark_frame = benchmark_frames_from_bars(benchmark_bars).get(
        benchmark_id,
        pd.DataFrame(),
    )
    observations: list[ResolvedBaselineObservation] = []
    for trade in eligible:
        benchmark_return = _matched_benchmark_return(
            benchmark_frame,
            trade.entry_date,
            trade.exit_date,
        )
        if benchmark_return is None:
            continue
        context = source_context_by_trade.get(trade.trade_id)
        observations.append(
            ResolvedBaselineObservation(
                instrument_id=trade.instrument_id,
                signal_date=trade.signal_date,
                exit_date=trade.exit_date,
                return_pct=float(trade.realized_return_pct),
                benchmark_return_pct=benchmark_return,
                net_excess_return_pct=(
                    float(trade.realized_return_pct) - benchmark_return
                ),
                primary_strategy_id=trade.strategy_id,
                factor_signals=context.factor_ids if context is not None else [],
                market_regime=(
                    context.market_regime if context is not None else "unknown"
                ),
                industry=context.industry if context is not None else None,
                asset_type=_source_asset_type(context),
                exit_reason=trade.status,
                holding_days=trade.holding_days,
            )
        )
    decision = score_baseline_candidates(
        candidates,
        observations,
        decision_date=decision_date,
    )
    missing = len(eligible) - len(observations)
    if current_cohort_id is None:
        reason = "current_model_cohort_unavailable"
    elif len(observations) < MIN_BASELINE_TRAINING_SAMPLES:
        reason = (
            "training_samples_below_minimum:"
            f"matched={len(observations)},required={MIN_BASELINE_TRAINING_SAMPLES}"
        )
    else:
        reason = "ready"
    return PaperCalibrationShadowReport(
        cohort_id=current_cohort_id,
        decision_date=decision_date,
        current_market_regime=current_market_regime,
        model_ready=decision.model_ready,
        current_cohort_trade_count=len(current_trades),
        eligible_closed_trade_count=len(eligible),
        excluded_future_trade_count=len(future),
        benchmark_matched_trade_count=len(observations),
        benchmark_missing_trade_count=missing,
        reason=reason,
        decision=decision,
        data_health={
            "paper_calibration_shadow_status": (
                "ready" if decision.model_ready else "collecting"
            ),
            "paper_calibration_shadow_mode": "shadow_only",
            "paper_calibration_shadow_paper_write_effect": "none",
            "paper_calibration_shadow_selection_effect": "none",
            "paper_calibration_shadow_order_effect": "none",
            "paper_calibration_shadow_weight_effect": "none",
            "paper_calibration_shadow_training_samples": str(len(observations)),
            "paper_calibration_shadow_excluded_future": str(len(future)),
            "paper_calibration_shadow_benchmark_missing": str(missing),
        },
    )


def _source_asset_type(context: PaperTradeSourceContext | None) -> str:
    if context is None:
        return "unknown"
    value = context.card.get("asset_type")
    text = str(value or "").strip().lower()
    return text or "unknown"


def _matched_benchmark_return(
    frame: pd.DataFrame,
    entry_date: date,
    exit_date: date,
) -> float | None:
    if frame.empty or exit_date < entry_date:
        return None
    normalized = frame.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"]).dt.date
    adjusted = (
        normalized["adjusted_close"]
        if "adjusted_close" in normalized.columns
        else pd.Series(index=normalized.index, dtype="float64")
    )
    close = (
        normalized["close"]
        if "close" in normalized.columns
        else pd.Series(index=normalized.index, dtype="float64")
    )
    prices = normalized.assign(
        reference_price=adjusted.where(adjusted.notna(), close)
    ).set_index("trade_date")["reference_price"]
    entry = prices.get(entry_date)
    exit_ = prices.get(exit_date)
    if (
        entry is None
        or exit_ is None
        or pd.isna(entry)
        or pd.isna(exit_)
        or entry <= 0
    ):
        return None
    return round((float(exit_) / float(entry) - 1) * 100, 4)
