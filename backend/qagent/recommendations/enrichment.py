from qagent.domain.models import OpportunityCard
from qagent.market.cn_context import build_market_context
from qagent.recommendations.cn_execution import build_trading_constraints
from qagent.recommendations.rotation import classify_opportunity
from qagent.recommendations.summary import build_recommendation_summary


def enrich_opportunity_card(card: OpportunityCard) -> OpportunityCard:
    if card.market.value == "CN":
        card.trading_constraints = build_trading_constraints(
            card.instrument_id,
            card.instrument_label,
        )
        card.market_context = build_market_context(
            card.instrument_id,
            card.instrument_label,
        )
    classify_opportunity(card)
    card.recommendation_summary = build_recommendation_summary(card)
    return card
