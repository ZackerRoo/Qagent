from datetime import datetime, timezone

from qagent.api import routes
from qagent.market.etf_exposure import (
    EtfConstituent,
    EtfExposureOverlap,
    EtfExposureProfile,
    EtfIndustryExposure,
    EtfSharedConstituent,
)
from qagent.monitoring.lookthrough import (
    PortfolioLookThroughHolding,
    build_portfolio_lookthrough_risk,
)


def _profile(
    instrument_id: str,
    name: str,
    *,
    tracking_index: str = "中证A50指数",
    holdings: list[EtfConstituent] | None = None,
    industries: list[EtfIndustryExposure] | None = None,
    status: str = "complete",
) -> EtfExposureProfile:
    return EtfExposureProfile(
        instrument_id=instrument_id,
        symbol=instrument_id.split(":")[-1],
        fund_name=name,
        tracking_index=tracking_index,
        exposure_group="宽基ETF:中证A50",
        exposure_category="broad",
        market_scope="A股宽基",
        style_exposure="大盘核心",
        holdings=holdings or [],
        holdings_coverage_pct=sum(item.weight_pct for item in holdings or []),
        holdings_scope="latest_quarterly_top10",
        industries=industries or [],
        source_url="https://example.test/fund",
        fetched_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        data_status=status,
    )


def test_portfolio_lookthrough_aggregates_stock_and_etf_exposure():
    holdings = [
        PortfolioLookThroughHolding(
            trade_id="stock-trade",
            instrument_id="CN:300308",
            instrument_label="中际旭创 300308.SZ",
            asset_type="stock",
            weight_pct=10.0,
            exposure_group="制造业",
        ),
        PortfolioLookThroughHolding(
            trade_id="etf-trade",
            instrument_id="CN:563080",
            instrument_label="中证A50ETF易方达 563080.SH",
            asset_type="etf",
            weight_pct=20.0,
            exposure_group="宽基ETF:中证A50",
        ),
    ]
    profile = _profile(
        "CN:563080",
        "中证A50ETF易方达",
        holdings=[
            EtfConstituent(
                instrument_id="CN:300308",
                symbol="300308",
                name="中际旭创",
                weight_pct=10.0,
            ),
            EtfConstituent(
                instrument_id="CN:300750",
                symbol="300750",
                name="宁德时代",
                weight_pct=8.0,
            ),
        ],
        industries=[
            EtfIndustryExposure(name="制造业", weight_pct=60.0),
            EtfIndustryExposure(name="金融业", weight_pct=20.0),
        ],
    )

    result = build_portfolio_lookthrough_risk(holdings, [profile], [])

    assert result.summary.position_count == 2
    assert result.summary.invested_weight_pct == 30.0
    assert result.summary.etf_weight_pct == 20.0
    assert result.summary.industry_known_weight_pct == 26.0
    assert result.summary.constituent_known_weight_pct == 13.6
    assert result.industries[0].label == "制造业"
    assert result.industries[0].weight_pct == 22.0
    assert result.industries[1].label == "ETF未披露行业"
    assert result.industries[1].weight_pct == 4.0
    underlying = next(
        item for item in result.underlying_exposures if item.instrument_id == "CN:300308"
    )
    assert underlying.direct_weight_pct == 10.0
    assert underlying.etf_weight_pct == 2.0
    assert underlying.known_weight_pct == 12.0
    assert {warning.kind for warning in result.warnings} == {
        "industry_concentration",
        "direct_etf_overlap",
    }


