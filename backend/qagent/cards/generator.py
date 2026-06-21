from decimal import Decimal
from uuid import uuid4

import pandas as pd

from qagent.cards.entry_exit import build_breakout_plan
from qagent.cards.scoring import aggregate_score
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import OpportunityCard, Signal, SignalSnapshot
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
        self.strategy_evaluator = strategy_evaluator or StrategyEvaluator(default_strategy_registry())

    def generate(
        self,
        instrument_id: str,
        signals: list[Signal],
        bars: pd.DataFrame,
        strategy_evaluations: list[StrategyEvaluation] | None = None,
    ) -> OpportunityCard | None:
        if not signals or bars.empty:
            return None

        latest = bars.sort_values("trade_date").iloc[-1]
        close = Decimal(str(round(float(latest["close"]), 2)))
        atr = Decimal(str(round(max(float(close) * 0.04, 0.01), 2)))
        plan = build_breakout_plan(latest_close=close, pivot=close, atr=atr)
        score = aggregate_score(signals)
        evaluations = strategy_evaluations or self.strategy_evaluator.evaluate(
            instrument_id, signals, bars
        )
        primary = _primary_strategy(evaluations)
        strategy_score = round(max([score, *[item.score for item in evaluations]]), 4)
        market = Market.US if instrument_id.startswith("US:") else Market.CN

        return OpportunityCard(
            card_id=f"card_{uuid4().hex[:12]}",
            instrument_id=instrument_id,
            market=market,
            status=OpportunityStatus.SETUP_READY if strategy_score >= 0.5 else OpportunityStatus.WATCH,
            thesis=_thesis(primary),
            score=score,
            entry_plan=plan.entry_plan,
            exit_plan=plan.exit_plan,
            risk_reward=plan.risk_reward,
            scenario=plan.scenario,
            signals=_signal_snapshots(signals),
            strategy_evaluations=evaluations,
            primary_strategy_id=primary.strategy_id if primary else None,
            strategy_score=strategy_score,
            data_caveats=_data_caveats(bars),
        )


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


def _primary_strategy(evaluations: list[StrategyEvaluation]) -> StrategyEvaluation | None:
    active = [
        evaluation
        for evaluation in evaluations
        if evaluation.status in {"passed", "watch"} and evaluation.score > 0
    ]
    if not active:
        return None
    role_rank = {"primary": 3, "risk_control": 2, "confirmation": 1, "valuation": 1, "context": 0}
    return max(active, key=lambda item: (role_rank.get(item.role, 0), item.score))


def _thesis(primary: StrategyEvaluation | None) -> str:
    if primary is None:
        return "Signal stack indicates a watchable setup. Review data caveats before action."
    return (
        f"Primary strategy is {primary.name}: {', '.join(primary.triggers) or 'setup forming'}. "
        "Review entry, invalidation, and missing-data caveats before action."
    )
