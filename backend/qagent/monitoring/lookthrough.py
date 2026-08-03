from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from qagent.market.etf_exposure import EtfExposureOverlap, EtfExposureProfile


INDUSTRY_CONCENTRATION_WARNING_PCT = 20.0
UNDERLYING_CONCENTRATION_WARNING_PCT = 15.0
ETF_PAIR_OVERLAP_WARNING_PCT = 0.5


class PortfolioLookThroughHolding(BaseModel):
    trade_id: str
    instrument_id: str
    instrument_label: str
    asset_type: str
    weight_pct: float
    exposure_group: str | None = None


class PortfolioLookThroughBucket(BaseModel):
    key: str
    label: str
    weight_pct: float
    source_count: int
    instrument_ids: list[str] = Field(default_factory=list)


class PortfolioUnderlyingExposure(BaseModel):
    instrument_id: str
    name: str
    known_weight_pct: float
    direct_weight_pct: float
    etf_weight_pct: float
    source_count: int
    source_instrument_ids: list[str] = Field(default_factory=list)


class PortfolioEtfOverlap(BaseModel):
    left_instrument_id: str
    right_instrument_id: str
    same_tracking_index: bool
    portfolio_overlap_lower_bound_pct: float | None = None
    shared_constituents: list[str] = Field(default_factory=list)


class PortfolioLookThroughWarning(BaseModel):
    kind: str
    severity: Literal["info", "watch"]
    label: str
    weight_pct: float | None = None
    instrument_ids: list[str] = Field(default_factory=list)
    related_names: list[str] = Field(default_factory=list)


class PortfolioLookThroughSummary(BaseModel):
    position_count: int
    stock_position_count: int
    etf_position_count: int
    invested_weight_pct: float
    etf_weight_pct: float
    industry_known_weight_pct: float
    constituent_known_weight_pct: float
    unavailable_etf_weight_pct: float
    warning_count: int
    status: str


class PortfolioLookThroughRisk(BaseModel):
    summary: PortfolioLookThroughSummary
    industries: list[PortfolioLookThroughBucket]
    indices: list[PortfolioLookThroughBucket]
    markets: list[PortfolioLookThroughBucket]
    styles: list[PortfolioLookThroughBucket]
    underlying_exposures: list[PortfolioUnderlyingExposure]
    etf_overlaps: list[PortfolioEtfOverlap]
    warnings: list[PortfolioLookThroughWarning]
    data_health: dict[str, str] = Field(default_factory=dict)


