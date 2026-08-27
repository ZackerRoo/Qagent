from qagent.catalysts.models import (
    CatalystBeneficiaryLink,
    CatalystFinancialTransmission,
    CatalystHypothesis,
    NewsItem,
)


DEMAND_KEYWORDS = ["order", "orders", "backlog", "订单", "需求", "中标", "合同"]
EARNINGS_KEYWORDS = ["earnings", "guidance", "profit", "revenue", "业绩", "利润", "营收", "预告"]
CAPITAL_KEYWORDS = ["buyback", "dividend", "repurchase", "回购", "分红", "增持"]
POLICY_KEYWORDS = ["policy", "subsidy", "regulation", "政策", "补贴", "监管", "规划"]
SUPPLY_KEYWORDS = ["capacity", "shortage", "price increase", "产能", "短缺", "涨价", "扩产"]
PRODUCT_KEYWORDS = ["launch", "approval", "release", "获批", "发布", "量产", "新品"]


def build_catalyst_hypotheses(news_items: list[NewsItem]) -> list[CatalystHypothesis]:
    return [_hypothesis_for_item(item) for item in news_items]


def _hypothesis_for_item(item: NewsItem) -> CatalystHypothesis:
    title = item.title
    lowered = title.lower()
    if _contains(lowered, title, DEMAND_KEYWORDS):
        return _research_hypothesis(
            item,
            catalyst_type="demand",
            investment_hypothesis=(
                "News may indicate incremental demand. Map it to orders, backlog, revenue, "
                "and gross margin before treating it as investable."
            ),
            verification_path="Check follow-up orders, backlog commentary, revenue line items, and margin trend.",
            confidence=0.62,
            demand_translation=(
                "A customer may be committing spend now; verify order size, delivery schedule, "
                "capacity availability, and whether the named instrument books the revenue."
            ),
            line_item="orders, backlog, and revenue",
            mechanism="signed demand converts into shipments and recognized revenue",
            margin_effect="positive only if pricing and utilization offset delivery costs",
            reporting_lag="next 1-2 quarters",
            evidence=[
                "signed order or backlog disclosure",
                "shipment or revenue conversion",
                "gross-margin trend",
            ],
            risks=[
                "headline may describe a framework agreement",
                "order may be immaterial or low margin",
            ],
            invalidation=[
                "order is cancelled or not reflected in backlog",
                "revenue or margin fails to improve within the stated delivery window",
            ],
        )
    if _contains(lowered, title, EARNINGS_KEYWORDS):
        return _research_hypothesis(
            item,
            catalyst_type="earnings",
            investment_hypothesis=(
                "News may imply earnings revision. Validate whether consensus estimates and "
                "company guidance actually move."
            ),
            verification_path="Check estimate revisions, management guidance, and next-quarter revenue growth.",
            confidence=0.58,
            demand_translation="The event matters only if earnings expectations or reported operating results change.",
            line_item="revenue, profit, and forward guidance",
            mechanism="new operating evidence changes consensus earnings expectations",
            margin_effect="verify whether profit growth comes from sustainable margin or one-offs",
            reporting_lag="current quarter to next 2 quarters",
            evidence=["company filing", "guidance revision", "consensus estimate revision"],
            risks=[
                "one-off gains may dominate reported profit",
                "expectations may already discount the result",
            ],
            invalidation=[
                "guidance or estimates reverse lower",
                "revenue and operating margin do not confirm the headline",
            ],
        )
    if _contains(lowered, title, CAPITAL_KEYWORDS):
        return _research_hypothesis(
            item,
            catalyst_type="capital_return",
            investment_hypothesis=(
                "News may affect shareholder return expectations, but usually needs earnings "
                "support to sustain a rerating."
            ),
            verification_path="Check authorization size, execution pace, cash flow, and valuation reaction.",
            confidence=0.5,
            demand_translation="No end-demand increase is established; the event changes capital allocation and share supply.",
            line_item="cash flow, shares outstanding, and per-share earnings",
            mechanism="executed distributions or repurchases alter shareholder yield and per-share metrics",
            margin_effect="no direct operating-margin effect",
            reporting_lag="current quarter to multi-year",
            evidence=["board authorization", "actual execution filings", "free-cash-flow coverage"],
            risks=[
                "authorization may not be executed",
                "capital return may mask weak reinvestment opportunities",
            ],
            invalidation=[
                "execution remains immaterial",
                "cash flow or balance-sheet capacity deteriorates",
            ],
        )
    if _contains(lowered, title, POLICY_KEYWORDS):
        return _research_hypothesis(
            item,
            catalyst_type="policy",
            investment_hypothesis="Policy may change demand, economics, or market access, but the named instrument's eligibility and exposure are unverified.",
            verification_path="Check the primary policy text, effective date, eligibility rules, implementation budget, and company exposure.",
            confidence=0.48,
            demand_translation="Identify who receives or must spend money, what is purchased, and when implementation becomes mandatory.",
            line_item="eligible segment revenue and compliance cost",
            mechanism="implementation changes addressable demand, pricing, or required spending",
            margin_effect="depends on subsidy pass-through, competition, and compliance cost",
            reporting_lag="next 1-5 quarters",
            evidence=[
                "primary policy document",
                "implementation budget",
                "company eligibility or contract evidence",
            ],
            risks=[
                "implementation may be delayed",
                "benefits may accrue to customers or competitors instead",
            ],
            invalidation=[
                "final rules exclude the named exposure",
                "budget or implementation timetable is withdrawn",
            ],
        )
    if _contains(lowered, title, SUPPLY_KEYWORDS):
        return _research_hypothesis(
            item,
            catalyst_type="supply",
            investment_hypothesis="A supply or capacity change may alter price and utilization, subject to the instrument's actual production exposure.",
            verification_path="Check capacity, utilization, inventory, realized pricing, and competitor supply additions.",
            confidence=0.52,
            demand_translation="Translate the imbalance into units, utilization, inventory days, and realized price rather than attention alone.",
            line_item="volume, average selling price, inventory, and gross margin",
            mechanism="supply-demand imbalance changes realized price and plant utilization",
            margin_effect="positive for constrained suppliers; negative if new capacity creates oversupply",
            reporting_lag="next 1-3 quarters",
            evidence=[
                "capacity and utilization disclosure",
                "inventory and price data",
                "competitor expansion schedule",
            ],
            risks=[
                "announced capacity may arrive before demand",
                "spot prices may not reach contract pricing",
            ],
            invalidation=[
                "inventory rises while prices weaken",
                "utilization or realized pricing fails to confirm the imbalance",
            ],
        )
    if _contains(lowered, title, PRODUCT_KEYWORDS):
        return _research_hypothesis(
            item,
            catalyst_type="product",
            investment_hypothesis="A launch or approval may create revenue, but adoption, pricing, attach rate, and production readiness remain unverified.",
            verification_path="Check shipments, customer adoption, price, attach rate, channel inventory, and segment disclosure.",
            confidence=0.5,
            demand_translation="Identify the buyer, unit volume, selling price, replacement cycle, and whether the launch expands or cannibalizes demand.",
            line_item="product or segment revenue and gross margin",
            mechanism="commercial adoption converts into unit volume, mix, and recurring attach revenue",
            margin_effect="depends on launch cost, mix, yield, and channel incentives",
            reporting_lag="next 1-4 quarters",
            evidence=[
                "shipment or adoption data",
                "segment revenue disclosure",
                "channel inventory and pricing",
            ],
            risks=[
                "launch attention may not convert into paid adoption",
                "new product may cannibalize existing revenue",
            ],
            invalidation=[
                "shipments or active users miss stated milestones",
                "segment revenue and margin fail to reflect adoption",
            ],
        )
    return _research_hypothesis(
        item,
        catalyst_type="general",
        investment_hypothesis=(
            "News is relevant context, but the financial transmission path is not obvious yet."
        ),
        verification_path="Identify affected revenue item, timing, margin impact, and whether estimates change.",
        confidence=0.35,
        demand_translation="The title alone does not establish who spends money, what is purchased, or why spending occurs now.",
        line_item="unidentified",
        mechanism="financial transmission is not established",
        margin_effect="unknown",
        reporting_lag="unknown",
        evidence=[
            "primary filing or policy source",
            "specific revenue exposure",
            "dated operating evidence",
        ],
        risks=["attention may not produce revenue or margin benefit"],
        invalidation=[
            "no material operating or estimate evidence appears after the stated event window"
        ],
    )


