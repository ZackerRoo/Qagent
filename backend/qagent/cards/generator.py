from decimal import Decimal
from uuid import uuid4

import pandas as pd

from qagent.cards.entry_exit import build_breakout_plan
from qagent.cards.scoring import aggregate_score
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import OpportunityCard, Signal, SignalSnapshot


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
    def generate(
        self, instrument_id: str, signals: list[Signal], bars: pd.DataFrame
    ) -> OpportunityCard | None:
        if not signals or bars.empty:
            return None

        latest = bars.sort_values("trade_date").iloc[-1]
        close = Decimal(str(round(float(latest["close"]), 2)))
        atr = Decimal(str(round(max(float(close) * 0.04, 0.01), 2)))
        plan = build_breakout_plan(latest_close=close, pivot=close, atr=atr)
        score = aggregate_score(signals)
        market = Market.US if instrument_id.startswith("US:") else Market.CN

        return OpportunityCard(
            card_id=f"card_{uuid4().hex[:12]}",
            instrument_id=instrument_id,
            market=market,
            status=OpportunityStatus.SETUP_READY if score >= 0.5 else OpportunityStatus.WATCH,
            thesis="Signal stack indicates a watchable setup. Review data caveats before action.",
            score=score,
            entry_plan=plan.entry_plan,
            exit_plan=plan.exit_plan,
            risk_reward=plan.risk_reward,
            scenario=plan.scenario,
            signals=_signal_snapshots(signals),
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