def build_portfolio_lookthrough_risk(
    holdings: list[PortfolioLookThroughHolding],
    profiles: list[EtfExposureProfile],
    overlaps: list[EtfExposureOverlap],
) -> PortfolioLookThroughRisk:
    profile_by_id = {profile.instrument_id: profile for profile in profiles}
    holding_by_id = {holding.instrument_id: holding for holding in holdings}
    industries: dict[str, dict[str, object]] = {}
    indices: dict[str, dict[str, object]] = {}
    markets: dict[str, dict[str, object]] = {}
    styles: dict[str, dict[str, object]] = {}
    underlyings: dict[str, dict[str, object]] = {}
    unavailable_etf_weight = 0.0
    industry_known_weight = 0.0
    stock_count = 0
    etf_count = 0

    for holding in holdings:
        weight = max(float(holding.weight_pct), 0.0)
        if holding.asset_type.lower() != "etf":
            stock_count += 1
            industry = holding.exposure_group or "未知个股行业"
            _add_bucket(industries, industry, industry, weight, holding.instrument_id)
            if holding.exposure_group:
                industry_known_weight += weight
            market = _stock_market_label(holding.instrument_id)
            _add_bucket(markets, market, market, weight, holding.instrument_id)
            _add_underlying(
                underlyings,
                instrument_id=holding.instrument_id,
                name=holding.instrument_label,
                direct_weight=weight,
                etf_weight=0.0,
                source_instrument_id=holding.instrument_id,
            )
            continue

        etf_count += 1
        profile = profile_by_id.get(holding.instrument_id)
        if profile is None or profile.data_status == "unavailable":
            unavailable_etf_weight += weight
            _add_bucket(
                industries,
                "__unknown_etf_industry__",
                "ETF未披露行业",
                weight,
                holding.instrument_id,
            )
            _add_bucket(
                indices,
                "__unknown_etf_index__",
                "ETF跟踪指数未提供",
                weight,
                holding.instrument_id,
            )
            _add_bucket(
                markets,
                "__unknown_etf_market__",
                "ETF市场范围未提供",
                weight,
                holding.instrument_id,
            )
            _add_bucket(
                styles,
                "__unknown_etf_style__",
                "ETF风格未提供",
                weight,
                holding.instrument_id,
            )
            continue

        index_label = profile.tracking_index or profile.exposure_group or "ETF跟踪指数未提供"
        _add_bucket(indices, index_label, index_label, weight, holding.instrument_id)
        _add_bucket(markets, profile.market_scope, profile.market_scope, weight, holding.instrument_id)
        style_label = profile.style_exposure or "ETF风格未提供"
        _add_bucket(styles, style_label, style_label, weight, holding.instrument_id)

        disclosed_industry_pct = min(
            sum(max(item.weight_pct, 0.0) for item in profile.industries),
            100.0,
        )
        for industry in profile.industries:
            contribution = weight * max(industry.weight_pct, 0.0) / 100.0
            _add_bucket(
                industries,
                industry.name,
                industry.name,
                contribution,
                holding.instrument_id,
            )
            industry_known_weight += contribution
        undisclosed_industry_weight = weight * (100.0 - disclosed_industry_pct) / 100.0
        if undisclosed_industry_weight > 0.0001:
            _add_bucket(
                industries,
                "__unknown_etf_industry__",
                "ETF未披露行业",
                undisclosed_industry_weight,
                holding.instrument_id,
            )

        for constituent in profile.holdings:
            contribution = weight * max(constituent.weight_pct, 0.0) / 100.0
            _add_underlying(
                underlyings,
                instrument_id=constituent.instrument_id,
                name=constituent.name,
                direct_weight=0.0,
                etf_weight=contribution,
                source_instrument_id=holding.instrument_id,
            )

    bucket_industries = _bucket_models(industries)
    bucket_indices = _bucket_models(indices)
    bucket_markets = _bucket_models(markets)
    bucket_styles = _bucket_models(styles)
    underlying_models = _underlying_models(underlyings)
    pair_models = _portfolio_etf_overlaps(overlaps, profile_by_id, holding_by_id)
    warnings = _portfolio_warnings(
        bucket_industries,
        bucket_indices,
        underlying_models,
        pair_models,
        unavailable_etf_weight,
        holdings,
    )
    invested_weight = sum(max(float(item.weight_pct), 0.0) for item in holdings)
    etf_weight = sum(
        max(float(item.weight_pct), 0.0)
        for item in holdings
        if item.asset_type.lower() == "etf"
    )
    constituent_known_weight = sum(item.known_weight_pct for item in underlying_models)
    status = "empty" if not holdings else "partial" if unavailable_etf_weight > 0 else "complete"
    return PortfolioLookThroughRisk(
        summary=PortfolioLookThroughSummary(
            position_count=len(holdings),
            stock_position_count=stock_count,
            etf_position_count=etf_count,
            invested_weight_pct=_rounded(invested_weight),
            etf_weight_pct=_rounded(etf_weight),
            industry_known_weight_pct=_rounded(industry_known_weight),
            constituent_known_weight_pct=_rounded(constituent_known_weight),
            unavailable_etf_weight_pct=_rounded(unavailable_etf_weight),
            warning_count=len(warnings),
            status=status,
        ),
        industries=bucket_industries,
        indices=bucket_indices,
        markets=bucket_markets,
        styles=bucket_styles,
        underlying_exposures=underlying_models,
        etf_overlaps=pair_models,
        warnings=warnings,
        data_health={
            "portfolio_lookthrough_scope": "current_open_positions",
            "portfolio_lookthrough_mode": "advisory_only",
            "portfolio_lookthrough_etf_holdings_scope": "latest_quarterly_top10",
            "portfolio_lookthrough_weights": "percent_of_total_equity",
        },
    )


