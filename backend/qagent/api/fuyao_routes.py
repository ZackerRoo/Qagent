from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from qagent.config import get_settings
from qagent.providers.fuyao import (
    FuyaoClient,
    FuyaoProviderError,
    fuyao_capability_manifest,
    to_fuyao_thscode,
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
    return _research_response(
        "stock",
        {"instrument_id": instrument_id.strip().upper(), "thscode": thscode},
        sections,
        errors,
    )


@router.get("/research/market")
def market_research(
    period: Literal["day", "hour"] = "day",
    trade_date: date | None = None,
    include_historical_hot_list: bool = False,
) -> dict[str, object]:
    if include_historical_hot_list and trade_date is None:
        raise HTTPException(
            status_code=422,
            detail="trade_date is required when include_historical_hot_list is true",
        )
    client = _configured_client()
    sections: dict[str, object] = {}
    errors: list[dict[str, object]] = []
    _collect(
        sections,
        errors,
        "limit_up_pool",
        lambda: client.get_limit_up_pool(trade_date=trade_date),
    )
    _collect(sections, errors, "limit_up_ladder", client.get_limit_up_ladder)
    _collect(
        sections,
        errors,
        "hot_stock_list",
        lambda: client.get_hot_stock_list(period=period),
    )
    _collect(
        sections,
        errors,
        "skyrocket_list",
        lambda: client.get_skyrocket_list(period=period),
    )
    _collect(sections, errors, "anomaly_analysis", client.get_anomaly_analysis)
    _collect(
        sections,
        errors,
        "dragon_tiger",
        lambda: client.get_dragon_tiger(trade_date=trade_date),
    )
    if include_historical_hot_list:
        assert trade_date is not None
        _collect(
            sections,
            errors,
            "hot_stock_history",
            lambda: client.get_hot_stock_history(trade_date),
        )
    return _research_response(
        "market",
        {
            "period": period,
            "trade_date": trade_date.isoformat() if trade_date else None,
        },
        sections,
        errors,
    )


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
    return _research_response("index", {"thscode": normalized}, sections, errors)


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
    return _research_response(
        "fund",
        {"instrument_id": instrument_id.strip().upper(), "thscode": thscode},
        sections,
        errors,
    )


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
) -> dict[str, object]:
    return {
        "provider": "fuyao",
        "research_type": research_type,
        "classification": "research_only",
        "decision_weight_applied": False,
        "paper_order_side_effect": False,
        "status": "ready" if sections and not errors else "partial" if sections else "unavailable",
        "identity": identity,
        "sections": sections,
        "errors": errors,
    }


def _provider_http_error(exc: FuyaoProviderError) -> HTTPException:
    detail: dict[str, object] = {"message": str(exc), "provider_code": exc.code}
    if exc.request_id:
        detail["request_id"] = exc.request_id
    return HTTPException(status_code=502, detail=detail)
