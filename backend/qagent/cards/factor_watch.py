from decimal import Decimal
from uuid import uuid4

import pandas as pd

from qagent.cards.entry_exit import build_breakout_plan
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import OpportunityCard
from qagent.factors.models import FactorRanking
from qagent.market.instruments import format_instrument_label
from qagent.recommendations.decision import build_research_decision
from qagent.recommendations.enrichment import enrich_opportunity_card
from qagent.recommendations.rotation import classify_opportunity
from qagent.strategies.models import StrategyEvaluation


FACTOR_WATCH_STRATEGY_ID = "factor_rotation_watch"


def build_factor_watch_card(
    instrument_id: str,
    bars: pd.DataFrame,
    ranking: FactorRanking,
) -> OpportunityCard | None:
    if bars.empty:
        return None

    latest = bars.sort_values("trade_date").iloc[-1]
    close = Decimal(str(round(float(latest["close"]), 2)))
    atr = Decimal(str(round(max(float(close) * 0.04, 0.01), 2)))
    plan = build_breakout_plan(latest_close=close, pivot=close, atr=atr)
    strategy_score = round(max(0.45, ranking.factor_score * 0.78), 4)
    rank_score = round(max(strategy_score, min(0.98, 0.42 + ranking.factor_score * 0.45)), 4)
    evaluation = _strategy_evaluation(ranking, strategy_score)
    market = Market.US if instrument_id.startswith("US:") else Market.CN

    card = OpportunityCard(
        card_id=f"card_{uuid4().hex[:12]}",
        instrument_id=instrument_id,
        instrument_label=ranking.instrument_label or format_instrument_label(instrument_id),
        market=market,
        status=OpportunityStatus.WATCH,
        thesis=(
            "因子排名靠前，适合作为观察候选；只有价格触发、成交确认和风险约束同时满足后，"
            "才进入买入计划。"
        ),
        score=strategy_score,
        entry_plan=plan.entry_plan,
        exit_plan=plan.exit_plan,
        risk_reward=plan.risk_reward,
        scenario=plan.scenario,
        strategy_evaluations=[evaluation],
        primary_strategy_id=FACTOR_WATCH_STRATEGY_ID,
        strategy_score=strategy_score,
        rank_score=rank_score,
        rank_reasons=[
            f"因子排名第 {ranking.factor_rank}",
            "价格因子进入观察池，但还不是确认买点",
        ],
        factor_score=ranking.factor_score,
        factor_rank=ranking.factor_rank,
        factor_percentile=ranking.percentile,
        factor_flags=ranking.flags,
        factor_exposures=ranking.factor_exposures,
        data_caveats=_data_caveats(bars),
    )
    card.decision = build_research_decision(card)
    enrich_opportunity_card(card)
    classify_opportunity(card)
    return card


def build_factor_watch_cards(
    factor_rankings: list[FactorRanking],
    bars_by_instrument: dict[str, pd.DataFrame],
    existing_instrument_ids: set[str],
    max_cards: int = 8,
    min_factor_score: float = 0.5,
) -> list[OpportunityCard]:
    cards: list[OpportunityCard] = []
    seen = set(existing_instrument_ids)
    for ranking in factor_rankings:
        if len(cards) >= max_cards:
            break
        if ranking.instrument_id in seen:
            continue
        if ranking.factor_score < min_factor_score and ranking.factor_rank > 20:
            continue
        if "low_liquidity" in ranking.flags:
            continue
        card = build_factor_watch_card(
            ranking.instrument_id,
            bars_by_instrument.get(ranking.instrument_id, pd.DataFrame()),
            ranking,
        )
        if card is None:
            continue
        cards.append(card)
        seen.add(ranking.instrument_id)
    return cards


def _strategy_evaluation(ranking: FactorRanking, strategy_score: float) -> StrategyEvaluation:
    return StrategyEvaluation(
        strategy_id=FACTOR_WATCH_STRATEGY_ID,
        name="Factor rotation watch",
        family="factor_rotation",
        role="context",
        status="watch",
        score=strategy_score,
        horizon="5-20d",
        preconditions=["daily_ohlcv", "cross_sectional_factor_rank"],
        triggers=["top_factor_rank", "trend_or_pullback_confirmation_pending"],
        confirmations=["volume_confirmation", "entry_trigger", "risk_reward_check"],
        invalidation="因子排名跌出观察区、价格跌破止损，或板块强度明显转弱。",
        evidence={
            "factor_rank": ranking.factor_rank,
            "factor_score": ranking.factor_score,
            "percentile": ranking.percentile,
            "flags": ranking.flags,
        },
        score_components={
            "factor_score": ranking.factor_score,
            "trend_quality": ranking.trend_quality_score,
            "liquidity": ranking.liquidity_score,
            "low_risk": ranking.low_risk_score,
        },
        missing_data=[],
        data_requirements=["daily_ohlcv", "cross_sectional_factor_rank"],
    )


def _data_caveats(bars: pd.DataFrame) -> list[str]:
    if "provider" not in bars.columns:
        return ["provider: unknown"]
    providers = sorted({str(provider) for provider in bars["provider"].dropna().unique()})
    if not providers:
        return ["provider: unknown"]
    if providers == ["fixture"]:
        return ["fixture data"]
    return [f"provider: {provider}" for provider in providers]