def test_portfolio_lookthrough_reports_same_index_and_weighted_pair_overlap():
    holdings = [
        PortfolioLookThroughHolding(
            trade_id="left",
            instrument_id="CN:563080",
            instrument_label="A50ETF易方达",
            asset_type="etf",
            weight_pct=15.0,
        ),
        PortfolioLookThroughHolding(
            trade_id="right",
            instrument_id="CN:159595",
            instrument_label="A50ETF大成",
            asset_type="etf",
            weight_pct=10.0,
        ),
    ]
    shared = EtfConstituent(
        instrument_id="CN:300308",
        symbol="300308",
        name="中际旭创",
        weight_pct=10.0,
    )
    profiles = [
        _profile("CN:563080", "A50ETF易方达", holdings=[shared]),
        _profile(
            "CN:159595",
            "A50ETF大成",
            holdings=[shared.model_copy(update={"weight_pct": 8.0})],
        ),
    ]
    overlaps = [
        EtfExposureOverlap(
            left_instrument_id="CN:563080",
            right_instrument_id="CN:159595",
            same_tracking_index=True,
            disclosed_overlap_lower_bound_pct=8.0,
            shared_constituents=[
                EtfSharedConstituent(
                    instrument_id="CN:300308",
                    name="中际旭创",
                    minimum_weight_pct=8.0,
                )
            ],
            status="measured",
        )
    ]

    result = build_portfolio_lookthrough_risk(holdings, profiles, overlaps)

    assert result.indices[0].label == "中证A50指数"
    assert result.indices[0].weight_pct == 25.0
    assert result.indices[0].source_count == 2
    assert result.etf_overlaps[0].portfolio_overlap_lower_bound_pct == 0.8
    assert "same_tracking_index" in {warning.kind for warning in result.warnings}
    assert "etf_constituent_overlap" not in {warning.kind for warning in result.warnings}


def test_portfolio_lookthrough_keeps_unavailable_etf_exposure_unknown():
    holding = PortfolioLookThroughHolding(
        trade_id="cross-border",
        instrument_id="CN:513100",
        instrument_label="纳指ETF",
        asset_type="etf",
        weight_pct=9.5,
    )
    unavailable = _profile(
        "CN:513100",
        "纳指ETF",
        tracking_index="纳斯达克100指数",
        status="unavailable",
    )

    result = build_portfolio_lookthrough_risk([holding], [unavailable], [])

    assert result.summary.status == "partial"
    assert result.summary.unavailable_etf_weight_pct == 9.5
    assert result.industries[0].label == "ETF未披露行业"
    assert result.warnings[0].kind == "missing_etf_disclosure"


def test_point_in_time_industries_do_not_merge_unrelated_stocks_as_composite():
    industries = {
        "CN:002612": "C18纺织服装、服饰业",
        "CN:600216": "C27医药制造业",
        "CN:600368": "G54道路运输业",
        "CN:601628": "J68保险业",
    }
    holdings = [
        PortfolioLookThroughHolding(
            trade_id=f"trade-{instrument_id}",
            instrument_id=instrument_id,
            instrument_label=instrument_id,
            asset_type="stock",
            weight_pct=10.0,
            exposure_group=routes._paper_card_exposure_group(
                {"market_context": {"industry": "综合"}},
                current_industry="综合",
                instrument_id=instrument_id,
                point_in_time_industry=industry,
            ),
        )
        for instrument_id, industry in industries.items()
    ]

    result = build_portfolio_lookthrough_risk(holdings, [], [])

    assert {item.label for item in result.industries} == set(industries.values())
    assert "综合" not in {item.label for item in result.industries}
    assert "industry_concentration" not in {warning.kind for warning in result.warnings}


def test_genuine_same_industry_still_warns_and_unknown_stays_explicit():
    same_industry = "C27医药制造业"
    holdings = [
        PortfolioLookThroughHolding(
            trade_id="medicine-a",
            instrument_id="CN:600216",
            instrument_label="医药A",
            asset_type="stock",
            weight_pct=12.0,
            exposure_group=same_industry,
        ),
        PortfolioLookThroughHolding(
            trade_id="medicine-b",
            instrument_id="CN:600267",
            instrument_label="医药B",
            asset_type="stock",
            weight_pct=10.0,
            exposure_group=same_industry,
        ),
        PortfolioLookThroughHolding(
            trade_id="unknown",
            instrument_id="CN:999999",
            instrument_label="未知公司",
            asset_type="stock",
            weight_pct=5.0,
            exposure_group=routes._paper_card_exposure_group(
                {"market_context": {"industry": "综合"}},
                current_industry="综合",
                instrument_id="CN:999999",
            ),
        ),
    ]

    result = build_portfolio_lookthrough_risk(holdings, [], [])

    warning = next(item for item in result.warnings if item.kind == "industry_concentration")
    assert warning.label == same_industry
    assert warning.weight_pct == 22.0
    unknown = next(item for item in result.industries if item.label == "未知个股行业")
    assert unknown.weight_pct == 5.0