def _add_bucket(
    buckets: dict[str, dict[str, object]],
    key: str,
    label: str,
    weight: float,
    instrument_id: str,
) -> None:
    bucket = buckets.setdefault(
        key,
        {"label": label, "weight": 0.0, "instrument_ids": set()},
    )
    bucket["weight"] = float(bucket["weight"]) + max(weight, 0.0)
    instrument_ids = bucket["instrument_ids"]
    if isinstance(instrument_ids, set):
        instrument_ids.add(instrument_id)


def _bucket_models(
    buckets: dict[str, dict[str, object]],
) -> list[PortfolioLookThroughBucket]:
    result = []
    for key, value in buckets.items():
        instrument_ids = sorted(str(item) for item in value["instrument_ids"])
        result.append(
            PortfolioLookThroughBucket(
                key=key,
                label=str(value["label"]),
                weight_pct=_rounded(float(value["weight"])),
                source_count=len(instrument_ids),
                instrument_ids=instrument_ids,
            )
        )
    return sorted(result, key=lambda item: (-item.weight_pct, item.label))


def _add_underlying(
    underlyings: dict[str, dict[str, object]],
    *,
    instrument_id: str,
    name: str,
    direct_weight: float,
    etf_weight: float,
    source_instrument_id: str,
) -> None:
    item = underlyings.setdefault(
        instrument_id,
        {
            "name": name,
            "direct_weight": 0.0,
            "etf_weight": 0.0,
            "sources": set(),
        },
    )
    if name and not str(item["name"]).strip():
        item["name"] = name
    item["direct_weight"] = float(item["direct_weight"]) + max(direct_weight, 0.0)
    item["etf_weight"] = float(item["etf_weight"]) + max(etf_weight, 0.0)
    sources = item["sources"]
    if isinstance(sources, set):
        sources.add(source_instrument_id)


def _underlying_models(
    underlyings: dict[str, dict[str, object]],
) -> list[PortfolioUnderlyingExposure]:
    result = []
    for instrument_id, value in underlyings.items():
        direct = float(value["direct_weight"])
        etf = float(value["etf_weight"])
        sources = sorted(str(item) for item in value["sources"])
        result.append(
            PortfolioUnderlyingExposure(
                instrument_id=instrument_id,
                name=str(value["name"]),
                known_weight_pct=_rounded(direct + etf),
                direct_weight_pct=_rounded(direct),
                etf_weight_pct=_rounded(etf),
                source_count=len(sources),
                source_instrument_ids=sources,
            )
        )
    return sorted(result, key=lambda item: (-item.known_weight_pct, item.instrument_id))


def _portfolio_etf_overlaps(
    overlaps: list[EtfExposureOverlap],
    profiles: dict[str, EtfExposureProfile],
    holdings: dict[str, PortfolioLookThroughHolding],
) -> list[PortfolioEtfOverlap]:
    result = []
    for overlap in overlaps:
        left_holding = holdings.get(overlap.left_instrument_id)
        right_holding = holdings.get(overlap.right_instrument_id)
        left_profile = profiles.get(overlap.left_instrument_id)
        right_profile = profiles.get(overlap.right_instrument_id)
        if not left_holding or not right_holding or not left_profile or not right_profile:
            continue
        left_weights = {item.instrument_id: item.weight_pct for item in left_profile.holdings}
        right_weights = {item.instrument_id: item.weight_pct for item in right_profile.holdings}
        shared_weight = sum(
            min(
                left_holding.weight_pct * left_weights.get(item.instrument_id, 0.0) / 100.0,
                right_holding.weight_pct * right_weights.get(item.instrument_id, 0.0) / 100.0,
            )
            for item in overlap.shared_constituents
        )
        result.append(
            PortfolioEtfOverlap(
                left_instrument_id=overlap.left_instrument_id,
                right_instrument_id=overlap.right_instrument_id,
                same_tracking_index=overlap.same_tracking_index,
                portfolio_overlap_lower_bound_pct=(
                    _rounded(shared_weight) if overlap.status == "measured" else None
                ),
                shared_constituents=[item.name for item in overlap.shared_constituents],
            )
        )
    return sorted(
        result,
        key=lambda item: (-(item.portfolio_overlap_lower_bound_pct or 0.0), item.left_instrument_id),
    )