def _research_hypothesis(
    item: NewsItem,
    *,
    catalyst_type: str,
    investment_hypothesis: str,
    verification_path: str,
    confidence: float,
    demand_translation: str,
    line_item: str,
    mechanism: str,
    margin_effect: str,
    reporting_lag: str,
    evidence: list[str],
    risks: list[str],
    invalidation: list[str],
) -> CatalystHypothesis:
    return CatalystHypothesis(
        instrument_id=item.instrument_id,
        news_id=item.news_id,
        title=item.title,
        catalyst_type=catalyst_type,
        investment_hypothesis=investment_hypothesis,
        verification_path=verification_path,
        confidence=confidence,
        source=item.source,
        published_at=item.published_at,
        observed_facts=[f"Source headline: {item.title}"],
        inferences=[investment_hypothesis],
        demand_translation=demand_translation,
        beneficiary_chain=[
            CatalystBeneficiaryLink(
                name=item.instrument_id,
                chain_role="named_instrument",
                benefit_order="unverified",
                demand_driver=demand_translation,
                evidence_required="Confirm segment exposure and materiality before treating the instrument as a beneficiary.",
            )
        ],
        financial_transmission=[
            CatalystFinancialTransmission(
                line_item=line_item,
                mechanism=mechanism,
                margin_effect=margin_effect,
                reporting_lag=reporting_lag,
                confidence=round(confidence * 0.8, 4),
            )
        ],
        evidence_to_watch=evidence,
        risks=risks,
        invalidation_triggers=invalidation,
    )


def _contains(lowered: str, original: str, keywords: list[str]) -> bool:
    return any(keyword in lowered or keyword in original for keyword in keywords)
