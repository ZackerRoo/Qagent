from decimal import Decimal
from uuid import uuid4

import pandas as pd

from qagent.cards.entry_exit import build_breakout_plan, build_pead_plan, build_pullback_plan
from qagent.cards.ranking import rank_opportunity
from qagent.cards.scoring import calculate_signal_consensus
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import OpportunityCard, Signal, SignalConsensus, SignalSnapshot
from qagent.market.indicators import wilder_atr
from qagent.market.instruments import format_instrument_label
from qagent.recommendations.decision import build_research_decision
from qagent.recommendations.enrichment import enrich_opportunity_card
from qagent.strategies.evaluator import StrategyEvaluator
from qagent.strategies.models import StrategyEvaluation
from qagent.strategies.registry import default_strategy_registry


def _data_caveats(bars: pd.DataFrame) -> list[str]:
    if "provider" not in bars.columns:
        return ["provider: unknown"]
    providers = sorted({str(provider) for provider in bars["provider"].dropna().unique()})
    if not providers:
        return ["provider: unknown"]
    if providers == ["fixture"]:
        return ["fixture data"]
    return [f"provider: {provider}" for provider in providers]


class OpportunityCardGenerator:
    def __init__(self, strategy_evaluator: StrategyEvaluator | None = None):
        self.strategy_evaluator = strategy_evaluator or StrategyEvaluator(
            default_strategy_registry()
        )

    def generate(
        self,
        instrument_id: str,
        signals: list[Signal],
        bars: pd.DataFrame,
        strategy_evaluations: list[StrategyEvaluation] | None = None,
        *,
        volatility_bars: pd.DataFrame | None = None,
    ) -> OpportunityCard | None:
        if not signals or bars.empty:
            return None

        ordered_bars = bars.sort_values("trade_date")
        latest = ordered_bars.iloc[-1]
        close = Decimal(str(round(float(latest["close"]), 2)))
        atr_source = volatility_bars if volatility_bars is not None else ordered_bars
        atr, atr_fallback = _latest_atr(atr_source, close)
        consensus = calculate_signal_consensus(signals)
        score = consensus.net_score
        evaluations = strategy_evaluations or self.strategy_evaluator.evaluate(
            instrument_id, signals, bars
        )
        primary = _primary_strategy(evaluations)
        plan = _trade_plan(primary, close, atr)
        evaluated_score = max([score, *[item.score for item in evaluations]])
        if consensus.bullish_support <= consensus.bearish_pressure:
            strategy_score = score
        else:
            strategy_score = round(
                max(
                    score,
                    evaluated_score * (1 - consensus.conflict) * (1 - consensus.bearish_pressure),
                ),
                4,
            )
        rank = rank_opportunity(primary, evaluations, strategy_score, plan.risk_reward)
        market = Market.US if instrument_id.startswith("US:") else Market.CN

        card = OpportunityCard(
            card_id=f"card_{uuid4().hex[:12]}",
            instrument_id=instrument_id,
            instrument_label=format_instrument_label(instrument_id),
            market=market,
            status=OpportunityStatus.SETUP_READY
            if strategy_score >= 0.5
            else OpportunityStatus.WATCH,
            thesis=_thesis(primary),
            score=score,
            entry_plan=plan.entry_plan,
            exit_plan=plan.exit_plan,
            risk_reward=plan.risk_reward,
            scenario=plan.scenario,
            signals=_signal_snapshots(signals),
            signal_consensus=SignalConsensus(
                bullish_support=consensus.bullish_support,
                bearish_pressure=consensus.bearish_pressure,
                neutral_weight=consensus.neutral_weight,
                agreement=consensus.agreement,
                conflict=consensus.conflict,
                net_score=consensus.net_score,
                dominant_direction=consensus.dominant_direction,
                signal_count=consensus.signal_count,
            ),
            strategy_evaluations=evaluations,
            primary_strategy_id=primary.strategy_id if primary else None,
            strategy_score=strategy_score,
            rank_score=rank.rank_score,
            rank_reasons=rank.rank_reasons,
            data_caveats=[
                *_data_caveats(bars),
                *(["atr: fallback_4pct_insufficient_history"] if atr_fallback else []),
            ],
        )
        card.decision = build_research_decision(card)
        enrich_opportunity_card(card)
        return card


def _signal_snapshots(signals: list[Signal]) -> list[SignalSnapshot]:
    return [
        SignalSnapshot(
            signal_type=signal.signal_type,
            direction=signal.direction,
            horizon=signal.horizon,
            score=signal.score,
            evidence=signal.evidence,
        )
        for signal in signals
    ]


def _latest_atr(bars: pd.DataFrame, close: Decimal) -> tuple[Decimal, bool]:
    ordered = bars.sort_values("trade_date")
    atr_series = wilder_atr(ordered, period=14)
    latest_atr = atr_series.iloc[-1] if not atr_series.empty else None
    if latest_atr is None or pd.isna(latest_atr) or float(latest_atr) <= 0:
        latest_atr = max(float(close) * 0.04, 0.01)
        return Decimal(str(round(float(latest_atr), 4))), True
    volatility_close = float(ordered.iloc[-1]["close"])
    if volatility_close <= 0 or pd.isna(volatility_close):
        fallback = max(float(close) * 0.04, 0.01)
        return Decimal(str(round(fallback, 4))), True
    price_atr = float(close) * float(latest_atr) / volatility_close
    return Decimal(str(round(max(price_atr, 0.0001), 4))), False


def _primary_strategy(evaluations: list[StrategyEvaluation]) -> StrategyEvaluation | None:
    active = [
        evaluation
        for evaluation in evaluations
        if evaluation.status in {"passed", "watch"} and evaluation.score > 0
    ]
    if not active:
        return None
    role_rank = {"primary": 3, "risk_control": 2, "confirmation": 1, "valuation": 1, "context": 0}
    family_rank = {"earnings_momentum": 3, "event_catalyst": 2, "technical_breakout": 1}
    return max(
        active,
        key=lambda item: (
            role_rank.get(item.role, 0),
            family_rank.get(item.family, 0),
            item.score,
        ),
    )


def _thesis(primary: StrategyEvaluation | None) -> str:
    if primary is None:
        return "Signal stack indicates a watchable setup. Review data caveats before action."
    return (
        f"Primary strategy is {primary.name}: {', '.join(primary.triggers) or 'setup forming'}. "
        "Review entry, invalidation, and missing-data caveats before action."
    )


def _trade_plan(primary: StrategyEvaluation | None, close: Decimal, atr: Decimal):
    if primary and primary.strategy_id == "pead_earnings_drift":
        low = _decimal_evidence(primary, "earnings_day_low", close - atr)
        high = _decimal_evidence(primary, "earnings_day_high", close)
        return build_pead_plan(
            latest_close=close,
            earnings_day_low=low,
            earnings_day_high=high,
            atr=atr,
        )
    if primary and primary.strategy_id == "healthy_pullback":
        support = _decimal_evidence(primary, "ma_20", close - atr)
        return build_pullback_plan(latest_close=close, support=support, atr=atr)
    return build_breakout_plan(latest_close=close, pivot=close, atr=atr)


def _decimal_evidence(
    evaluation: StrategyEvaluation,
    key: str,
    default: Decimal,
) -> Decimal:
    value = evaluation.evidence.get(key)
    if value is None:
        return default
    return Decimal(str(round(float(value), 2)))