def _portfolio_warnings(
    industries: list[PortfolioLookThroughBucket],
    indices: list[PortfolioLookThroughBucket],
    underlyings: list[PortfolioUnderlyingExposure],
    overlaps: list[PortfolioEtfOverlap],
    unavailable_etf_weight: float,
    holdings: list[PortfolioLookThroughHolding],
) -> list[PortfolioLookThroughWarning]:
    warnings: list[PortfolioLookThroughWarning] = []
    labels = {holding.instrument_id: holding.instrument_label for holding in holdings}
    for industry in industries:
        if industry.key.startswith("__unknown"):
            continue
        if industry.weight_pct >= INDUSTRY_CONCENTRATION_WARNING_PCT:
            warnings.append(
                PortfolioLookThroughWarning(
                    kind="industry_concentration",
                    severity="watch",
                    label=industry.label,
                    weight_pct=industry.weight_pct,
                    instrument_ids=industry.instrument_ids,
                )
            )
    for index in indices:
        if index.source_count >= 2 and not index.key.startswith("__unknown"):
            warnings.append(
                PortfolioLookThroughWarning(
                    kind="same_tracking_index",
                    severity="watch",
                    label=index.label,
                    weight_pct=index.weight_pct,
                    instrument_ids=index.instrument_ids,
                    related_names=[labels.get(item, item) for item in index.instrument_ids],
                )
            )
    for underlying in underlyings:
        if underlying.direct_weight_pct > 0 and underlying.etf_weight_pct > 0:
            warnings.append(
                PortfolioLookThroughWarning(
                    kind="direct_etf_overlap",
                    severity="watch",
                    label=underlying.name,
                    weight_pct=underlying.known_weight_pct,
                    instrument_ids=underlying.source_instrument_ids,
                )
            )
        elif underlying.known_weight_pct >= UNDERLYING_CONCENTRATION_WARNING_PCT:
            warnings.append(
                PortfolioLookThroughWarning(
                    kind="underlying_concentration",
                    severity="watch",
                    label=underlying.name,
                    weight_pct=underlying.known_weight_pct,
                    instrument_ids=underlying.source_instrument_ids,
                )
            )
    for overlap in overlaps:
        overlap_weight = overlap.portfolio_overlap_lower_bound_pct or 0.0
        if overlap_weight >= ETF_PAIR_OVERLAP_WARNING_PCT and not overlap.same_tracking_index:
            warnings.append(
                PortfolioLookThroughWarning(
                    kind="etf_constituent_overlap",
                    severity="info",
                    label="ETF披露成分重叠",
                    weight_pct=overlap.portfolio_overlap_lower_bound_pct,
                    instrument_ids=[overlap.left_instrument_id, overlap.right_instrument_id],
                    related_names=overlap.shared_constituents,
                )
            )
    if unavailable_etf_weight > 0:
        warnings.append(
            PortfolioLookThroughWarning(
                kind="missing_etf_disclosure",
                severity="watch",
                label="ETF穿透来源不完整",
                weight_pct=_rounded(unavailable_etf_weight),
            )
        )
    return warnings


def _stock_market_label(instrument_id: str) -> str:
    if instrument_id.startswith("CN:"):
        return "A股个股"
    return instrument_id.split(":", 1)[0] or "其他市场"


def _rounded(value: float) -> float:
    return round(value, 4)
