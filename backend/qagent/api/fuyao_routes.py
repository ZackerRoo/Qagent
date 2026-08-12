from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
import math
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from qagent.config import get_settings
from qagent.db import create_session_factory, initialize_database
from qagent.providers.fuyao import (
    FuyaoClient,
    FuyaoProviderError,
    fuyao_capability_manifest,
    to_fuyao_thscode,
)
from qagent.research.fuyao_market_sentiment import (
    capture_fuyao_market_research,
    latest_fuyao_market_research,
)
from qagent.research.fuyao_shadow_outcomes import (
    build_fuyao_shadow_evaluation,
    resolve_fuyao_shadow_outcomes,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.fuyao_research import (
    FuyaoResearchRepository,
    FuyaoResearchSnapshot,
)


router = APIRouter(prefix="/fuyao", tags=["fuyao"])


@router.get("/capabilities")
def capabilities() -> dict[str, object]:
    settings = get_settings()
    return fuyao_capability_manifest(configured=bool(settings.fuyao_api_key))


@router.get("/tickers/search")
def search_tickers(
    q: str,
    exchange: str | None = None,
    asset_type: str | None = None,
    limit: int = 10,
) -> dict[str, object]:
    client = _configured_client()
    try:
        result = client.search_tickers(
            q,
            exchange=exchange,
            asset_type=asset_type,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FuyaoProviderError as exc:
        raise _provider_http_error(exc) from exc
    return _research_response("ticker_search", {"query": q}, {"results": result}, [])


@router.get("/tickers")
def ticker_catalog(
    asset_type: str | None = None,
    limit: int = 1_000,
    offset: int = 0,
) -> dict[str, object]:
    client = _configured_client()
    try:
        result = client.list_tickers(asset_type=asset_type, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FuyaoProviderError as exc:
        raise _provider_http_error(exc) from exc
    return _research_response(
        "ticker_catalog",
        {"asset_type": asset_type, "limit": limit, "offset": offset},
        {"catalog": result},
        [],
    )


@router.get("/market/snapshot-page")
def stock_market_snapshot_page(
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    client = _configured_client()
    try:
        result = client.get_stock_market_page(limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FuyaoProviderError as exc:
        raise _provider_http_error(exc) from exc
    return _research_response(
        "stock_market_snapshot_page",
        {"limit": limit, "offset": offset},
        {"snapshot": result},
        [],
    )


@router.get("/market/trading-calendar")
def trading_calendar() -> dict[str, object]:
    client = _configured_client()
    try:
        result = client.get_trading_days()
    except FuyaoProviderError as exc:
        raise _provider_http_error(exc) from exc
    return _research_response("trading_calendar", {}, {"calendar": result}, [])


@router.get("/research/stock")
def stock_research(
    instrument_id: str,
    report: str | None = None,
    include_statements: bool = False,
    statement_period: Literal["annual", "quarterly"] = "quarterly",
    statement_limit: int = 4,
) -> dict[str, object]:
    client = _configured_client()
    thscode = _stock_thscode(instrument_id)
    today = date.today()
    sections: dict[str, object] = {}
    errors: list[dict[str, object]] = []

    _collect(sections, errors, "snapshot", lambda: client.get_stock_snapshot_data([thscode]))
    _collect(sections, errors, "valuation", lambda: client.get_valuations([thscode]))
    if report:
        _collect(
            sections,
            errors,
            "financial_indicators",
            lambda: client.get_financial_indicators(thscode, report),
        )
    else:
        _collect(
            sections,
            errors,
            "financial_indicators",
            lambda: client.get_latest_financial_indicators(thscode, as_of=today),
        )
    _collect(
        sections,
        errors,
        "corporate_actions",
        lambda: client.get_corporate_actions(
            thscode,
            start=today - timedelta(days=370),
            end=today,
        ),
    )
    _collect(
        sections,
        errors,
        "hot_rank_trend",
        lambda: client.get_hot_stock_rank_trend(
            thscode,
            start=today - timedelta(days=30),
            end=today,
        ),
    )
    _collect(
        sections,
        errors,
        "anomaly_analysis",
        lambda: client.get_anomaly_analysis(thscodes=[thscode]),
    )
    if include_statements:
        for statement in ("income", "balance", "cash_flow"):
            _collect(
                sections,
                errors,
                f"{statement}_statement",
                lambda statement=statement: client.get_financial_statements(
                    thscode,
                    statement,
                    period=statement_period,
                    limit=statement_limit,
                ),
            )
    response = _finalize_research_response(
        "stock",
        {"instrument_id": instrument_id.strip().upper(), "thscode": thscode},
        sections,
        errors,
        client=client,
        persist=True,
    )
    response["quality_comparison"] = _quote_quality_comparison(
        instrument_id.strip().upper(),
        response,
    )
    return response


@router.get("/research/market")
def market_research(
    period: Literal["day", "hour"] = "day",
    trade_date: date | None = None,
    include_historical_hot_list: bool = False,
    refresh: bool = False,
) -> dict[str, object]:
    if include_historical_hot_list and trade_date is None:
        raise HTTPException(
            status_code=422,
            detail="trade_date is required when include_historical_hot_list is true",
        )
    resolved_date = trade_date or date.today()
    initialize_database()
    capture = capture_fuyao_market_research(
        create_session_factory(),
        client=_configured_client(),
        trade_date=resolved_date,
        period=period,
        include_historical_hot_list=include_historical_hot_list,
        reuse_existing=not refresh,
    )
    return capture.response


@router.get("/research/market/latest")
def latest_market_research(include_raw: bool = False) -> dict[str, object]:
    initialize_database()
    latest = latest_fuyao_market_research(create_session_factory())
    if latest is None:
        raise HTTPException(status_code=404, detail="Fuyao market research has not started")
    return latest if include_raw else _compact_market_research(latest)


@router.get("/research/market/shadow-evaluation")
def market_shadow_evaluation(as_of_date: date | None = None) -> dict[str, object]:
    initialize_database()
    evaluation = build_fuyao_shadow_evaluation(
        create_session_factory(),
        as_of_date=as_of_date or date.today(),
    )
    return {"evaluation": evaluation.model_dump(mode="json")}


@router.post("/research/market/shadow-outcomes/resolve")
def resolve_market_shadow_outcomes(as_of_date: date | None = None) -> dict[str, object]:
    initialize_database()
    resolved_date = as_of_date or date.today()
    resolution = resolve_fuyao_shadow_outcomes(
        create_session_factory(),
        provider_mode="free",
        as_of_date=resolved_date,
    )
    evaluation = build_fuyao_shadow_evaluation(
        create_session_factory(),
        as_of_date=resolved_date,
    )
    return {
        "resolution": resolution.model_dump(mode="json"),
        "evaluation": evaluation.model_dump(mode="json"),
    }


@router.get("/research/index-catalog")
def index_catalog(
    tag: Literal["cn_concept", "region", "tszs", "industry"] = "industry",
) -> dict[str, object]:
    client = _configured_client()
    try:
        result = client.get_index_catalog(tag)
    except (ValueError, FuyaoProviderError) as exc:
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise _provider_http_error(exc) from exc
    return _research_response("index_catalog", {"tag": tag}, {"catalog": result}, [])


@router.get("/research/index")
def index_research(
    thscode: str,
    include_constituents: bool = True,
    history_days: int = 0,
) -> dict[str, object]:
    if history_days < 0 or history_days > 365:
        raise HTTPException(status_code=422, detail="history_days must be between 0 and 365")
    client = _configured_client()
    normalized = thscode.strip().upper()
    sections: dict[str, object] = {}
    errors: list[dict[str, object]] = []
    _collect(
        sections,
        errors,
        "snapshot",
        lambda: client.get_index_snapshot_data([normalized]),
    )
    if include_constituents:
        _collect(
            sections,
            errors,
            "constituents",
            lambda: client.get_index_constituents(normalized),
        )
    if history_days:
        today = date.today()
        _collect(
            sections,
            errors,
            "history",
            lambda: client.get_index_history_data(
                normalized,
                today - timedelta(days=history_days),
                today,
            ),
        )
    return _finalize_research_response(
        "index",
        {"thscode": normalized},
        sections,
        errors,
        client=client,
        persist=True,
    )


@router.get("/research/fund")
def fund_research(
    instrument_id: str,
    include_holders: bool = True,
    include_performance: bool = True,
    history_days: int = 0,
) -> dict[str, object]:
    if history_days < 0 or history_days > 365:
        raise HTTPException(status_code=422, detail="history_days must be between 0 and 365")
    client = _configured_client()
    try:
        thscode = to_fuyao_thscode(instrument_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sections: dict[str, object] = {}
    errors: list[dict[str, object]] = []
    _collect(sections, errors, "profile", lambda: client.get_fund_profile(thscode))
    _collect(sections, errors, "holdings", lambda: client.get_fund_holdings(thscode))
    _collect(sections, errors, "snapshot", lambda: client.get_fund_snapshot_data(thscode))
    if include_holders:
        _collect(sections, errors, "holders", lambda: client.get_fund_holders(thscode))
    if include_performance:
        _collect(sections, errors, "nav", lambda: client.get_fund_nav(thscode))
        _collect(sections, errors, "returns", lambda: client.get_fund_returns(thscode))
    if history_days:
        today = date.today()
        _collect(
            sections,
            errors,
            "history",
            lambda: client.get_fund_history_data(
                thscode,
                today - timedelta(days=history_days),
                today,
            ),
        )
    response = _finalize_research_response(
        "fund",
        {"instrument_id": instrument_id.strip().upper(), "thscode": thscode},
        sections,
        errors,
        client=client,
        persist=True,
    )
    response["quality_comparison"] = _quote_quality_comparison(
        instrument_id.strip().upper(),
        response,
    )
    return response


def _configured_client() -> FuyaoClient:
    settings = get_settings()
    if not settings.fuyao_api_key:
        raise HTTPException(status_code=409, detail="Fuyao API key is not configured")
    return FuyaoClient(
        settings.fuyao_api_key,
        base_url=settings.fuyao_base_url,
        request_timeout_seconds=settings.fuyao_timeout_seconds,
    )


def _stock_thscode(instrument_id: str) -> str:
    normalized = instrument_id.strip().upper()
    if normalized.endswith(".IDX"):
        raise HTTPException(status_code=422, detail="stock research does not accept index IDs")
    try:
        thscode = to_fuyao_thscode(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    symbol = thscode.split(".", 1)[0]
    if symbol.startswith(("15", "16", "51", "52", "56", "58")):
        raise HTTPException(status_code=422, detail="use the fund research endpoint for ETFs")
    return thscode


def _collect(
    sections: dict[str, object],
    errors: list[dict[str, object]],
    name: str,
    loader: Callable[[], dict[str, Any]],
) -> None:
    try:
        sections[name] = loader()
    except (ValueError, FuyaoProviderError) as exc:
        error: dict[str, object] = {
            "section": name,
            "code": exc.code if isinstance(exc, FuyaoProviderError) else "invalid_request",
            "message": str(exc),
        }
        if isinstance(exc, FuyaoProviderError) and exc.request_id:
            error["request_id"] = exc.request_id
        errors.append(error)


def _research_response(
    research_type: str,
    identity: dict[str, object],
    sections: dict[str, object],
    errors: list[dict[str, object]],
    *,
    client: FuyaoClient | None = None,
) -> dict[str, object]:
    return {
        "provider": "fuyao",
        "research_type": research_type,
        "classification": "research_only",
        "decision_weight_applied": False,
        "paper_order_side_effect": False,
        "status": "ready" if sections and not errors else "partial" if sections else "unavailable",
        "freshness": "live" if sections else "unavailable",
        "identity": identity,
        "sections": sections,
        "summary": _build_research_summary(research_type, sections),
        "source": _client_source(client),
        "errors": errors,
    }


def _finalize_research_response(
    research_type: str,
    identity: dict[str, object],
    sections: dict[str, object],
    errors: list[dict[str, object]],
    *,
    client: FuyaoClient | None = None,
    persist: bool = False,
) -> dict[str, object]:
    response = _research_response(
        research_type,
        identity,
        sections,
        errors,
        client=client,
    )
    if not persist:
        return response

    try:
        repository = _research_repository()
        if sections:
            source = response.get("source")
            source_map = source if isinstance(source, dict) else {}
            snapshot = repository.append(
                research_type=research_type,
                identity=identity,
                payload=_persisted_payload(response),
                source_request_id=_optional_text(source_map.get("request_id")),
                source_timestamp=_optional_text(source_map.get("data_timestamp")),
            )
            response["snapshot"] = _snapshot_reference(snapshot, persisted=True)
            return response

        latest = repository.latest(research_type=research_type, identity=identity)
        if latest is None:
            response["snapshot"] = None
            return response
        stored = latest.payload
        response.update(
            {
                "status": "stale",
                "freshness": "stored_fallback",
                "sections": stored.get("sections", {}),
                "summary": stored.get("summary", {}),
                "snapshot": _snapshot_reference(latest, persisted=True),
            }
        )
        return response
    except Exception as exc:
        response["snapshot"] = {"persisted": False, "error": str(exc)[:300]}
        return response


def _research_repository() -> FuyaoResearchRepository:
    initialize_database()
    return FuyaoResearchRepository(create_session_factory())


def _persisted_payload(response: dict[str, object]) -> dict[str, object]:
    return {
        key: response[key]
        for key in (
            "provider",
            "research_type",
            "classification",
            "decision_weight_applied",
            "paper_order_side_effect",
            "identity",
            "sections",
            "summary",
            "errors",
        )
    }


def _snapshot_reference(
    snapshot: FuyaoResearchSnapshot,
    *,
    persisted: bool,
) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "payload_digest": snapshot.payload_digest,
        "observed_at": snapshot.observed_at.isoformat(),
        "source_timestamp": snapshot.source_timestamp,
        "source_request_id": snapshot.source_request_id,
        "persisted": persisted,
    }


def _client_source(client: FuyaoClient | None) -> dict[str, object]:
    if client is None:
        return {}
    snapshot_getter = getattr(client, "telemetry_snapshot", None)
    if callable(snapshot_getter):
        telemetry = snapshot_getter()
        source = asdict(telemetry)
        source.update(
            {
                "request_id": telemetry.last_request_id,
                "data_timestamp": telemetry.last_data_timestamp,
            }
        )
        return source
    last_request = getattr(client, "last_request", None)
    return {
        "request_id": getattr(last_request, "request_id", None),
        "data_timestamp": getattr(last_request, "timestamp", None),
    }


def _build_research_summary(
    research_type: str,
    sections: dict[str, object],
) -> dict[str, object]:
    if research_type == "stock":
        return _stock_research_summary(sections)
    if research_type == "fund":
        return _fund_research_summary(sections)
    if research_type == "index":
        snapshot = _first_item(sections.get("snapshot"))
        return {
            "title": _optional_text(snapshot.get("name")),
            "metrics": _market_metrics(snapshot),
            "notes": [],
        }
    return {"title": None, "metrics": [], "notes": []}


def _stock_research_summary(sections: dict[str, object]) -> dict[str, object]:
    snapshot = _first_item(sections.get("snapshot"))
    valuation = _first_item(sections.get("valuation"))
    indicators = _financial_indicators(sections.get("financial_indicators"))
    hot_items = _items(sections.get("hot_rank_trend"))
    hot_rank = _safe_number(hot_items[-1].get("rank")) if hot_items else None
    metrics = [
        *_market_metrics(snapshot),
        _metric("pe_ttm", "市盈率 TTM", valuation.get("pe_ttm")),
        _metric("pb_mrq", "市净率 MRQ", valuation.get("pb_mrq")),
        _metric("ps_ttm", "市销率 TTM", valuation.get("ps_ttm")),
        _metric(
            "revenue_yoy",
            "营收同比",
            indicators.get("calculate_operating_income_yoy_growth_ratio"),
            "%",
        ),
        _metric(
            "net_profit_yoy",
            "归母净利同比",
            indicators.get("calculate_parent_holder_net_profit_yoy_growth_ratio"),
            "%",
        ),
        _metric("gross_margin", "销售毛利率", indicators.get("sale_gross_margin"), "%"),
        _metric(
            "roe",
            "加权 ROE",
            indicators.get("index_deduct_weighted_avg_roe")
            or indicators.get("index_weighted_avg_roe"),
            "%",
        ),
        _metric("hot_rank", "热度排名", hot_rank),
    ]
    report = sections.get("financial_indicators")
    report_name = report.get("report") if isinstance(report, dict) else None
    notes = [f"财务报告期 {report_name}"] if report_name else []
    return {
        "title": _optional_text(valuation.get("name")) or _optional_text(snapshot.get("name")),
        "metrics": [item for item in metrics if item is not None],
        "notes": notes,
    }


def _fund_research_summary(sections: dict[str, object]) -> dict[str, object]:
    profile = _first_item(sections.get("profile"))
    snapshot = _first_item(sections.get("snapshot"))
    returns = _first_item(sections.get("returns"))
    holdings = _items(sections.get("holdings"))
    top_holding = holdings[0] if holdings else {}
    metrics = [
        *_market_metrics(snapshot),
        _metric("return_month", "近一月", returns.get("return_month"), "%"),
        _metric("return_nowyear", "今年以来", returns.get("return_nowyear"), "%"),
        _metric("return_year", "近一年", returns.get("return_year"), "%"),
        _metric("holdings_count", "披露持仓数", len(holdings)),
        _metric(
            "top_holding",
            "第一大持仓",
            _optional_text(top_holding.get("stock_name"))
            or _optional_text(top_holding.get("thscode")),
        ),
        _metric("top_holding_ratio", "第一大持仓占比", top_holding.get("hold_ratio"), "%"),
    ]
    manager = _optional_text(profile.get("manager_name"))
    return {
        "title": _optional_text(profile.get("fund_name")) or _optional_text(snapshot.get("name")),
        "metrics": [item for item in metrics if item is not None],
        "notes": [f"基金经理 {manager}"] if manager else [],
    }


def _market_metrics(item: dict[str, object]) -> list[dict[str, object]]:
    metrics = [
        _metric("latest_price", "最新价", item.get("last_price")),
        _metric("change_pct", "涨跌幅", item.get("price_change_ratio_pct"), "%"),
        _metric("turnover", "成交额", item.get("turnover"), "元"),
    ]
    return [metric for metric in metrics if metric is not None]


def _metric(
    key: str,
    label: str,
    raw_value: object,
    unit: str | None = None,
) -> dict[str, object] | None:
    value: object
    number = _safe_number(raw_value)
    if number is not None:
        value = number
    else:
        text = _optional_text(raw_value)
        if text is None:
            return None
        value = text
    return {"key": key, "label": label, "value": value, "unit": unit}


def _financial_indicators(section: object) -> dict[str, object]:
    if not isinstance(section, dict):
        return {}
    result: dict[str, object] = {}
    abilities = section.get("abilities")
    if not isinstance(abilities, list):
        return result
    for ability in abilities:
        if not isinstance(ability, dict):
            continue
        indicators = ability.get("indicators")
        if not isinstance(indicators, list):
            continue
        for indicator in indicators:
            if not isinstance(indicator, dict):
                continue
            key = _optional_text(indicator.get("index_id"))
            if key:
                result[key] = indicator.get("value")
    return result


def _items(section: object) -> list[dict[str, object]]:
    if not isinstance(section, dict):
        return []
    items = section.get("item")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _first_item(section: object) -> dict[str, object]:
    items = _items(section)
    return items[0] if items else {}


def _safe_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quote_quality_comparison(
    instrument_id: str,
    response: dict[str, object],
) -> dict[str, object]:
    summary = response.get("summary")
    metrics = summary.get("metrics") if isinstance(summary, dict) else None
    fuyao_price = next(
        (
            _safe_number(metric.get("value"))
            for metric in metrics
            if isinstance(metric, dict) and metric.get("key") == "latest_price"
        ),
        None,
    ) if isinstance(metrics, list) else None
    cache = MarketDataCacheRepository(create_session_factory())
    frame = cache.load_latest_daily_bars("free", [instrument_id])
    if frame.empty or fuyao_price is None:
        return {
            "state": "insufficient",
            "fuyao_price": fuyao_price,
            "reference_price": None,
            "difference_pct": None,
            "classification": "research_only",
        }
    row = frame.iloc[0]
    reference_price = _safe_number(row.get("close"))
    reference_date = row.get("trade_date")
    source = response.get("source")
    raw_source_timestamp = (
        source.get("data_timestamp") if isinstance(source, dict) else None
    )
    if raw_source_timestamp is None:
        snapshot = response.get("snapshot")
        raw_source_timestamp = (
            snapshot.get("source_timestamp") if isinstance(snapshot, dict) else None
        )
    source_date = _timestamp_date(raw_source_timestamp)
    difference_pct = (
        (fuyao_price / reference_price - 1.0) * 100.0
        if reference_price is not None and reference_price > 0
        else None
    )
    same_session = source_date is not None and source_date == reference_date
    if difference_pct is None:
        state = "insufficient"
    elif not same_session:
        state = "different_sessions"
    elif abs(difference_pct) <= 0.5:
        state = "aligned"
    elif abs(difference_pct) <= 1.0:
        state = "watch"
    else:
        state = "mismatch"
    return {
        "state": state,
        "fuyao_price": fuyao_price,
        "fuyao_timestamp": raw_source_timestamp,
        "reference_price": reference_price,
        "reference_trade_date": (
            reference_date.isoformat() if isinstance(reference_date, date) else None
        ),
        "reference_provider": str(row.get("provider") or "free"),
        "difference_pct": round(difference_pct, 6) if difference_pct is not None else None,
        "same_session": same_session,
        "classification": "research_only",
    }


def _timestamp_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _compact_market_research(response: dict[str, object]) -> dict[str, object]:
    compact = dict(response)
    sections = response.get("sections")
    derived = sections.get("derived_sentiment") if isinstance(sections, dict) else None
    compact["sections"] = (
        {"derived_sentiment": derived} if isinstance(derived, dict) else {}
    )
    compact["raw_sections_included"] = False
    compact["raw_sections_available"] = (
        sorted(
            key
            for key in sections
            if isinstance(key, str) and key != "derived_sentiment"
        )
        if isinstance(sections, dict)
        else []
    )
    return compact


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _provider_http_error(exc: FuyaoProviderError) -> HTTPException:
    detail: dict[str, object] = {"message": str(exc), "provider_code": exc.code}
    if exc.request_id:
        detail["request_id"] = exc.request_id
    return HTTPException(status_code=502, detail=detail)
