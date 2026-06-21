from qagent.catalysts.models import CatalystHypothesis, NewsItem


DEMAND_KEYWORDS = ["order", "orders", "backlog", "订单", "需求", "中标", "合同"]
EARNINGS_KEYWORDS = ["earnings", "guidance", "profit", "revenue", "业绩", "利润", "营收", "预告"]
CAPITAL_KEYWORDS = ["buyback", "dividend", "repurchase", "回购", "分红", "增持"]


def build_catalyst_hypotheses(news_items: list[NewsItem]) -> list[CatalystHypothesis]:
    return [_hypothesis_for_item(item) for item in news_items]


def _hypothesis_for_item(item: NewsItem) -> CatalystHypothesis:
    title = item.title
    lowered = title.lower()
    if _contains(lowered, title, DEMAND_KEYWORDS):
        return CatalystHypothesis(
            instrument_id=item.instrument_id,
            news_id=item.news_id,
            title=title,
            catalyst_type="demand",
            investment_hypothesis=(
                "News may indicate incremental demand. Map it to orders, backlog, revenue, "
                "and gross margin before treating it as investable."
            ),
            verification_path="Check follow-up orders, backlog commentary, revenue line items, and margin trend.",
            confidence=0.62,
        )
    if _contains(lowered, title, EARNINGS_KEYWORDS):
        return CatalystHypothesis(
            instrument_id=item.instrument_id,
            news_id=item.news_id,
            title=title,
            catalyst_type="earnings",
            investment_hypothesis=(
                "News may imply earnings revision. Validate whether consensus estimates and "
                "company guidance actually move."
            ),
            verification_path="Check estimate revisions, management guidance, and next-quarter revenue growth.",
            confidence=0.58,
        )
    if _contains(lowered, title, CAPITAL_KEYWORDS):
        return CatalystHypothesis(
            instrument_id=item.instrument_id,
            news_id=item.news_id,
            title=title,
            catalyst_type="capital_return",
            investment_hypothesis=(
                "News may affect shareholder return expectations, but usually needs earnings "
                "support to sustain a rerating."
            ),
            verification_path="Check authorization size, execution pace, cash flow, and valuation reaction.",
            confidence=0.5,
        )
    return CatalystHypothesis(
        instrument_id=item.instrument_id,
        news_id=item.news_id,
        title=title,
        catalyst_type="general",
        investment_hypothesis=(
            "News is relevant context, but the financial transmission path is not obvious yet."
        ),
        verification_path="Identify affected revenue item, timing, margin impact, and whether estimates change.",
        confidence=0.35,
    )


def _contains(lowered: str, original: str, keywords: list[str]) -> bool:
    return any(keyword in lowered or keyword in original for keyword in keywords)
