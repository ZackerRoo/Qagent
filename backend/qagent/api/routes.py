from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from qagent.agent.responder import answer_question
from qagent.api.schemas import AgentQueryRequest, AgentQueryResponse, AlertEvaluationRequest
from qagent.backtesting.engine import run_historical_backtest
from qagent.backtesting.portfolio import run_portfolio_backtest
from qagent.briefing.daily import DailyBrief, build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.db import create_session_factory, initialize_database
from qagent.factors.backtest import run_factor_backtest
from qagent.jobs.automation import run_research_automation
from qagent.jobs.daily_scan import run_daily_scan
from qagent.jobs.alert_runner import run_alert_rules
from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.market.a_share_universe import ResolvedSymbols, resolve_symbol_tokens
from qagent.market.instruments import format_instrument_label
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.market.universes import UniverseCreate, builtin_universes, merge_universes
from qagent.market.indicators import add_moving_averages, add_volume_ratio, percent_distance
from qagent.monitoring.outcomes import (
    compute_opportunity_outcome,
    diagnose_strategy_performance,
    summarize_strategy_performance,
)
from qagent.monitoring.portfolio import PositionInput, analyze_position_risk
from qagent.monitoring.alerts import AlertRule, suggest_alert_rules
from qagent.paper_trading.engine import (
    seed_paper_trades_from_snapshots,
    update_paper_trades,
    summarize_paper_trades,
)
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.storage.paper import PaperTradingRepository
from qagent.storage.repository import (
    AlertRuleCreate,
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.strategy_data.providers import EmptyStrategyDataProvider

router = APIRouter()


def _signal_summary(card) -> str:
    return "; ".join(
        f"{signal.signal_type.value} {signal.direction.value} {signal.score:.2f}"
        for signal in card.signals[:4]
    )


def _strategy_summary(card) -> str:
    return "; ".join(
        f"{strategy.strategy_id} {strategy.status} {strategy.score:.2f}"
        for strategy in card.strategy_evaluations[:5]
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/provider-status")
def provider_status() -> dict[str, list[object]]:
    return {"providers": [status.model_dump(mode="json") for status in build_provider_status()]}


@router.get("/data-cache")
def data_cache(
    provider: str | None = None,
    instrument_id: str | None = None,
) -> dict[str, list[object]]:
    summaries = _market_cache_repo().list_summaries(
        provider_mode=provider.strip().lower() if provider else None,
        instrument_id=instrument_id.strip().upper() if instrument_id else None,
    )
    return {"summaries": [summary.model_dump(mode="json") for summary in summaries]}


@router.delete("/data-cache")
def clear_data_cache(
    provider: str | None = None,
    instrument_id: str | None = None,
) -> dict[str, int]:
    deleted = _market_cache_repo().delete(
        provider_mode=provider.strip().lower() if provider else None,
        instrument_id=instrument_id.strip().upper() if instrument_id else None,
    )
    return {"deleted": deleted}


def _parse_symbols(symbols: str | None, default_universe: list[str]) -> list[str]:
    if not symbols:
        return default_universe
    return [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]


def _resolve_symbols(provider_mode: str, symbols: str | None) -> ResolvedSymbols:
    mode = provider_mode.strip().lower()
    default_universe = DEFAULT_FREE_UNIVERSE if mode == "free" else DEFAULT_DEV_UNIVERSE
    parsed = _parse_symbols(symbols, default_universe)
    if mode == "free":
        return resolve_symbol_tokens(parsed)
    return ResolvedSymbols(symbols=parsed)


def _scan(provider_mode: str = "fixture", symbols: str | None = None):
    mode = provider_mode.strip().lower()
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    try:
        provider = build_market_data_provider(mode)
        strategy_data_provider = EmptyStrategyDataProvider() if resolved.is_dynamic else None
        result = run_daily_scan(
            instrument_ids,
            provider,
            mode=mode,
            strategy_data_provider=strategy_data_provider,
        )
        result.data_health.update(resolved.data_health)
        if resolved.is_dynamic:
            result.data_health["strategy_data_skipped"] = "true"
        return result, mode, instrument_ids
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _backtest_dates(mode: str, start: date | None, end: date | None) -> tuple[date, date]:
    end_date = end or (date(2026, 3, 20) if mode == "fixture" else date.today())
    start_date = start or (
        date(2026, 1, 15) if mode == "fixture" else end_date - timedelta(days=180)
    )
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    return start_date, end_date


def _factor_backtest_dates(mode: str, start: date | None, end: date | None) -> tuple[date, date]:
    if start or end:
        return _backtest_dates(mode, start, end)
    if mode == "fixture":
        return date(1900, 1, 1), date(2100, 1, 1)
    end_date = date.today()
    return end_date - timedelta(days=365), end_date


def _chart_dates(mode: str, days: int) -> tuple[date, date]:
    if mode == "fixture":
        return date(1900, 1, 1), date(2100, 1, 1)
    end_date = date.today()
    return end_date - timedelta(days=max(days * 3, 240)), end_date


def _chart_bar(row) -> dict[str, object]:
    return {
        "trade_date": row["trade_date"].isoformat(),
        "open": _clean_float(row["open"]),
        "high": _clean_float(row["high"]),
        "low": _clean_float(row["low"]),
        "close": _clean_float(row["close"]),
        "volume": int(row["volume"]),
        "ma20": _clean_float(row.get("ma_20")),
        "ma50": _clean_float(row.get("ma_50")),
        "ma100": _clean_float(row.get("ma_100")),
        "ma200": _clean_float(row.get("ma_200")),
    }


def _chart_levels(card) -> dict[str, str | None]:
    if card is None:
        return {
            "trigger_price": None,
            "initial_stop": None,
            "target_1": None,
            "target_2": None,
            "no_chase_above": None,
        }
    return {
        "trigger_price": _decimal_text(card.entry_plan.trigger_price),
        "initial_stop": _decimal_text(card.exit_plan.initial_stop),
        "target_1": _decimal_text(card.exit_plan.target_1),
        "target_2": _decimal_text(card.exit_plan.target_2),
        "no_chase_above": _decimal_text(card.entry_plan.no_chase_above),
    }


def _radar_item(instrument_id: str, bars, card, scan_item) -> dict[str, object]:
    if bars.empty:
        return {
            "instrument_id": instrument_id,
            "instrument_label": format_instrument_label(instrument_id),
            "latest_trade_date": None,
            "latest_close": None,
            "previous_close": None,
            "change_pct": None,
            "volume_ratio": None,
            "signal": "no_setup",
            "severity": "info",
            "score": 0.0,
            "message": "No daily bars are available for the latest radar scan.",
            "action": "Skip until market data is available.",
            "distance_to_trigger_pct": None,
            "trigger_price": None,
            "initial_stop": None,
            "target_1": None,
            "no_chase_above": None,
        }

    enriched = add_volume_ratio(bars.sort_values("trade_date"), window=20)
    latest = enriched.iloc[-1]
    previous = enriched.iloc[-2] if len(enriched) >= 2 else None
    latest_close = Decimal(str(round(float(latest["close"]), 2)))
    previous_close = (
        Decimal(str(round(float(previous["close"]), 2))) if previous is not None else None
    )
    change_pct = (
        percent_distance(float(latest_close), float(previous_close))
        if previous_close not in {None, Decimal("0")}
        else None
    )
    volume_ratio = _clean_float(latest.get("volume_ratio"))

    if card is None:
        reason = scan_item.reason if scan_item is not None else "Signal stack did not meet threshold."
        return _radar_payload(
            instrument_id=instrument_id,
            latest=latest,
            latest_close=latest_close,
            previous_close=previous_close,
            change_pct=change_pct,
            volume_ratio=volume_ratio,
            signal="no_setup",
            severity="info",
            score=0.1,
            message=f"No recommendation yet: {reason}",
            action="Keep on watchlist; review blockers before considering entry.",
            card=None,
            distance_to_trigger_pct=None,
        )

    trigger = card.entry_plan.trigger_price
    stop = card.exit_plan.initial_stop
    target = card.exit_plan.target_1
    no_chase = card.entry_plan.no_chase_above
    distance_to_trigger_pct = (
        percent_distance(float(trigger), float(latest_close)) if trigger is not None else None
    )

    signal = "inside_plan"
    severity = "info"
    score = card.rank_score
    message = "Price remains inside the current research plan."
    action = "Track trigger, stop, target, and no-chase levels."

    if stop is not None and latest_close <= stop * Decimal("1.02"):
        signal = "near_stop"
        severity = "danger"
        score = 0.98
        message = "Latest price is close to or below the stop guard."
        action = "Do not add exposure; verify whether the setup is invalidated."
    elif target is not None and latest_close >= target * Decimal("0.98"):
        signal = "near_target"
        severity = "success"
        score = 0.92
        message = "Latest price is near the first target."
        action = "Follow the exit plan; consider partial profit or tighter trailing stop."
    elif no_chase is not None and latest_close > no_chase:
        signal = "overextended"
        severity = "warning"
        score = 0.9
        message = "Latest price is above the no-chase level."
        action = "Avoid chasing; wait for a new setup or pullback."
    elif trigger is not None and latest_close >= trigger and (volume_ratio or 0) >= 1.1:
        signal = "trigger_breakout"
        severity = "success"
        score = 0.88
        message = "Price has crossed the trigger with acceptable volume confirmation."
        action = "Check no-chase level and risk vetoes before treating it as actionable."
    elif distance_to_trigger_pct is not None and 0 <= distance_to_trigger_pct <= 3:
        signal = "approaching_trigger"
        severity = "watch"
        score = 0.82
        message = "Price is approaching the planned trigger."
        action = "Wait for trigger and volume confirmation; avoid early entry."
    elif volume_ratio is not None and volume_ratio >= 1.8:
        signal = "volume_surge"
        severity = "watch"
        score = 0.75
        message = "Volume is unusually high relative to recent history."
        action = "Check whether price confirms the strategy trigger."
    elif "overextended" in card.factor_flags:
        signal = "overextended"
        severity = "warning"
        score = 0.7
        message = "Factor model marks the setup as short-term overextended."
        action = "Wait for consolidation or pullback before considering entry."

    return _radar_payload(
        instrument_id=instrument_id,
        latest=latest,
        latest_close=latest_close,
        previous_close=previous_close,
        change_pct=change_pct,
        volume_ratio=volume_ratio,
        signal=signal,
        severity=severity,
        score=score,
        message=message,
        action=action,
        card=card,
        distance_to_trigger_pct=distance_to_trigger_pct,
    )


def _radar_payload(
    *,
    instrument_id: str,
    latest,
    latest_close: Decimal,
    previous_close: Decimal | None,
    change_pct: float | None,
    volume_ratio: float | None,
    signal: str,
    severity: str,
    score: float,
    message: str,
    action: str,
    card,
    distance_to_trigger_pct: float | None,
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_label": format_instrument_label(instrument_id),
        "latest_trade_date": latest["trade_date"].isoformat(),
        "latest_close": _decimal_text(latest_close),
        "previous_close": _decimal_text(previous_close),
        "change_pct": change_pct,
        "volume_ratio": volume_ratio,
        "signal": signal,
        "severity": severity,
        "score": round(float(score), 4),
        "message": message,
        "action": action,
        "distance_to_trigger_pct": distance_to_trigger_pct,
        "trigger_price": _decimal_text(card.entry_plan.trigger_price) if card else None,
        "initial_stop": _decimal_text(card.exit_plan.initial_stop) if card else None,
        "target_1": _decimal_text(card.exit_plan.target_1) if card else None,
        "no_chase_above": _decimal_text(card.entry_plan.no_chase_above) if card else None,
    }


def _radar_severity_rank(severity: str) -> int:
    return {"danger": 4, "success": 3, "warning": 2, "watch": 1, "info": 0}.get(severity, 0)


def _clean_float(value) -> float | None:
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except TypeError:
        return None
    return round(float(value), 4)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _repo() -> QagentRepository:
    initialize_database()
    return QagentRepository(create_session_factory())


def _market_cache_repo() -> MarketDataCacheRepository:
    initialize_database()
    return MarketDataCacheRepository(create_session_factory())


def _paper_repo() -> PaperTradingRepository:
    initialize_database()
    return PaperTradingRepository(create_session_factory())


@router.get("/opportunities")
def opportunities(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, mode, instrument_ids = _scan(provider, symbols)
    _repo().save_scan_run(provider=mode, mode=mode, symbols=instrument_ids, result=result)
    return {
        "cards": [card.model_dump(mode="json") for card in result.cards],
        "items": [item.model_dump(mode="json") for item in result.items],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health],
        "factor_rankings": [item.model_dump(mode="json") for item in result.factor_rankings],
        "data_health": result.data_health,
    }


@router.get("/market-bars")
def market_bars(
    provider: str = "fixture",
    instrument_id: str = "US:TEST",
    days: int = 160,
) -> dict[str, object]:
    mode = provider.strip().lower()
    instrument = instrument_id.strip().upper()
    if days <= 0 or days > 500:
        raise HTTPException(status_code=400, detail="days must be between 1 and 500")
    try:
        market_provider = build_market_data_provider(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    start_date, end_date = _chart_dates(mode, days)
    bars = market_provider.get_daily_bars([instrument], start=start_date, end=end_date)
    if bars.empty:
        raise HTTPException(status_code=404, detail="market bars not found")
    enriched = add_moving_averages(bars.sort_values("trade_date"), windows=(20, 50, 100, 200))
    visible = enriched.tail(days)
    scan_result = run_daily_scan([instrument], market_provider, mode=mode)
    card = next((item for item in scan_result.cards if item.instrument_id == instrument), None)
    provider_errors = getattr(market_provider, "last_errors", [])
    data_health = {
        "provider": mode,
        "instrument": instrument,
        "bars": str(len(visible)),
    }
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return {
        "instrument_id": instrument,
        "bars": [_chart_bar(row) for _, row in visible.iterrows()],
        "levels": _chart_levels(card),
        "data_health": data_health,
    }


@router.get("/intraday-radar")
def intraday_radar(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    mode = provider.strip().lower()
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    try:
        market_provider = build_market_data_provider(mode)
        scan_result = run_daily_scan(
            instrument_ids,
            market_provider,
            mode=mode,
            strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    start_date, end_date = _chart_dates(mode, 80)
    cards_by_id = {card.instrument_id: card for card in scan_result.cards}
    scan_items_by_id = {item.instrument_id: item for item in scan_result.items}
    radar_items = []
    for instrument in instrument_ids:
        bars = market_provider.get_daily_bars([instrument], start=start_date, end=end_date)
        radar_items.append(
            _radar_item(instrument, bars, cards_by_id.get(instrument), scan_items_by_id.get(instrument))
        )
    radar_items.sort(key=lambda item: (_radar_severity_rank(item["severity"]), item["score"]), reverse=True)
    provider_errors = getattr(market_provider, "last_errors", [])
    data_health = {
        "provider": mode,
        "symbols": str(len(instrument_ids)),
        "radar_items": str(len(radar_items)),
    }
    data_health.update(resolved.data_health)
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return {"items": radar_items, "data_health": data_health}


@router.get("/factors")
def factors(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, mode, instrument_ids = _scan(provider, symbols)
    return {
        "provider": mode,
        "symbols": instrument_ids,
        "rankings": [item.model_dump(mode="json") for item in result.factor_rankings],
        "data_health": result.data_health,
    }


@router.get("/factors/backtest")
def factor_backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    forward_days: int = 20,
    step_days: int = 20,
    top_n: int = 3,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if forward_days <= 0 or forward_days > 120:
        raise HTTPException(status_code=400, detail="forward_days must be between 1 and 120")
    if step_days <= 0 or step_days > 120:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 120")
    if top_n <= 0 or top_n > 50:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 50")
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    start_date, end_date = _factor_backtest_dates(mode, start, end)
    market_provider = build_market_data_provider(mode)
    bars = market_provider.get_daily_bars(instrument_ids, start_date, end_date)
    result = run_factor_backtest(
        bars,
        forward_days=forward_days,
        step_days=step_days,
        top_n=top_n,
    )
    payload = result.model_dump(mode="json")
    payload["data_health"].update(resolved.data_health)
    return payload


@router.get("/overview")
def overview(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, _, _ = _scan(provider, symbols)
    return {
        "market_regime": {
            "US": "development_fixture",
            "CN": "development_fixture",
        },
        "top_cards": [card.model_dump(mode="json") for card in result.cards[:5]],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health[:6]],
        "factor_rankings": [item.model_dump(mode="json") for item in result.factor_rankings[:10]],
        "data_health": result.data_health,
    }


@router.get("/daily-brief")
def daily_brief(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
) -> dict[str, object]:
    brief = _build_daily_brief_response(provider, symbols, limit, include_news)
    return brief.model_dump(mode="json")


@router.post("/daily-brief/runs")
def save_daily_brief_run(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
) -> dict[str, object]:
    brief = _build_daily_brief_response(provider, symbols, limit, include_news)
    saved = _repo().save_brief_run(brief)
    return saved.model_dump(mode="json")


@router.get("/daily-brief/runs")
def daily_brief_runs(limit: int = 20) -> dict[str, list[object]]:
    return {"runs": [run.model_dump(mode="json") for run in _repo().list_brief_runs(limit=limit)]}


@router.get("/daily-brief/runs/{brief_id}")
def daily_brief_run(brief_id: str) -> dict[str, object]:
    run = _repo().get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    return {"run": run.model_dump(mode="json"), "brief": run.payload}


@router.get("/daily-brief/runs/{brief_id}/markdown")
def daily_brief_run_markdown(brief_id: str) -> dict[str, str]:
    run = _repo().get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    brief = DailyBrief.model_validate(run.payload)
    return {"markdown": render_daily_brief_markdown(brief)}


@router.post("/daily-brief/runs/{brief_id}/deliveries")
def queue_daily_brief_delivery(
    brief_id: str,
    channel: str = "markdown",
    recipient: str | None = None,
) -> dict[str, object]:
    repo = _repo()
    run = repo.get_brief_run(brief_id)
    if run is None:
        raise HTTPException(status_code=404, detail="brief run not found")
    if channel not in {"markdown", "email", "webhook"}:
        raise HTTPException(status_code=400, detail="unsupported delivery channel")
    brief = DailyBrief.model_validate(run.payload)
    delivery = repo.enqueue_brief_delivery(
        brief_run=run,
        channel=channel,
        recipient=recipient,
        markdown=render_daily_brief_markdown(brief),
    )
    return delivery.model_dump(mode="json")


@router.get("/deliveries")
def deliveries(status: str | None = None, limit: int = 20) -> dict[str, list[object]]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return {
        "deliveries": [
            delivery.model_dump(mode="json")
            for delivery in _repo().list_delivery_outbox(status=status, limit=limit)
        ]
    }


@router.post("/deliveries/{delivery_id}/mark-sent")
def mark_delivery_sent(delivery_id: str) -> dict[str, object]:
    delivery = _repo().mark_delivery_sent(delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return delivery.model_dump(mode="json")


@router.post("/automation/run")
def run_automation(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
    queue_brief: bool = True,
    run_alerts: bool = False,
    queue_alerts: bool = True,
    run_backtest: bool = True,
    recipient: str | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if limit <= 0 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    try:
        result = run_research_automation(
            repo=_repo(),
            provider=build_market_data_provider(mode),
            provider_mode=mode,
            symbols=instrument_ids,
            include_news=False if resolved.is_dynamic else include_news,
            queue_brief=queue_brief,
            run_alerts=run_alerts,
            queue_alerts=queue_alerts,
            run_backtest=run_backtest,
            recipient=recipient,
            limit=limit,
            strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
        )
        result.data_health.update(resolved.data_health)
        if resolved.is_dynamic:
            result.data_health["automation_news_scope"] = "skipped_for_dynamic_universe"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@router.get("/paper-trades")
def paper_trades(status: str | None = None, limit: int = 100) -> dict[str, object]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    trades = _paper_repo().list_trades(status=status, limit=limit)
    return {
        "summary": summarize_paper_trades(trades).model_dump(mode="json"),
        "trades": [trade.model_dump(mode="json") for trade in trades],
    }


@router.post("/paper-trades/seed")
def seed_paper_trades(provider: str = "fixture", limit: int = 50) -> dict[str, object]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    mode = provider.strip().lower()
    snapshots = _repo().list_opportunity_snapshots(limit=limit)
    result = seed_paper_trades_from_snapshots(_paper_repo(), snapshots, provider=mode)
    return result.model_dump(mode="json")


@router.post("/paper-trades/update")
def update_paper_trade_status(provider: str = "fixture") -> dict[str, object]:
    mode = provider.strip().lower()
    try:
        result = update_paper_trades(
            _paper_repo(),
            provider=build_market_data_provider(mode),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump(mode="json")


def _build_daily_brief_response(
    provider: str,
    symbols: str | None,
    limit: int,
    include_news: bool,
):
    mode = provider.strip().lower()
    if limit <= 0 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, None, None)
    try:
        market_provider = build_market_data_provider(mode)
        scan_result = run_daily_scan(
            instrument_ids,
            market_provider,
            mode=mode,
            strategy_data_provider=EmptyStrategyDataProvider() if resolved.is_dynamic else None,
        )
        backtest_result = run_historical_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=5,
            max_signals=100,
        )
        position_risks = _position_risks(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    catalyst_hypotheses = []
    brief_health = {
        "brief_provider": mode,
        "brief_symbols": str(len(instrument_ids)),
        "brief_news": "skipped",
    }
    brief_health.update(resolved.data_health)
    if resolved.is_dynamic:
        brief_health["strategy_data_skipped"] = "true"
    if include_news:
        news_symbols = [card.instrument_id for card in scan_result.cards[:limit]] or instrument_ids[:limit]
        catalyst_provider = FreeCatalystProvider()
        news = catalyst_provider.get_news(news_symbols, limit=limit)
        catalyst_hypotheses = build_catalyst_hypotheses(news)
        brief_health["brief_news"] = str(len(news))
        brief_health["brief_news_symbols"] = str(len(news_symbols))
        brief_health["brief_catalysts"] = str(len(catalyst_hypotheses))
        if catalyst_provider.last_errors:
            brief_health["brief_news_errors"] = " | ".join(catalyst_provider.last_errors[:3])

    brief = build_daily_brief(
        provider=mode,
        symbols=instrument_ids,
        scan_result=scan_result,
        backtest_result=backtest_result,
        catalyst_hypotheses=catalyst_hypotheses,
        position_risks=position_risks,
        provider_statuses=build_provider_status(),
        limit=limit,
        data_health=brief_health,
    )
    return brief


@router.get("/backtest")
def backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    limit: int = 100,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        result = run_historical_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            max_signals=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "summary": result.summary.model_dump(mode="json"),
        "performance": [item.model_dump(mode="json") for item in result.performance],
        "signals": [item.model_dump(mode="json") for item in result.signals],
        "data_health": {**result.data_health, **resolved.data_health},
    }


@router.get("/portfolio-backtest")
def portfolio_backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    initial_capital: Decimal = Decimal("100000"),
    risk_per_trade_pct: Decimal = Decimal("1"),
    max_positions: int = 5,
    transaction_cost_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("5"),
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be positive")
    if risk_per_trade_pct <= 0 or risk_per_trade_pct > 10:
        raise HTTPException(status_code=400, detail="risk_per_trade_pct must be between 0 and 10")
    if max_positions <= 0 or max_positions > 20:
        raise HTTPException(status_code=400, detail="max_positions must be between 1 and 20")

    resolved = _resolve_symbols(mode, symbols)
    instrument_ids = resolved.symbols
    start_date, end_date = _backtest_dates(mode, start, end)
    try:
        market_provider = build_market_data_provider(mode)
        result = run_portfolio_backtest(
            instrument_ids=instrument_ids,
            provider=market_provider,
            start=start_date,
            end=end_date,
            step_days=step_days,
            initial_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            max_positions=max_positions,
            transaction_cost_bps=transaction_cost_bps,
            slippage_bps=slippage_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "summary": result.summary.model_dump(mode="json"),
        "trades": [trade.model_dump(mode="json") for trade in result.trades],
        "equity_curve": [point.model_dump(mode="json") for point in result.equity_curve],
        "data_health": {**result.data_health, **resolved.data_health},
    }


def _position_risks(provider: str):
    positions = _repo().list_positions()
    if not positions:
        return []
    market_provider = build_market_data_provider(provider)
    snapshot = market_provider.get_snapshot([position.instrument_id for position in positions])
    latest_prices = {
        row["instrument_id"]: Decimal(str(row["close"]))
        for _, row in snapshot.iterrows()
    }
    risks = []
    for position in positions:
        latest_price = latest_prices.get(position.instrument_id)
        if latest_price is None:
            continue
        risks.append(
            analyze_position_risk(
                PositionInput(**position.model_dump()),
                current_price=latest_price,
            )
        )
    return risks


@router.get("/alerts")
def alerts() -> dict[str, list[object]]:
    return {"alerts": []}


@router.get("/catalysts")
def catalysts(symbols: str | None = None, limit: int = 5) -> dict[str, object]:
    resolved = _resolve_symbols("free", symbols)
    instrument_ids = resolved.symbols[: max(limit, 1)]
    provider = FreeCatalystProvider()
    news = provider.get_news(instrument_ids, limit=limit)
    hypotheses = build_catalyst_hypotheses(news)
    data_health = {
        "provider": "free",
        "scanned": str(len(instrument_ids)),
        "news": str(len(news)),
        "hypotheses": str(len(hypotheses)),
    }
    data_health.update(resolved.data_health)
    if provider.last_errors:
        data_health["errors"] = " | ".join(provider.last_errors[:3])
    return {
        "news": [item.model_dump(mode="json") for item in news],
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "data_health": data_health,
    }


@router.get("/alert-rules")
def alert_rules() -> dict[str, list[object]]:
    return {"rules": [rule.model_dump(mode="json") for rule in _repo().list_alert_rules()]}


@router.post("/alert-rules")
def upsert_alert_rule(rule: AlertRuleCreate) -> dict[str, object]:
    saved = _repo().upsert_alert_rule(rule)
    return saved.model_dump(mode="json")


@router.get("/universes")
def universes() -> dict[str, list[object]]:
    repo = _repo()
    return {
        "universes": [
            universe.model_dump(mode="json")
            for universe in merge_universes(repo.list_custom_universes())
        ]
    }


@router.post("/universes")
def upsert_universe(universe: UniverseCreate) -> dict[str, object]:
    saved = _repo().upsert_universe(universe)
    return saved.model_dump(mode="json")


@router.get("/universes/{universe_id}")
def universe_detail(universe_id: str) -> dict[str, object]:
    repo = _repo()
    custom = repo.get_universe(universe_id)
    if custom is not None:
        return {"universe": custom.model_dump(mode="json")}
    builtin = next(
        (universe for universe in builtin_universes() if universe.universe_id == universe_id),
        None,
    )
    if builtin is None:
        raise HTTPException(status_code=404, detail="universe not found")
    return {"universe": builtin.model_dump(mode="json")}


@router.post("/alerts/evaluate")
def evaluate_alerts(request: AlertEvaluationRequest) -> dict[str, list[object]]:
    prices = {instrument_id: Decimal(price) for instrument_id, price in request.prices.items()}
    rules = [
        AlertRule(
            rule_id=rule.rule_id,
            instrument_id=rule.instrument_id,
            kind=rule.kind,
            operator=rule.operator,
            threshold=rule.threshold,
        )
        for rule in _repo().list_alert_rules()
    ]
    alerts = evaluate_snapshot_alerts(prices, rules)
    return {"alerts": [alert.model_dump(mode="json") for alert in alerts]}


@router.post("/alerts/run")
def run_alerts(
    provider: str = "fixture",
    queue: bool = False,
    recipient: str | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    try:
        market_provider = build_market_data_provider(mode)
        result = run_alert_rules(
            repo=_repo(),
            provider=market_provider,
            queue_delivery=queue,
            recipient=recipient,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "summary": result.summary.model_dump(mode="json"),
        "alerts": [alert.model_dump(mode="json") for alert in result.alerts],
        "latest_prices": {key: str(value) for key, value in result.latest_prices.items()},
        "delivery": result.delivery.model_dump(mode="json") if result.delivery else None,
        "data_health": result.data_health,
    }


@router.get("/alert-suggestions")
def alert_suggestions(limit: int = 50) -> dict[str, list[object]]:
    snapshots = _repo().list_opportunity_snapshots(limit=limit)
    suggestions = suggest_alert_rules(snapshots)
    return {"suggestions": [item.model_dump(mode="json") for item in suggestions]}


@router.get("/scan-runs")
def scan_runs(limit: int = 20) -> dict[str, list[object]]:
    return {"runs": [run.model_dump(mode="json") for run in _repo().list_scan_runs(limit=limit)]}


@router.get("/opportunity-history")
def opportunity_history(
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, list[object]]:
    snapshots = _repo().list_opportunity_snapshots(instrument_id=instrument_id, limit=limit)
    return {"snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots]}


@router.get("/outcomes")
def outcomes(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    return {
        "outcomes": [outcome.model_dump(mode="json") for outcome in replayed],
        "data_health": data_health,
    }


def _replay_outcomes(provider: str, instrument_id: str | None, limit: int):
    repo = _repo()
    snapshots = repo.list_opportunity_snapshots(instrument_id=instrument_id, limit=limit)
    try:
        market_provider = build_market_data_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    replayed = []
    for snapshot in snapshots:
        bars = market_provider.get_daily_bars(
            [snapshot.instrument_id],
            start=date(1900, 1, 1),
            end=date(2100, 1, 1),
        )
        replayed.append(compute_opportunity_outcome(snapshot, bars))
    data_health = {
        "provider": provider,
        "snapshots": str(len(snapshots)),
        "outcomes": str(len(replayed)),
    }
    provider_errors = getattr(market_provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return replayed, data_health


@router.get("/strategy-performance")
def strategy_performance(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    return {
        "performance": [
            item.model_dump(mode="json") for item in summarize_strategy_performance(replayed)
        ],
        "data_health": data_health,
    }


@router.get("/strategy-diagnostics")
def strategy_diagnostics(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    performance = summarize_strategy_performance(replayed)
    diagnostics = diagnose_strategy_performance(performance)
    data_health = {**data_health, "diagnostics": str(len(diagnostics))}
    return {
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        "data_health": data_health,
    }


@router.get("/portfolio")
def portfolio(provider: str = "fixture") -> dict[str, object]:
    positions = _repo().list_positions()
    risks = []
    data_health = {"provider": provider, "positions": str(len(positions)), "risk": "0"}
    if positions:
        try:
            market_provider = build_market_data_provider(provider)
            instrument_ids = [position.instrument_id for position in positions]
            snapshot = market_provider.get_snapshot(instrument_ids)
            latest_prices = {
                row["instrument_id"]: Decimal(str(row["close"]))
                for _, row in snapshot.iterrows()
            }
            for position in positions:
                latest_price = latest_prices.get(position.instrument_id)
                if latest_price is None:
                    continue
                risks.append(
                    analyze_position_risk(
                        PositionInput(**position.model_dump()),
                        current_price=latest_price,
                    )
                )
            data_health["risk"] = str(len(risks))
            provider_errors = getattr(market_provider, "last_errors", [])
            if provider_errors:
                data_health["errors"] = " | ".join(provider_errors[:3])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "positions": [position.model_dump(mode="json") for position in positions],
        "risk": [risk.model_dump(mode="json") for risk in risks],
        "data_health": data_health,
    }


@router.get("/watchlist")
def watchlist() -> dict[str, list[object]]:
    return {"items": [item.model_dump(mode="json") for item in _repo().list_watchlist_items()]}


@router.post("/watchlist")
def upsert_watchlist_item(item: WatchlistCreate) -> dict[str, object]:
    saved = _repo().upsert_watchlist_item(item)
    return saved.model_dump(mode="json")


@router.get("/positions")
def positions() -> dict[str, list[object]]:
    return {"positions": [position.model_dump(mode="json") for position in _repo().list_positions()]}


@router.post("/positions")
def upsert_position(position: PositionCreate) -> dict[str, object]:
    saved = _repo().upsert_position(position)
    return saved.model_dump(mode="json")


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    result, mode, _ = _scan(request.provider, request.symbols)
    selected = None
    if request.instrument_id:
        selected = next((card for card in result.cards if card.instrument_id == request.instrument_id), None)
    if selected is None and result.cards:
        selected = result.cards[0]
    if selected is None:
        return AgentQueryResponse(answer="No opportunity context is available yet.")

    answer = answer_question(
        request.question,
        context={
            "instrument_id": selected.instrument_id,
            "status": selected.status.value,
            "score": selected.score,
            "initial_stop": str(selected.exit_plan.initial_stop),
            "trigger_price": str(selected.entry_plan.trigger_price),
            "target_1": str(selected.exit_plan.target_1),
            "downside_pct": selected.scenario.downside_pct,
            "target_1_pct": selected.scenario.target_1_pct,
            "no_chase_above": str(selected.entry_plan.no_chase_above),
            "signal_summary": _signal_summary(selected),
            "primary_strategy_id": selected.primary_strategy_id,
            "strategy_score": selected.strategy_score,
            "strategy_summary": _strategy_summary(selected),
            "cards": [_agent_card_summary(card) for card in result.cards],
            "provider": mode,
            "data_health": result.data_health,
        },
    )
    return AgentQueryResponse(answer=answer)


def _agent_card_summary(card) -> dict[str, object]:
    decision = card.decision
    return {
        "instrument_id": card.instrument_id,
        "instrument_label": card.instrument_label or format_instrument_label(card.instrument_id),
        "status": card.status.value,
        "score": card.score,
        "rank_score": card.rank_score,
        "factor_score": card.factor_score,
        "factor_rank": card.factor_rank,
        "factor_flags": card.factor_flags,
        "action": decision.action if decision else "watch",
        "conviction_score": decision.conviction_score if decision else None,
        "trigger_price": str(card.entry_plan.trigger_price) if card.entry_plan.trigger_price else None,
        "initial_stop": str(card.exit_plan.initial_stop) if card.exit_plan.initial_stop else None,
        "target_1": str(card.exit_plan.target_1) if card.exit_plan.target_1 else None,
        "target_2": str(card.exit_plan.target_2) if card.exit_plan.target_2 else None,
        "no_chase_above": str(card.entry_plan.no_chase_above) if card.entry_plan.no_chase_above else None,
        "risk_reward": card.risk_reward,
        "primary_strategy_id": card.primary_strategy_id,
        "data_caveats": card.data_caveats,
    }
