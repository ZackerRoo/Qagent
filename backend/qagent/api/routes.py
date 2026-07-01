from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException

from qagent.agent.responder import answer_question
from qagent.api.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    AlertEvaluationRequest,
    PaperSessionStartRequest,
    PaperTradeFromOpportunityRequest,
)
from qagent.backtesting.engine import run_historical_backtest
from qagent.backtesting.portfolio import run_portfolio_backtest
from qagent.briefing.daily import DailyBrief, build_daily_brief
from qagent.briefing.export import render_daily_brief_markdown
from qagent.catalysts.hypotheses import build_catalyst_hypotheses
from qagent.catalysts.providers import FreeCatalystProvider
from qagent.db import create_session_factory, initialize_database
from qagent.domain.models import OpportunityCard, PortfolioPlan, SectorStrength
from qagent.factors.backtest import run_factor_backtest
from qagent.jobs.automation import run_research_automation
from qagent.jobs.automation_scheduler import (
    AutoProcessingCycleResult,
    AutoProcessingSettings,
    AutomationScheduler,
)
from qagent.jobs.daily_scan import DailyScanResult, run_daily_scan
from qagent.jobs.full_market import (
    build_full_market_batch_symbols,
    full_market_batch_cache_key,
    run_full_market_batch_scan_job,
    run_full_market_scan,
    sync_cn_tradable_catalog,
)
from qagent.jobs.alert_runner import run_alert_rules
from qagent.jobs.intraday_check import evaluate_snapshot_alerts
from qagent.jobs.task_manager import TaskManager
from qagent.market.a_share_universe import (
    ResolvedSymbols,
    resolve_symbol_tokens,
)
from qagent.market.instruments import format_instrument_label
from qagent.market.instruments import market_symbol
from qagent.market.rotation_radar import MarketRotationRadar, build_rotation_radar
from qagent.market.tradable import search_cn_tradable_instruments
from qagent.market.universe import DEFAULT_DEV_UNIVERSE, DEFAULT_FREE_UNIVERSE
from qagent.market.universes import UniverseCreate, builtin_universes, merge_universes
from qagent.market.indicators import add_moving_averages, add_volume_ratio, percent_distance
from qagent.monitoring.outcomes import (
    compute_opportunity_outcome,
    diagnose_strategy_performance,
    summarize_recommendation_closure,
    summarize_strategy_performance,
)
from qagent.monitoring.followthrough import build_recommendation_followthrough_center
from qagent.monitoring.recommendation_calibration import (
    build_recommendation_calibration_center,
)
from qagent.monitoring.signal_monitor import SignalMonitorCenter, build_signal_monitor_center
from qagent.monitoring.portfolio import PositionInput, analyze_position_risk
from qagent.monitoring.alerts import AlertRule, suggest_alert_rules
from qagent.paper_trading.engine import (
    build_paper_ledger,
    build_paper_validation,
    seed_paper_trades_from_snapshots,
    update_paper_trades,
    summarize_paper_trades,
)
from qagent.providers.factory import build_market_data_provider
from qagent.providers.status import build_provider_status
from qagent.recommendations.enrichment import enrich_opportunity_card
from qagent.recommendations.portfolio import build_portfolio_plan
from qagent.recommendations.probability import (
    apply_probability_calibration,
    probability_calibration_data_health,
)
from qagent.recommendations.quality_gate import (
    apply_recommendation_quality_gate,
    recommendation_quality_data_health,
)
from qagent.recommendations.rotation import sort_recommendation_cards
from qagent.recommendations.signal_hub import build_signal_hub
from qagent.research.action_center import build_manual_action_center
from qagent.research.alpha_quality import build_alpha_quality_center
from qagent.research.command_center import build_research_command_center
from qagent.research.decision_quality import build_decision_quality_center
from qagent.research.market_intelligence import MarketIntelligenceCenter
from qagent.research.market_intelligence import (
    apply_market_intelligence_to_cards,
    build_market_intelligence_center,
)
from qagent.research.operational_readiness import build_operational_readiness_center
from qagent.storage.paper import PaperAccountSettings, PaperTradingRepository
from qagent.storage.repository import (
    AlertRuleCreate,
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.strategy_data.providers import EmptyStrategyDataProvider
from qagent.strategies.models import StrategyHealth

router = APIRouter()
_task_manager = TaskManager()
_task_executor = ThreadPoolExecutor(max_workers=2)
_automation_scheduler = AutomationScheduler()


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


def _resolve_symbols_with_limit(
    provider_mode: str,
    symbols: str | None,
    scan_limit: int | None = None,
    include_supplements: bool = True,
) -> ResolvedSymbols:
    mode = provider_mode.strip().lower()
    limit = scan_limit
    if mode != "free" or not limit:
        return _resolve_symbols(mode, symbols)
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="scan_limit must be between 1 and 1000")
    parsed = _parse_symbols(symbols, DEFAULT_FREE_UNIVERSE)
    return resolve_symbol_tokens(
        parsed,
        limit=limit,
        include_supplements=include_supplements,
    )


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
    payload = {
        "cards": [card.model_dump(mode="json") for card in result.cards],
        "items": [item.model_dump(mode="json") for item in result.items],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health],
        "factor_rankings": [item.model_dump(mode="json") for item in result.factor_rankings],
        "sector_strength": [item.model_dump(mode="json") for item in result.sector_strength],
        "rotation_radar": _rotation_radar_payload(result.cards, result.sector_strength),
        "portfolio_plan": result.portfolio_plan.model_dump(mode="json"),
        "market_intelligence": result.market_intelligence.model_dump(mode="json")
        if result.market_intelligence
        else None,
        "manual_action_center": result.manual_action_center.model_dump(mode="json")
        if result.manual_action_center
        else None,
        "signal_monitor": result.signal_monitor.model_dump(mode="json")
        if result.signal_monitor
        else None,
        "decision_quality_center": result.decision_quality_center.model_dump(mode="json")
        if result.decision_quality_center
        else None,
        "operational_readiness_center": result.operational_readiness_center.model_dump(mode="json")
        if result.operational_readiness_center
        else None,
        "data_health": result.data_health,
    }
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_live_paper_health_payload(payload)
    payload.pop("operational_readiness_center", None)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _attach_live_paper_health_payload(payload)
    return payload


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
    scan_limit: int | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if forward_days <= 0 or forward_days > 120:
        raise HTTPException(status_code=400, detail="forward_days must be between 1 and 120")
    if step_days <= 0 or step_days > 120:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 120")
    if top_n <= 0 or top_n > 50:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 50")
    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
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
    payload["signals"] = [_attach_instrument_label(signal) for signal in payload.get("signals", [])]
    payload["data_health"].update(resolved.data_health)
    return payload


@router.get("/overview")
def overview(provider: str = "fixture", symbols: str | None = None) -> dict[str, object]:
    result, _, _ = _scan(provider, symbols)
    payload = {
        "market_regime": {
            "US": "development_fixture",
            "CN": "development_fixture",
        },
        "top_cards": [card.model_dump(mode="json") for card in result.cards[:5]],
        "strategy_health": [item.model_dump(mode="json") for item in result.strategy_health[:6]],
        "factor_rankings": [item.model_dump(mode="json") for item in result.factor_rankings[:10]],
        "sector_strength": [item.model_dump(mode="json") for item in result.sector_strength[:6]],
        "rotation_radar": _rotation_radar_payload(result.cards, result.sector_strength),
        "portfolio_plan": result.portfolio_plan.model_dump(mode="json"),
        "market_intelligence": result.market_intelligence.model_dump(mode="json")
        if result.market_intelligence
        else None,
        "manual_action_center": result.manual_action_center.model_dump(mode="json")
        if result.manual_action_center
        else None,
        "signal_monitor": result.signal_monitor.model_dump(mode="json")
        if result.signal_monitor
        else None,
        "decision_quality_center": result.decision_quality_center.model_dump(mode="json")
        if result.decision_quality_center
        else None,
        "operational_readiness_center": result.operational_readiness_center.model_dump(mode="json")
        if result.operational_readiness_center
        else None,
        "data_health": result.data_health,
    }
    _attach_signal_hub_payload(payload, cards_key="top_cards")
    _attach_market_intelligence_payload(payload, cards_key="top_cards")
    _attach_recommendation_quality_payload(payload, cards_key="top_cards")
    _attach_probability_forecast_payload(payload, cards_key="top_cards")
    _attach_manual_action_center_payload(payload, cards_key="top_cards")
    _attach_signal_monitor_payload(payload, cards_key="top_cards")
    _attach_decision_quality_payload(payload, cards_key="top_cards")
    _attach_operational_readiness_payload(payload, cards_key="top_cards")
    _attach_alpha_quality_payload(payload, cards_key="top_cards")
    _attach_research_center_payload(payload, cards_key="top_cards")
    return payload


@router.get("/daily-brief")
def daily_brief(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
    fast: bool = False,
    skip_backtest: bool = False,
    scan_limit: int | None = None,
) -> dict[str, object]:
    if fast:
        skip_backtest = True
        include_news = False
    brief = _build_daily_brief_response(
        provider=provider,
        symbols=symbols,
        limit=limit,
        include_news=include_news,
        skip_backtest=skip_backtest,
        scan_limit=scan_limit,
        fast=fast,
    )
    return brief.model_dump(mode="json")


@router.post("/daily-brief/runs")
def save_daily_brief_run(
    provider: str = "fixture",
    symbols: str | None = None,
    limit: int = 5,
    include_news: bool = True,
    fast: bool = False,
    skip_backtest: bool = False,
    scan_limit: int | None = None,
) -> dict[str, object]:
    if fast:
        skip_backtest = True
        include_news = False
    brief = _build_daily_brief_response(
        provider=provider,
        symbols=symbols,
        limit=limit,
        include_news=include_news,
        skip_backtest=skip_backtest,
        scan_limit=scan_limit,
        fast=fast,
    )
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


@router.get("/automation/scheduler")
def automation_scheduler_state() -> dict[str, object]:
    return _automation_scheduler.state().model_dump(mode="json")


@router.post("/automation/scheduler/run-once")
def run_automation_scheduler_once(
    provider: str = "free",
    symbols: str | None = None,
    interval_seconds: int = 1800,
    include_etfs: bool = True,
    run_scan: bool = True,
    scan_max_age_minutes: int = 240,
    batch_size: int = 200,
    max_symbols: int | None = None,
    sync_if_empty: bool = True,
    seed_paper: bool = True,
    seed_limit: int = 5,
    update_paper: bool = True,
    run_alerts: bool = True,
    queue_alerts: bool = True,
) -> dict[str, object]:
    settings = _auto_processing_settings(
        provider=provider,
        symbols=symbols,
        interval_seconds=interval_seconds,
        include_etfs=include_etfs,
        run_scan=run_scan,
        scan_max_age_minutes=scan_max_age_minutes,
        batch_size=batch_size,
        max_symbols=max_symbols,
        sync_if_empty=sync_if_empty,
        seed_paper=seed_paper,
        seed_limit=seed_limit,
        update_paper=update_paper,
        run_alerts=run_alerts,
        queue_alerts=queue_alerts,
    )
    state = _automation_scheduler.run_once(settings, _run_auto_processing_cycle)
    return state.model_dump(mode="json")


@router.post("/automation/scheduler/start")
def start_automation_scheduler(
    provider: str = "free",
    symbols: str | None = None,
    interval_seconds: int = 1800,
    include_etfs: bool = True,
    run_scan: bool = True,
    scan_max_age_minutes: int = 240,
    batch_size: int = 200,
    max_symbols: int | None = None,
    sync_if_empty: bool = True,
    seed_paper: bool = True,
    seed_limit: int = 5,
    update_paper: bool = True,
    run_alerts: bool = True,
    queue_alerts: bool = True,
) -> dict[str, object]:
    settings = _auto_processing_settings(
        provider=provider,
        symbols=symbols,
        interval_seconds=interval_seconds,
        include_etfs=include_etfs,
        run_scan=run_scan,
        scan_max_age_minutes=scan_max_age_minutes,
        batch_size=batch_size,
        max_symbols=max_symbols,
        sync_if_empty=sync_if_empty,
        seed_paper=seed_paper,
        seed_limit=seed_limit,
        update_paper=update_paper,
        run_alerts=run_alerts,
        queue_alerts=queue_alerts,
    )
    state = _automation_scheduler.start(settings, _run_auto_processing_cycle)
    return state.model_dump(mode="json")


@router.post("/automation/scheduler/stop")
def stop_automation_scheduler() -> dict[str, object]:
    return _automation_scheduler.stop().model_dump(mode="json")


def _auto_processing_settings(
    *,
    provider: str,
    symbols: str | None,
    interval_seconds: int,
    include_etfs: bool,
    run_scan: bool,
    scan_max_age_minutes: int,
    batch_size: int,
    max_symbols: int | None,
    sync_if_empty: bool,
    seed_paper: bool,
    seed_limit: int,
    update_paper: bool,
    run_alerts: bool,
    queue_alerts: bool,
) -> AutoProcessingSettings:
    try:
        return AutoProcessingSettings(
            provider=provider.strip().lower(),
            symbols=symbols,
            interval_seconds=interval_seconds,
            include_etfs=include_etfs,
            run_scan=run_scan,
            scan_max_age_minutes=scan_max_age_minutes,
            batch_size=batch_size,
            max_symbols=max_symbols,
            sync_if_empty=sync_if_empty,
            seed_paper=seed_paper,
            seed_limit=seed_limit,
            update_paper=update_paper,
            run_alerts=run_alerts,
            queue_alerts=queue_alerts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_auto_processing_cycle(settings: AutoProcessingSettings) -> AutoProcessingCycleResult:
    started_at = datetime.now(timezone.utc)
    mode = settings.provider.strip().lower()
    repo = _repo()
    paper_repo = _paper_repo()
    errors: list[str] = []
    data_health: dict[str, str] = {
        "automation_scheduler": "enabled",
        "automation_provider": mode,
        "automation_run_scan": str(settings.run_scan).lower(),
        "automation_seed_paper": str(settings.seed_paper).lower(),
        "automation_update_paper": str(settings.update_paper).lower(),
        "automation_run_alerts": str(settings.run_alerts).lower(),
    }
    scan_status = "disabled"
    scan_started = False
    scan_job_id: str | None = None
    paper_created = 0
    alerts_triggered = 0

    if settings.run_scan:
        try:
            if mode == "fixture":
                resolved = _resolve_symbols(mode, settings.symbols)
                automation = run_research_automation(
                    repo=repo,
                    provider=build_market_data_provider(mode),
                    provider_mode=mode,
                    symbols=resolved.symbols,
                    include_news=False,
                    queue_brief=False,
                    run_alerts=False,
                    run_backtest=False,
                    seed_paper=settings.seed_paper,
                    update_paper=False,
                    limit=settings.seed_limit,
                )
                paper_created += automation.summary.paper_created
                scan_status = "completed"
                data_health.update(automation.data_health)
            else:
                scan_status, scan_started, scan_job_id = _maybe_start_automatic_full_scan(
                    repo,
                    settings,
                )
        except Exception as exc:
            scan_status = "failed"
            errors.append(f"scan: {exc}")

    if settings.seed_paper and mode != "fixture":
        try:
            snapshots = repo.list_opportunity_snapshots(limit=settings.seed_limit)
            seed_result = seed_paper_trades_from_snapshots(paper_repo, snapshots, provider=mode)
            paper_created += seed_result.created
            data_health["automation_seed_snapshots"] = str(len(snapshots))
        except Exception as exc:
            errors.append(f"paper_seed: {exc}")

    paper_total = 0
    paper_closed = 0
    if settings.update_paper:
        try:
            paper_update = update_paper_trades(
                paper_repo,
                provider=build_market_data_provider(mode),
            )
            paper_total = paper_update.summary.total
            paper_closed = paper_update.summary.closed
            data_health.update(paper_update.data_health)
        except Exception as exc:
            errors.append(f"paper_update: {exc}")
            summary = summarize_paper_trades(paper_repo.list_trades(limit=1000))
            paper_total = summary.total
            paper_closed = summary.closed
    else:
        summary = summarize_paper_trades(paper_repo.list_trades(limit=1000))
        paper_total = summary.total
        paper_closed = summary.closed

    if settings.run_alerts:
        try:
            alert_result = run_alert_rules(
                repo=repo,
                provider=build_market_data_provider(mode),
                queue_delivery=settings.queue_alerts,
            )
            alerts_triggered = alert_result.summary.triggered
            data_health.update(alert_result.data_health)
        except Exception as exc:
            errors.append(f"alerts: {exc}")

    finished_at = datetime.now(timezone.utc)
    data_health.update(
        {
            "automation_scan_status": scan_status,
            "automation_scan_started": str(scan_started).lower(),
            "automation_paper_created": str(paper_created),
            "automation_paper_total": str(paper_total),
            "automation_paper_closed": str(paper_closed),
            "automation_alerts_triggered": str(alerts_triggered),
            "automation_errors": str(len(errors)),
        }
    )
    return AutoProcessingCycleResult(
        provider=mode,
        started_at=started_at,
        finished_at=finished_at,
        scan_status=scan_status,
        scan_started=scan_started,
        scan_job_id=scan_job_id,
        paper_created=paper_created,
        paper_total=paper_total,
        paper_closed=paper_closed,
        alerts_triggered=alerts_triggered,
        errors=errors,
        data_health=data_health,
    )


def _maybe_start_automatic_full_scan(
    repo: QagentRepository,
    settings: AutoProcessingSettings,
) -> tuple[str, bool, str | None]:
    mode = settings.provider.strip().lower()
    latest = repo.get_latest_full_market_scan_job(provider=mode)
    if latest and latest.status in {"queued", "running"}:
        return "already_running", False, latest.job_id
    cached = repo.get_recent_scan_result_cache(
        cache_key=full_market_batch_cache_key(mode, settings.include_etfs),
        max_age=timedelta(minutes=settings.scan_max_age_minutes),
    )
    if cached is not None:
        return "cache_fresh", False, latest.job_id if latest else None

    summary = repo.tradable_catalog_summary()
    if settings.sync_if_empty and summary.total_count == 0:
        sync_cn_tradable_catalog(repo=repo, include_full_etfs=settings.include_etfs)
    symbols = build_full_market_batch_symbols(
        repo=repo,
        include_etfs=settings.include_etfs,
        max_symbols=settings.max_symbols,
    )
    if not symbols:
        raise ValueError("tradable catalog is empty")
    job = repo.create_full_market_scan_job(
        provider=mode,
        symbols=symbols,
        batch_size=settings.batch_size,
        include_etfs=settings.include_etfs,
        sync_if_empty=settings.sync_if_empty,
    )
    _task_executor.submit(run_full_market_batch_scan_job, job.job_id)
    return "queued", True, job.job_id


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


@router.get("/paper-trades/session")
def paper_trade_session() -> dict[str, object]:
    repo = _paper_repo()
    account = repo.get_account_settings()
    trades = repo.list_trades(limit=1000)
    return {
        "account": account.model_dump(mode="json"),
        "summary": summarize_paper_trades(trades).model_dump(mode="json"),
        "data_health": _paper_account_data_health(account),
    }


@router.post("/paper-trades/session/start")
def start_paper_trade_session(request: PaperSessionStartRequest) -> dict[str, object]:
    repo = _paper_repo()
    initial_capital = _decimal_or_none(request.initial_capital) or Decimal("0")
    allocation_per_trade_pct = _decimal_or_none(request.allocation_per_trade_pct) or Decimal("0")
    transaction_cost_bps = _decimal_or_none(request.transaction_cost_bps) or Decimal("-1")
    slippage_bps = _decimal_or_none(request.slippage_bps) or Decimal("-1")
    take_profit_pct = _decimal_or_none(request.take_profit_pct) or Decimal("0")
    _validate_paper_account_inputs(
        initial_capital=initial_capital,
        allocation_per_trade_pct=allocation_per_trade_pct,
        max_positions=request.max_positions,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        take_profit_pct=take_profit_pct,
    )
    cleared = repo.clear_trades() if request.reset_existing else 0
    account = repo.start_account_session(
        label=request.label.strip() or "A股正式模拟盘",
        initial_capital=initial_capital,
        allocation_per_trade_pct=allocation_per_trade_pct,
        max_positions=request.max_positions,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        take_profit_pct=take_profit_pct,
    )
    ledger = build_paper_ledger(
        repo.list_trades(limit=1000),
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
    )
    ledger.data_health.update(_paper_account_data_health(account))
    return {
        "account": account.model_dump(mode="json"),
        "cleared_trades": cleared,
        "ledger": ledger.model_dump(mode="json"),
    }


@router.get("/paper-trades/ledger")
def paper_trade_ledger(
    initial_capital: Decimal | None = None,
    allocation_per_trade_pct: Decimal | None = None,
    max_positions: int | None = None,
    transaction_cost_bps: Decimal | None = None,
    slippage_bps: Decimal | None = None,
    take_profit_pct: Decimal | None = None,
    limit: int = 500,
) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    account = _paper_repo().get_account_settings()
    try:
        ledger = build_paper_ledger(
            _paper_repo().list_trades(limit=limit),
            initial_capital=initial_capital or account.initial_capital,
            allocation_per_trade_pct=allocation_per_trade_pct or account.allocation_per_trade_pct,
            max_positions=max_positions or account.max_positions,
            transaction_cost_bps=transaction_cost_bps or account.transaction_cost_bps,
            slippage_bps=slippage_bps or account.slippage_bps,
            take_profit_pct=take_profit_pct or account.take_profit_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ledger.data_health.update(_paper_account_data_health(account))
    return ledger.model_dump(mode="json")


@router.get("/paper-trades/validation")
def paper_trade_validation(limit: int = 500) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    return _paper_validation_payload(limit=limit)


@router.post("/paper-trades/validation/run")
def run_paper_trade_validation(provider: str = "fixture", limit: int = 500) -> dict[str, object]:
    if limit <= 0 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    mode = provider.strip().lower()
    try:
        update_result = update_paper_trades(
            _paper_repo(),
            provider=build_market_data_provider(mode),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _paper_validation_payload(limit=limit)
    payload["data_health"].update(
        {
            **update_result.data_health,
            "validation_refreshed": "true",
            "validation_provider": mode,
        }
    )
    return payload


def _paper_validation_payload(limit: int = 500) -> dict[str, object]:
    repo = _paper_repo()
    account = repo.get_account_settings()
    trades = repo.list_trades(limit=limit)
    ledger = build_paper_ledger(
        trades,
        initial_capital=account.initial_capital,
        allocation_per_trade_pct=account.allocation_per_trade_pct,
        max_positions=account.max_positions,
        transaction_cost_bps=account.transaction_cost_bps,
        slippage_bps=account.slippage_bps,
        take_profit_pct=account.take_profit_pct,
    )
    ledger.data_health.update(_paper_account_data_health(account))
    validation = build_paper_validation(trades, ledger)
    return validation.model_dump(mode="json")


@router.delete("/paper-trades/{trade_id}")
def delete_paper_trade(trade_id: str) -> dict[str, object]:
    deleted = _paper_repo().delete_trade(trade_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="paper trade not found")
    return {"deleted": True, "trade_id": trade_id}


@router.post("/paper-trades/from-opportunity")
def create_paper_trade_from_opportunity(
    request: PaperTradeFromOpportunityRequest,
) -> dict[str, object]:
    if request.risk_status == "blocked" or request.action == "avoid":
        raise HTTPException(status_code=400, detail="opportunity is blocked")
    if not request.instrument_id.strip():
        raise HTTPException(status_code=400, detail="instrument_id is required")
    if not request.trigger_price:
        raise HTTPException(status_code=400, detail="trigger_price is required")

    source_snapshot_id = f"opportunity:{request.card_id.strip()}"
    repo = _paper_repo()
    existing = repo.get_trade_by_source_snapshot_id(source_snapshot_id)
    if existing is not None:
        return {
            "created": False,
            "trade": existing.model_dump(mode="json"),
            "message": "already_tracking",
        }

    trade = repo.create_trade(
        source_snapshot_id=source_snapshot_id,
        provider=request.provider.strip().lower(),
        instrument_id=request.instrument_id.strip(),
        strategy_id=request.strategy_id,
        signal_date=date.today(),
        trigger_price=Decimal(str(request.trigger_price)),
        initial_stop=_decimal_or_none(request.initial_stop),
        target_1=_decimal_or_none(request.target_1),
        rank_score=_decimal_or_none(request.rank_score),
        notes="从机会卡加入模拟跟踪；等待触发价确认后才视为开仓。",
    )
    return {
        "created": True,
        "trade": trade.model_dump(mode="json"),
        "message": "tracking_created",
    }


def _decimal_or_none(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def _validate_paper_account_inputs(
    *,
    initial_capital: Decimal,
    allocation_per_trade_pct: Decimal,
    max_positions: int,
    transaction_cost_bps: Decimal,
    slippage_bps: Decimal,
    take_profit_pct: Decimal,
) -> None:
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be greater than zero")
    if allocation_per_trade_pct <= 0 or allocation_per_trade_pct > 100:
        raise HTTPException(
            status_code=400,
            detail="allocation_per_trade_pct must be between 0 and 100",
        )
    if max_positions <= 0:
        raise HTTPException(status_code=400, detail="max_positions must be greater than zero")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise HTTPException(
            status_code=400,
            detail="transaction_cost_bps and slippage_bps must be non-negative",
        )
    if take_profit_pct <= 0 or take_profit_pct > 100:
        raise HTTPException(status_code=400, detail="take_profit_pct must be between 0 and 100")


def _paper_account_data_health(account: PaperAccountSettings) -> dict[str, str]:
    return {
        "paper_session_id": account.session_id,
        "paper_session_status": account.status,
        "paper_session_label": account.label,
        "paper_initial_capital": str(account.initial_capital),
        "paper_allocation_per_trade_pct": str(account.allocation_per_trade_pct),
        "paper_max_positions": str(account.max_positions),
        "paper_transaction_cost_bps": str(account.transaction_cost_bps),
        "paper_slippage_bps": str(account.slippage_bps),
        "paper_take_profit_pct": str(account.take_profit_pct),
    }


def _build_daily_brief_response(
    provider: str,
    symbols: str | None,
    limit: int,
    include_news: bool,
    skip_backtest: bool,
    scan_limit: int | None,
    fast: bool = False,
):
    mode = provider.strip().lower()
    if limit <= 0 or limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    if scan_limit is None and fast:
        scan_limit = 80 if mode == "free" else None
    if fast:
        cached = _cached_daily_scan_for_brief(mode)
        if cached is not None:
            scan_result, cached_symbols, cached_health = cached
            brief_health = {
                "brief_provider": mode,
                "brief_symbols": str(len(cached_symbols)),
                "brief_requested_symbols": symbols or "",
                "brief_news": "skipped",
                "brief_mode": "fast",
                "brief_scan_limit": "cache",
                "brief_backtest": "skipped",
                "brief_skip_backtest": "true",
                **cached_health,
            }
            return build_daily_brief(
                provider=mode,
                symbols=cached_symbols,
                scan_result=scan_result,
                backtest_result=None,
                catalyst_hypotheses=[],
                position_risks=[],
                provider_statuses=build_provider_status(),
                limit=limit,
                data_health=brief_health,
            )

    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=not fast,
    )
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
        if skip_backtest:
            backtest_result = None
        else:
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
        "brief_mode": "fast" if fast else "full",
        "brief_scan_limit": str(scan_limit) if scan_limit else "default",
        "brief_backtest": "skipped" if skip_backtest else "run",
        "brief_skip_backtest": str(skip_backtest).lower(),
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


def _cached_daily_scan_for_brief(
    mode: str,
) -> tuple[DailyScanResult, list[str], dict[str, str]] | None:
    repo = _repo()
    cached = repo.get_latest_scan_result_cache_by_modes(
        provider=mode,
        modes={"full_market_scan", "today_scan_fallback", "full_market_batch"},
        max_age=timedelta(days=7),
    )
    if cached is None:
        return None
    payload = deepcopy(cached.payload)
    _hydrate_full_market_batch_payload(payload, repo, mode, cache_ttl_minutes=7 * 24 * 60)
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        return None
    normalized = {
        "cards": cards,
        "items": payload.get("items") if isinstance(payload.get("items"), list) else [],
        "strategy_health": payload.get("strategy_health")
        if isinstance(payload.get("strategy_health"), list)
        else [],
        "factor_rankings": payload.get("factor_rankings")
        if isinstance(payload.get("factor_rankings"), list)
        else [],
        "sector_strength": payload.get("sector_strength")
        if isinstance(payload.get("sector_strength"), list)
        else [],
        "market_intelligence": payload.get("market_intelligence")
        if isinstance(payload.get("market_intelligence"), dict)
        else None,
        "manual_action_center": payload.get("manual_action_center")
        if isinstance(payload.get("manual_action_center"), dict)
        else None,
        "portfolio_plan": payload.get("portfolio_plan"),
        "data_health": payload.get("data_health") if isinstance(payload.get("data_health"), dict) else {},
    }
    if not isinstance(normalized["portfolio_plan"], dict):
        normalized["portfolio_plan"] = build_portfolio_plan(
            [OpportunityCard.model_validate(card) for card in cards if isinstance(card, dict)]
        ).model_dump(mode="json")
    scan_result = DailyScanResult.model_validate(normalized)
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list):
        raw_symbols = cached.symbols
    symbols = [str(symbol) for symbol in raw_symbols if str(symbol)]
    cache_health = {
        "brief_cache": "hit",
        "brief_cache_id": cached.cache_id,
        "brief_cache_mode": cached.mode,
        "brief_cache_created_at": cached.created_at.isoformat(),
    }
    return scan_result, symbols, cache_health


@router.get("/backtest")
def backtest(
    provider: str = "fixture",
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 5,
    limit: int = 100,
    scan_limit: int | None = None,
) -> dict[str, object]:
    mode = provider.strip().lower()
    if step_days <= 0 or step_days > 60:
        raise HTTPException(status_code=400, detail="step_days must be between 1 and 60")
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
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
        "signals": [_model_payload_with_label(item) for item in result.signals],
        "benchmark": result.benchmark.model_dump(mode="json"),
        "environment_breakdown": [
            item.model_dump(mode="json") for item in result.environment_breakdown
        ],
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
    scan_limit: int | None = None,
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

    resolved = _resolve_symbols_with_limit(
        mode,
        symbols,
        scan_limit,
        include_supplements=scan_limit is None,
    )
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
        "trades": [_model_payload_with_label(trade) for trade in result.trades],
        "equity_curve": [point.model_dump(mode="json") for point in result.equity_curve],
        "monthly_returns": [item.model_dump(mode="json") for item in result.monthly_returns],
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


@router.get("/instruments/search")
def instrument_search(q: str = "", limit: int = 50) -> dict[str, object]:
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    catalog = search_cn_tradable_instruments(
        q,
        limit=limit,
        include_full_etfs=False,
        use_cache=True,
    )
    return {
        "items": [item.model_dump(mode="json") for item in catalog.items],
        "data_health": catalog.data_health,
    }


@router.post("/tradable-catalog/sync")
def sync_tradable_catalog(include_full_etfs: bool = True) -> dict[str, object]:
    result = sync_cn_tradable_catalog(repo=_repo(), include_full_etfs=include_full_etfs)
    return result.model_dump(mode="json")


@router.get("/tradable-catalog")
def tradable_catalog(
    q: str = "",
    asset_type: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    result = _repo().search_tradable_instruments(q, asset_type=asset_type, limit=limit)
    return result.model_dump(mode="json")


@router.get("/instruments/labels")
def instrument_labels(symbols: str = "") -> dict[str, object]:
    requested = _normalize_symbol_list(symbols)
    if requested:
        labels: dict[str, str] = {}
        for symbol in requested:
            if symbol in labels:
                continue
            labels[symbol] = format_instrument_label(symbol)
        return {
            "labels": labels,
            "data_health": {"requested": str(len(labels))},
        }

    # Return full tradable map in one shot for UI label hydration.
    instruments = _repo().list_tradable_instruments(limit=20_000)
    labels = {item.instrument_id: item.label for item in instruments}
    if not labels:
        from qagent.market.tradable import load_cn_tradable_instruments

        catalog = load_cn_tradable_instruments(use_cache=True)
        labels = {f"CN:{item.symbol}": item.label for item in catalog.items}
    return {
        "labels": labels,
        "data_health": {"requested": str(len(labels))},
    }


@router.post("/full-market/scan")
def full_market_scan(
    provider: str = "free",
    max_symbols: int = 300,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
) -> dict[str, object]:
    return _full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty)


@router.post("/full-market/batch-scan")
def start_full_market_batch_scan(
    provider: str = "free",
    batch_size: int = 200,
    max_symbols: int | None = None,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
    force_restart: bool = False,
) -> dict[str, object]:
    mode = provider.strip().lower()
    _validate_full_market_batch_scan_params(batch_size, max_symbols)
    repo = _repo()
    latest = repo.get_latest_full_market_scan_job(provider=mode)
    if latest and latest.status in {"queued", "running"} and not force_restart:
        return _full_market_job_payload(latest)

    summary = repo.tradable_catalog_summary()
    if sync_if_empty and summary.total_count == 0:
        sync_cn_tradable_catalog(repo=repo, include_full_etfs=include_etfs)
    symbols = build_full_market_batch_symbols(
        repo=repo,
        include_etfs=include_etfs,
        max_symbols=max_symbols,
    )
    if not symbols:
        raise HTTPException(status_code=400, detail="tradable catalog is empty")
    job = repo.create_full_market_scan_job(
        provider=mode,
        symbols=symbols,
        batch_size=batch_size,
        include_etfs=include_etfs,
        sync_if_empty=sync_if_empty,
    )
    _task_executor.submit(run_full_market_batch_scan_job, job.job_id)
    return _full_market_job_payload(job)


def _normalize_symbol_list(raw: str) -> list[str]:
    if not raw.strip():
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        normalized = value.strip().upper()
        if not normalized:
            continue
        if normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


@router.get("/full-market/batch-scan/latest")
def latest_full_market_batch_scan(provider: str = "free") -> dict[str, object]:
    job = _repo().get_latest_full_market_scan_job(provider=provider.strip().lower())
    if job is None:
        raise HTTPException(status_code=404, detail="full-market batch scan not found")
    return _full_market_job_payload(job)


@router.get("/full-market/batch-scan/latest-result")
def latest_full_market_batch_scan_result(
    provider: str = "free",
    include_etfs: bool = True,
    cache_ttl_minutes: int = 7 * 24 * 60,
) -> dict[str, object]:
    _validate_scan_cache_ttl(cache_ttl_minutes)
    mode = provider.strip().lower()
    repo = _repo()
    cached = repo.get_recent_scan_result_cache(
        cache_key=full_market_batch_cache_key(provider, include_etfs),
        max_age=timedelta(minutes=cache_ttl_minutes),
    )
    if cached is None:
        raise HTTPException(status_code=404, detail="full-market batch result not found")
    payload = deepcopy(cached.payload)
    _hydrate_full_market_batch_payload(payload, repo, mode, cache_ttl_minutes)
    data_health = payload.setdefault("data_health", {})
    if isinstance(data_health, dict):
        data_health["scan_result_cache"] = "hit"
        data_health["scan_result_cache_id"] = cached.cache_id
    return payload


@router.get("/full-market/batch-scan/{job_id}")
def full_market_batch_scan_job(job_id: str) -> dict[str, object]:
    job = _repo().get_full_market_scan_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="full-market batch scan not found")
    return _full_market_job_payload(job)


@router.post("/scan-tasks/today")
def start_today_scan_task(
    provider: str = "free",
    max_symbols: int = 80,
    include_etfs: bool = True,
    sync_if_empty: bool = True,
    force_refresh: bool = False,
    cache_ttl_minutes: int = 60,
) -> dict[str, object]:
    _validate_full_market_scan_params(max_symbols)
    _validate_scan_cache_ttl(cache_ttl_minutes)
    record = _task_manager.create(
        kind="today_scan",
        message=f"Queued today scan for up to {max_symbols} symbols",
    )
    if not force_refresh:
        cached_payload = _recent_full_market_scan_payload(
            provider=provider,
            max_symbols=max_symbols,
            include_etfs=include_etfs,
            sync_if_empty=sync_if_empty,
            cache_ttl_minutes=cache_ttl_minutes,
        )
        if cached_payload is not None:
            cached_payload["task"] = _task_payload(
                record.task_id,
                record.kind,
                provider,
                max_symbols,
                include_etfs,
                cache="hit",
            )
            _task_manager.mark_succeeded(
                record.task_id,
                cached_payload,
                message="Loaded recent SQLite scan snapshot",
            )
            cached_record = _task_manager.get(record.task_id)
            return (cached_record or record).model_dump(mode="json")

    def work() -> dict[str, object]:
        _task_manager.update(
            record.task_id,
            progress=15,
            message="Building tradable A-share universe",
        )
        payload = _full_market_scan_payload(provider, max_symbols, include_etfs, sync_if_empty)
        payload.setdefault("data_health", {})["scan_result_cache"] = (
            "force_refresh" if force_refresh else "miss"
        )
        payload["task"] = _task_payload(
            record.task_id,
            record.kind,
            provider,
            max_symbols,
            include_etfs,
            cache="refresh" if force_refresh else "miss",
        )
        return payload

    _task_executor.submit(_task_manager.run, record.task_id, work)
    return record.model_dump(mode="json")


@router.get("/scan-tasks")
def scan_tasks(limit: int = 20) -> dict[str, list[object]]:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    tasks = [
        _enrich_scan_task_result(record.model_dump(mode="json"))
        for record in _task_manager.list(limit)
    ]
    return {"tasks": tasks}


@router.get("/scan-tasks/{task_id}")
def scan_task(task_id: str) -> dict[str, object]:
    record = _task_manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="scan task not found")
    return _enrich_scan_task_result(record.model_dump(mode="json"))


def _enrich_scan_task_result(payload: dict[str, object]) -> dict[str, object]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    _relabel_instrument_payload(result)
    _hydrate_legacy_opportunity_cards(result)
    _attach_rotation_radar_payload(result)
    _attach_signal_hub_payload(result)
    _attach_market_intelligence_payload(result)
    _attach_recommendation_quality_payload(result)
    _attach_probability_forecast_payload(result)
    _attach_manual_action_center_payload(result)
    _attach_signal_monitor_payload(result)
    _attach_decision_quality_payload(result)
    _attach_operational_readiness_payload(result)
    _attach_alpha_quality_payload(result)
    _attach_research_center_payload(result)
    return payload


def _full_market_scan_payload(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
) -> dict[str, object]:
    _validate_full_market_scan_params(max_symbols)
    mode = provider.strip().lower()
    result = run_full_market_scan(
        repo=_repo(),
        provider_mode=mode,
        max_symbols=max_symbols,
        include_etfs=include_etfs,
        sync_if_empty=sync_if_empty,
    )
    _repo().save_scan_run(provider=mode, mode=mode, symbols=result.symbols, result=result.scan)
    payload = {
        "symbols": result.symbols,
        "cards": [card.model_dump(mode="json") for card in result.scan.cards],
        "items": [item.model_dump(mode="json") for item in result.scan.items],
        "strategy_health": [item.model_dump(mode="json") for item in result.scan.strategy_health],
        "factor_rankings": [item.model_dump(mode="json") for item in result.scan.factor_rankings],
        "sector_strength": [item.model_dump(mode="json") for item in result.scan.sector_strength],
        "rotation_radar": _rotation_radar_payload(
            result.scan.cards,
            result.scan.sector_strength,
        ),
        "portfolio_plan": result.scan.portfolio_plan.model_dump(mode="json"),
        "market_intelligence": result.scan.market_intelligence.model_dump(mode="json")
        if result.scan.market_intelligence
        else None,
        "manual_action_center": result.scan.manual_action_center.model_dump(mode="json")
        if result.scan.manual_action_center
        else None,
        "signal_monitor": result.scan.signal_monitor.model_dump(mode="json")
        if result.scan.signal_monitor
        else None,
        "decision_quality_center": result.scan.decision_quality_center.model_dump(mode="json")
        if result.scan.decision_quality_center
        else None,
        "operational_readiness_center": result.scan.operational_readiness_center.model_dump(mode="json")
        if result.scan.operational_readiness_center
        else None,
        "data_health": result.data_health,
    }
    _relabel_instrument_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_live_paper_health_payload(payload)
    payload.pop("operational_readiness_center", None)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _attach_live_paper_health_payload(payload)
    _repo().save_scan_result_cache(
        cache_key=_full_market_scan_cache_key(mode, max_symbols, include_etfs, sync_if_empty),
        provider=mode,
        mode="full_market_scan",
        symbols=result.symbols,
        payload=payload,
    )
    return payload


def _validate_full_market_scan_params(max_symbols: int) -> None:
    if max_symbols <= 0 or max_symbols > 1000:
        raise HTTPException(status_code=400, detail="max_symbols must be between 1 and 1000")


def _validate_scan_cache_ttl(cache_ttl_minutes: int) -> None:
    if cache_ttl_minutes < 0 or cache_ttl_minutes > 7 * 24 * 60:
        raise HTTPException(status_code=400, detail="cache_ttl_minutes must be between 0 and 10080")


def _validate_full_market_batch_scan_params(
    batch_size: int,
    max_symbols: int | None,
) -> None:
    if batch_size <= 0 or batch_size > 500:
        raise HTTPException(status_code=400, detail="batch_size must be between 1 and 500")
    if max_symbols is not None and (max_symbols <= 0 or max_symbols > 20_000):
        raise HTTPException(status_code=400, detail="max_symbols must be between 1 and 20000")


def _full_market_job_payload(job) -> dict[str, object]:
    payload = job.model_dump(mode="json")
    symbols = payload.pop("symbols", [])
    payload["progress"] = job.progress
    payload["symbols_preview"] = symbols[:20]
    return payload


def _recent_full_market_scan_payload(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
    cache_ttl_minutes: int,
) -> dict[str, object] | None:
    if cache_ttl_minutes == 0:
        return None
    mode = provider.strip().lower()
    cache_key = _full_market_scan_cache_key(mode, max_symbols, include_etfs, sync_if_empty)
    cached = _repo().get_recent_scan_result_cache(
        cache_key=cache_key,
        max_age=timedelta(minutes=cache_ttl_minutes),
    )
    if cached is not None:
        payload = deepcopy(cached.payload)
        _relabel_instrument_payload(payload)
        _attach_rotation_radar_payload(payload)
        _attach_signal_hub_payload(payload)
        _attach_market_intelligence_payload(payload)
        _attach_recommendation_quality_payload(payload)
        _attach_probability_forecast_payload(payload)
        data_health = payload.setdefault("data_health", {})
        if isinstance(data_health, dict):
            data_health["scan_result_cache"] = "hit"
            data_health["scan_result_cache_key"] = cache_key
            data_health["scan_result_cache_id"] = cached.cache_id
        _attach_manual_action_center_payload(payload)
        _attach_signal_monitor_payload(payload)
        _attach_decision_quality_payload(payload)
        _attach_operational_readiness_payload(payload)
        _attach_alpha_quality_payload(payload)
        _attach_research_center_payload(payload)
        return payload

    payload = _recent_scan_run_fallback_payload(
        provider=mode,
        max_symbols=max_symbols,
        include_etfs=include_etfs,
        sync_if_empty=sync_if_empty,
        cache_ttl_minutes=cache_ttl_minutes,
    )
    if payload is None:
        return None
    _relabel_instrument_payload(payload)
    _attach_rotation_radar_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _repo().save_scan_result_cache(
        cache_key=cache_key,
        provider=mode,
        mode="today_scan_fallback",
        symbols=[str(symbol) for symbol in payload.get("symbols", [])],
        payload=payload,
    )
    return payload


def _recent_scan_run_fallback_payload(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
    cache_ttl_minutes: int,
) -> dict[str, object] | None:
    cache_key = _full_market_scan_cache_key(provider, max_symbols, include_etfs, sync_if_empty)
    bundle = _repo().get_recent_scan_run_with_snapshots(
        provider=provider,
        scanned=max_symbols,
        max_age=timedelta(minutes=cache_ttl_minutes),
    )
    if bundle is None:
        return None
    cards = [snapshot.card for snapshot in bundle.snapshots]
    portfolio_plan = build_portfolio_plan(
        [OpportunityCard.model_validate(card) for card in cards]
    ).model_dump(mode="json")
    data_health = {
        **bundle.run.data_health,
        "scan_result_cache": "scan_run_fallback",
        "scan_result_cache_key": cache_key,
        "scan_result_source_run": bundle.run.run_id,
        "full_market_requested": str(max_symbols),
        "full_market_include_etfs": str(include_etfs).lower(),
        "reconstructed_items": "false",
    }
    payload = {
        "symbols": bundle.run.symbols,
        "cards": cards,
        "items": [],
        "strategy_health": [],
        "factor_rankings": [],
        "sector_strength": [],
        "portfolio_plan": portfolio_plan,
        "data_health": data_health,
    }
    _attach_rotation_radar_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    return payload


def _hydrate_full_market_batch_payload(
    payload: dict[str, object],
    repo: QagentRepository,
    provider: str,
    cache_ttl_minutes: int,
) -> None:
    _relabel_instrument_payload(payload)
    data_health = payload.setdefault("data_health", {})
    if not isinstance(data_health, dict):
        data_health = {}
        payload["data_health"] = data_health
    hydrated_cards = _hydrate_legacy_opportunity_cards(payload)
    if hydrated_cards:
        data_health["legacy_cards_hydrated"] = str(hydrated_cards)
    if not payload.get("strategy_health"):
        recent = repo.get_latest_scan_result_cache_by_modes(
            provider=provider,
            modes={"full_market_scan", "today_scan_fallback"},
            max_age=timedelta(minutes=cache_ttl_minutes),
        )
        if recent and isinstance(recent.payload.get("strategy_health"), list):
            strategy_health = recent.payload.get("strategy_health", [])
            if strategy_health:
                payload["strategy_health"] = deepcopy(strategy_health)
                data_health["strategy_health_source"] = "recent_scan_cache"
                data_health["strategy_health_source_cache_id"] = recent.cache_id
    if not payload.get("strategy_health"):
        strategy_health = _strategy_health_from_card_calibration(payload)
        if strategy_health:
            payload["strategy_health"] = strategy_health
            data_health["strategy_health_source"] = "card_strategy_calibration"
    _attach_rotation_radar_payload(payload)
    _attach_signal_hub_payload(payload)
    _attach_market_intelligence_payload(payload)
    _attach_recommendation_quality_payload(payload)
    _attach_probability_forecast_payload(payload)
    _attach_manual_action_center_payload(payload)
    _attach_signal_monitor_payload(payload)
    _attach_decision_quality_payload(payload)
    _attach_live_paper_health_payload(payload)
    payload.pop("operational_readiness_center", None)
    _attach_operational_readiness_payload(payload)
    _attach_alpha_quality_payload(payload)
    _attach_research_center_payload(payload)
    _attach_live_paper_health_payload(payload)


def _hydrate_legacy_opportunity_cards(payload: dict[str, object]) -> int:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return 0
    hydrated: list[object] = []
    hydrated_count = 0
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            hydrated.append(raw_card)
            continue
        if raw_card.get("confidence_explanation") and raw_card.get("execution_plan"):
            hydrated.append(raw_card)
            continue
        try:
            card = OpportunityCard.model_validate(raw_card)
        except Exception:
            hydrated.append(raw_card)
            continue
        enrich_opportunity_card(card)
        if _should_refresh_instrument_label(card.instrument_id, card.instrument_label):
            card.instrument_label = format_instrument_label(card.instrument_id)
        hydrated.append(card.model_dump(mode="json"))
        hydrated_count += 1
    if hydrated_count:
        payload["cards"] = hydrated
    return hydrated_count


def _rotation_radar_payload(
    cards: list[OpportunityCard],
    sector_strength: list[SectorStrength] | None = None,
) -> dict[str, object]:
    return build_rotation_radar(cards, sector_strength or []).model_dump(mode="json")


def _attach_rotation_radar_payload(payload: dict[str, object]) -> None:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        payload["rotation_radar"] = _rotation_radar_payload([])
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue

    sectors: list[SectorStrength] = []
    raw_sectors = payload.get("sector_strength")
    if isinstance(raw_sectors, list):
        for raw_sector in raw_sectors:
            if not isinstance(raw_sector, dict):
                continue
            try:
                sectors.append(SectorStrength.model_validate(raw_sector))
            except Exception:
                continue

    payload["rotation_radar"] = _rotation_radar_payload(cards, sectors)


def _attach_signal_hub_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    enriched_cards: list[object] = []
    rotation = payload.get("rotation_radar")
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            enriched_cards.append(raw_card)
            continue
        try:
            card = OpportunityCard.model_validate(raw_card)
        except Exception:
            enriched_cards.append(raw_card)
            continue
        rotation_score, rotation_name = _card_rotation_score(card, rotation)
        card.signal_hub = build_signal_hub(
            card,
            rotation_score=rotation_score,
            rotation_name=rotation_name,
        )
        enriched_cards.append(card.model_dump(mode="json"))
    payload[cards_key] = enriched_cards


def _attach_market_intelligence_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("market_intelligence"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    raw_health = payload.get("strategy_health")
    strategy_health: list[StrategyHealth] = []
    if isinstance(raw_health, list):
        for item in raw_health:
            if not isinstance(item, dict):
                continue
            try:
                strategy_health.append(StrategyHealth.model_validate(item))
            except Exception:
                continue

    raw_data_health = payload.get("data_health")
    data_health = raw_data_health if isinstance(raw_data_health, dict) else {}
    raw_items = payload.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    center = build_market_intelligence_center(
        cards=cards,
        items=items,
        bars_by_instrument={},
        strategy_health=strategy_health,
        data_health=data_health,
    )
    apply_market_intelligence_to_cards(cards, center)
    payload[cards_key] = [card.model_dump(mode="json") for card in cards]
    payload["market_intelligence"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_recommendation_quality_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    if raw_cards and all(
        isinstance(raw_card, dict) and isinstance(raw_card.get("recommendation_quality"), dict)
        for raw_card in raw_cards
    ):
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    apply_recommendation_quality_gate(cards)
    cards = sort_recommendation_cards(cards)
    payload[cards_key] = [card.model_dump(mode="json") for card in cards]
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(recommendation_quality_data_health(cards))


def _attach_probability_forecast_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    cards = _cards_from_payload(raw_cards)
    if not cards:
        return
    if raw_cards and all(
        isinstance(raw_card, dict) and isinstance(raw_card.get("probability_forecast"), dict)
        for raw_card in raw_cards
    ):
        payload_data_health = payload.setdefault("data_health", {})
        if isinstance(payload_data_health, dict):
            payload_data_health.update(probability_calibration_data_health(cards))
        return

    apply_probability_calibration(cards, _strategy_health_from_payload(payload.get("strategy_health")))
    cards = sort_recommendation_cards(cards)
    payload[cards_key] = [card.model_dump(mode="json") for card in cards]
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(probability_calibration_data_health(cards))


def _attach_manual_action_center_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("manual_action_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    raw_health = payload.get("strategy_health")
    strategy_health: list[StrategyHealth] = []
    if isinstance(raw_health, list):
        for item in raw_health:
            if not isinstance(item, dict):
                continue
            try:
                strategy_health.append(StrategyHealth.model_validate(item))
            except Exception:
                continue

    raw_data_health = payload.get("data_health")
    data_health = raw_data_health if isinstance(raw_data_health, dict) else {}
    center = build_manual_action_center(
        cards=cards,
        market_intelligence=payload.get("market_intelligence")
        if isinstance(payload.get("market_intelligence"), dict)
        else None,
        strategy_health=strategy_health,
        data_health=data_health,
    )
    payload["manual_action_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_signal_monitor_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("signal_monitor"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    if not cards:
        return

    provider = _payload_provider_mode(payload)
    bars_by_instrument = _cached_latest_bars_by_instrument(provider, cards)
    center = build_signal_monitor_center(cards, bars_by_instrument=bars_by_instrument)
    payload["signal_monitor"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)
        if provider:
            payload_data_health["signal_monitor_price_source"] = "market_cache"
            payload_data_health["signal_monitor_cached_bars"] = str(len(bars_by_instrument))


def _payload_provider_mode(payload: dict[str, object]) -> str | None:
    data_health = payload.get("data_health")
    if isinstance(data_health, dict):
        provider = data_health.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    provider = payload.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip().lower()
    return None


def _cached_latest_bars_by_instrument(
    provider: str | None,
    cards: list[OpportunityCard],
) -> dict[str, object]:
    if not provider:
        return {}
    instrument_ids = sorted({card.instrument_id for card in cards})
    if not instrument_ids:
        return {}
    try:
        latest = _market_cache_repo().load_latest_daily_bars(provider, instrument_ids)
    except Exception:
        return {}
    if latest.empty:
        return {}
    return {
        str(instrument_id): group.copy()
        for instrument_id, group in latest.groupby("instrument_id", sort=False)
    }


def _attach_decision_quality_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("decision_quality_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    center = build_decision_quality_center(
        cards=cards,
        market_intelligence=_market_intelligence_from_payload(payload.get("market_intelligence")),
        portfolio_plan=_portfolio_plan_from_payload(payload.get("portfolio_plan")),
        signal_monitor=_signal_monitor_from_payload(payload.get("signal_monitor")),
        strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
        data_health=payload.get("data_health") if isinstance(payload.get("data_health"), dict) else {},
    )
    payload["decision_quality_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_operational_readiness_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("operational_readiness_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return

    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    alert_rules_count = 0
    try:
        alert_rules_count = len(_repo().list_alert_rules())
    except Exception:
        alert_rules_count = 0

    center = build_operational_readiness_center(
        cards=cards,
        market_intelligence=_market_intelligence_from_payload(payload.get("market_intelligence")),
        decision_quality_center=(
            None
            if not isinstance(payload.get("decision_quality_center"), dict)
            else build_decision_quality_center(
                cards=cards,
                market_intelligence=_market_intelligence_from_payload(
                    payload.get("market_intelligence")
                ),
                portfolio_plan=_portfolio_plan_from_payload(payload.get("portfolio_plan")),
                signal_monitor=_signal_monitor_from_payload(payload.get("signal_monitor")),
                strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
                data_health=payload.get("data_health")
                if isinstance(payload.get("data_health"), dict)
                else {},
            )
        ),
        signal_monitor=_signal_monitor_from_payload(payload.get("signal_monitor")),
        strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
        data_health=payload.get("data_health") if isinstance(payload.get("data_health"), dict) else {},
        alert_rules_count=alert_rules_count,
    )
    payload["operational_readiness_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _attach_live_paper_health_payload(payload: dict[str, object]) -> None:
    data_health = payload.setdefault("data_health", {})
    if not isinstance(data_health, dict):
        data_health = {}
        payload["data_health"] = data_health
    try:
        trades = _paper_repo().list_trades(limit=1000)
        summary = summarize_paper_trades(trades)
    except Exception:
        return
    data_health.update(
        {
            "paper_total": str(summary.total),
            "paper_pending": str(summary.pending),
            "paper_open": str(summary.open),
            "paper_closed": str(summary.closed),
            "paper_target_hit_count": str(summary.target_hit_count),
            "paper_stopped_count": str(summary.stopped_count),
            "paper_ledger": "true",
        }
    )


def _attach_alpha_quality_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    if isinstance(payload.get("alpha_quality_center"), dict):
        return
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        return
    cards = _cards_from_payload(raw_cards)
    if not cards:
        return

    center = build_alpha_quality_center(
        cards=cards,
        rotation_radar=_rotation_radar_from_payload(payload.get("rotation_radar")),
        strategy_health=_strategy_health_from_payload(payload.get("strategy_health")),
        data_health=payload.get("data_health") if isinstance(payload.get("data_health"), dict) else {},
    )
    payload["alpha_quality_center"] = center.model_dump(mode="json")
    payload_data_health = payload.setdefault("data_health", {})
    if isinstance(payload_data_health, dict):
        payload_data_health.update(center.data_health)


def _cards_from_payload(raw_cards: list[object]) -> list[OpportunityCard]:
    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue
    return cards


def _market_intelligence_from_payload(value: object) -> MarketIntelligenceCenter | None:
    if isinstance(value, MarketIntelligenceCenter):
        return value
    if isinstance(value, dict):
        try:
            return MarketIntelligenceCenter.model_validate(value)
        except Exception:
            return None
    return None


def _portfolio_plan_from_payload(value: object) -> PortfolioPlan | None:
    if isinstance(value, PortfolioPlan):
        return value
    if isinstance(value, dict):
        try:
            return PortfolioPlan.model_validate(value)
        except Exception:
            return None
    return None


def _signal_monitor_from_payload(value: object) -> SignalMonitorCenter | None:
    if isinstance(value, SignalMonitorCenter):
        return value
    if isinstance(value, dict):
        try:
            return SignalMonitorCenter.model_validate(value)
        except Exception:
            return None
    return None


def _rotation_radar_from_payload(value: object) -> MarketRotationRadar | None:
    if isinstance(value, MarketRotationRadar):
        return value
    if isinstance(value, dict):
        try:
            return MarketRotationRadar.model_validate(value)
        except Exception:
            return None
    return None


def _strategy_health_from_payload(value: object) -> list[StrategyHealth]:
    if not isinstance(value, list):
        return []
    health: list[StrategyHealth] = []
    for item in value:
        if isinstance(item, StrategyHealth):
            health.append(item)
        elif isinstance(item, dict):
            try:
                health.append(StrategyHealth.model_validate(item))
            except Exception:
                continue
    return health


def _attach_research_center_payload(
    payload: dict[str, object],
    cards_key: str = "cards",
) -> None:
    raw_cards = payload.get(cards_key)
    if not isinstance(raw_cards, list):
        payload["research_center"] = build_research_command_center(cards=[]).model_dump(mode="json")
        return

    cards: list[OpportunityCard] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        try:
            cards.append(OpportunityCard.model_validate(raw_card))
        except Exception:
            continue

    data_health = payload.get("data_health")
    center = build_research_command_center(
        cards=cards,
        portfolio_plan=payload.get("portfolio_plan")
        if isinstance(payload.get("portfolio_plan"), dict)
        else None,
        rotation_radar=payload.get("rotation_radar")
        if isinstance(payload.get("rotation_radar"), dict)
        else None,
        strategy_health=payload.get("strategy_health")
        if isinstance(payload.get("strategy_health"), list)
        else [],
        data_health=data_health if isinstance(data_health, dict) else {},
    )
    payload["research_center"] = center.model_dump(mode="json")


def _card_rotation_score(
    card: OpportunityCard,
    rotation: object,
) -> tuple[float | None, str | None]:
    if not isinstance(rotation, dict):
        return None, None
    raw_themes = rotation.get("themes")
    if not isinstance(raw_themes, list):
        return None, None
    matched: list[tuple[float, str]] = []
    card_keys = _card_rotation_keys(card)
    for raw_theme in raw_themes:
        if not isinstance(raw_theme, dict):
            continue
        name = raw_theme.get("name")
        score = raw_theme.get("score")
        if not isinstance(name, str) or not isinstance(score, (int, float)):
            continue
        leaders = raw_theme.get("leaders")
        leader_ids = set()
        if isinstance(leaders, list):
            leader_ids = {
                str(leader.get("instrument_id"))
                for leader in leaders
                if isinstance(leader, dict)
            }
        if card.instrument_id in leader_ids or name in card_keys:
            matched.append((float(score), name))
    if not matched:
        return None, None
    return max(matched, key=lambda item: item[0])


def _card_rotation_keys(card: OpportunityCard) -> set[str]:
    keys: set[str] = set()
    if card.asset_type == "ETF" or card.opportunity_bucket == "etf_index":
        keys.add("ETF/指数工具")
    if not card.market_context:
        return keys
    keys.add(card.market_context.industry)
    keys.update(card.market_context.themes)
    for membership in card.market_context.index_memberships:
        keys.add(_normalize_rotation_membership(membership))
    return keys


def _normalize_rotation_membership(value: str) -> str:
    text = value.strip()
    if "科创" in text:
        return "科创板"
    if "创业" in text:
        return "创业板"
    if "沪深300" in text:
        return "沪深300"
    if "中证500" in text:
        return "中证500"
    if "中证1000" in text:
        return "中证1000"
    if "ETF" in text.upper():
        return "ETF/指数工具"
    return text


def _relabel_instrument_payload(payload: dict[str, object]) -> None:
    _refresh_instrument_label(payload, "cards")
    _refresh_instrument_label(payload, "items")
    _refresh_instrument_label(payload, "factor_rankings")
    _refresh_portfolio_labels(payload, "portfolio_plan")
    _refresh_sector_instrument_labels(payload)


def _model_payload_with_label(record) -> dict[str, object]:
    return _attach_instrument_label(record.model_dump(mode="json"))


def _snapshot_payload_with_label(record) -> dict[str, object]:
    payload = _model_payload_with_label(record)
    card = payload.get("card")
    if isinstance(card, dict):
        instrument_id = card.get("instrument_id")
        current_label = card.get("instrument_label")
        if isinstance(instrument_id, str):
            if not isinstance(current_label, str):
                current_label = None
            if _should_refresh_instrument_label(instrument_id, current_label):
                card["instrument_label"] = format_instrument_label(instrument_id)
    return payload


def _attach_instrument_label(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    instrument_id = payload.get("instrument_id")
    if isinstance(instrument_id, str):
        current_label = payload.get("instrument_label")
        if isinstance(current_label, str) and not _should_refresh_instrument_label(
            instrument_id,
            current_label,
        ):
            return payload
        payload["instrument_label"] = format_instrument_label(instrument_id)
    return payload


def _refresh_instrument_label(payload: dict[str, object], key: str) -> None:
    records = payload.get(key)
    if not isinstance(records, list):
        return
    for record in records:
        if not isinstance(record, dict):
            continue
        instrument_id = record.get("instrument_id")
        if not isinstance(instrument_id, str):
            continue
        current_label = record.get("instrument_label")
        if not isinstance(current_label, str):
            current_label = None
        if _should_refresh_instrument_label(instrument_id, current_label):
            record["instrument_label"] = format_instrument_label(instrument_id)


def _refresh_sector_instrument_labels(payload: dict[str, object]) -> None:
    for key in ("leaders", "laggards"):
        sector_records = payload.get("sector_strength")
        if not isinstance(sector_records, list):
            continue
        for sector in sector_records:
            if not isinstance(sector, dict):
                continue
            moves = sector.get(key)
            if not isinstance(moves, list):
                continue
            for move in moves:
                if not isinstance(move, dict):
                    continue
                instrument_id = move.get("instrument_id")
                if not isinstance(instrument_id, str):
                    continue
                current_label = move.get("instrument_label")
                if not isinstance(current_label, str):
                    current_label = None
                if _should_refresh_instrument_label(instrument_id, current_label):
                    move["instrument_label"] = format_instrument_label(instrument_id)


def _refresh_portfolio_labels(payload: dict[str, object], key: str) -> None:
    portfolio = payload.get(key)
    if not isinstance(portfolio, dict):
        return
    for section_key in ("allocations", "watchlist"):
        entries = portfolio.get(section_key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            instrument_id = entry.get("instrument_id")
            if not isinstance(instrument_id, str):
                continue
            current_label = entry.get("instrument_label")
            if not isinstance(current_label, str):
                current_label = None
            if _should_refresh_instrument_label(instrument_id, current_label):
                entry["instrument_label"] = format_instrument_label(instrument_id)


def _should_refresh_instrument_label(instrument_id: str, current_label: str | None) -> bool:
    if not current_label or not current_label.strip():
        return True
    symbol = market_symbol(instrument_id)
    if not symbol:
        return current_label.strip() == instrument_id.strip()

    # 如果是 A 股代码类标的，任意不含中文的展示都要升级为可读中文标签。
    # 旧数据中经常会留下“688059.SH”这类代码形式的标签。
    if symbol.isdigit():
        return not _contains_chinese(current_label)

    if _contains_chinese(current_label):
        return False
    return current_label.strip() == symbol


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _strategy_health_from_card_calibration(payload: dict[str, object]) -> list[dict[str, object]]:
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        return []
    by_strategy: dict[str, dict[str, object]] = {}
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        calibration = raw_card.get("strategy_calibration")
        if not isinstance(calibration, dict):
            continue
        strategy_id = calibration.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id:
            continue
        sample_count = _int_value(calibration.get("sample_count"))
        current = by_strategy.get(strategy_id)
        if current is not None and _int_value(current.get("sample_count")) >= sample_count:
            continue
        name, family = _strategy_identity_from_card(raw_card, strategy_id)
        by_strategy[strategy_id] = {
            "strategy_id": strategy_id,
            "name": name,
            "family": family,
            "readiness": str(calibration.get("readiness") or "limited_sample"),
            "sample_count": sample_count,
            "win_rate_10d": calibration.get("win_rate_10d"),
            "avg_return_10d": calibration.get("avg_return_10d"),
            "avg_return_20d": calibration.get("avg_return_20d"),
            "max_loss_10d": calibration.get("max_loss_10d"),
            "missing_data": [],
            "curve": [],
        }
    return sorted(by_strategy.values(), key=lambda item: str(item["strategy_id"]))


def _strategy_identity_from_card(raw_card: dict[str, object], strategy_id: str) -> tuple[str, str]:
    evaluations = raw_card.get("strategy_evaluations")
    if isinstance(evaluations, list):
        for raw_evaluation in evaluations:
            if not isinstance(raw_evaluation, dict):
                continue
            if raw_evaluation.get("strategy_id") == strategy_id:
                name = raw_evaluation.get("name")
                family = raw_evaluation.get("family")
                return (
                    str(name or strategy_id),
                    str(family or "calibrated_strategy"),
                )
    return strategy_id, "calibrated_strategy"


def _int_value(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _full_market_scan_cache_key(
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    sync_if_empty: bool,
) -> str:
    return (
        f"today_scan:{provider.strip().lower()}:{max_symbols}:"
        f"{str(include_etfs).lower()}:{str(sync_if_empty).lower()}"
    )


def _task_payload(
    task_id: str,
    kind: str,
    provider: str,
    max_symbols: int,
    include_etfs: bool,
    cache: str,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "kind": kind,
        "provider": provider.strip().lower(),
        "max_symbols": max_symbols,
        "include_etfs": include_etfs,
        "cache": cache,
    }


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
    return {"snapshots": [_snapshot_payload_with_label(snapshot) for snapshot in snapshots]}


@router.get("/outcomes")
def outcomes(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    return {
        "outcomes": [_model_payload_with_label(outcome) for outcome in replayed],
        "data_health": data_health,
    }


def _replay_outcomes(provider: str, instrument_id: str | None, limit: int):
    repo = _repo()
    snapshots = repo.list_opportunity_snapshots(instrument_id=instrument_id, limit=limit)
    try:
        market_provider = build_market_data_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    instrument_ids = list(dict.fromkeys(snapshot.instrument_id for snapshot in snapshots))
    all_bars = market_provider.get_daily_bars(
        instrument_ids,
        start=date(1900, 1, 1),
        end=date(2100, 1, 1),
    )

    replayed = []
    for snapshot in snapshots:
        if not all_bars.empty and "instrument_id" in all_bars.columns:
            bars = all_bars.loc[all_bars["instrument_id"] == snapshot.instrument_id]
        else:
            bars = all_bars
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


@router.get("/recommendation-closure")
def recommendation_closure(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 150,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    as_of = max(
        (outcome.signal_date for outcome in replayed if outcome.signal_date is not None),
        default=date.today(),
    )
    summary = summarize_recommendation_closure(replayed, as_of=as_of, windows=(30, 60, 90))
    payload = summary.model_dump(mode="json")
    payload["latest_outcomes"] = [
        _attach_existing_instrument_label(outcome)
        for outcome in payload.get("latest_outcomes", [])
        if isinstance(outcome, dict)
    ]
    payload["completed_outcomes"] = [
        _attach_existing_instrument_label(outcome)
        for outcome in payload.get("completed_outcomes", [])
        if isinstance(outcome, dict)
    ]
    payload["data_health"] = {
        **data_health,
        "closure_windows": "30,60,90",
        "as_of": str(as_of),
        "completed_outcomes": str(len(payload["completed_outcomes"])),
    }
    return payload


@router.get("/recommendation-followthrough")
def recommendation_followthrough(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 150,
) -> dict[str, object]:
    replayed, data_health = _replay_outcomes(provider, instrument_id, limit)
    as_of = max(
        (outcome.signal_date for outcome in replayed if outcome.signal_date is not None),
        default=date.today(),
    )
    closure = summarize_recommendation_closure(replayed, as_of=as_of, windows=(30, 60, 90))
    center = build_recommendation_followthrough_center(
        closure,
        data_health=data_health,
    )
    return center.model_dump(mode="json")


@router.get("/recommendation-calibration")
def recommendation_calibration(
    provider: str = "fixture",
    instrument_id: str | None = None,
    limit: int = 200,
) -> dict[str, object]:
    pairs, data_health = _replay_snapshot_outcome_pairs(provider, instrument_id, limit)
    as_of = max(
        (outcome.signal_date for _, outcome in pairs if outcome.signal_date is not None),
        default=date.today(),
    )
    center = build_recommendation_calibration_center(
        pairs,
        as_of=as_of,
        data_health=data_health,
    )
    return center.model_dump(mode="json")


def _replay_snapshot_outcome_pairs(
    provider: str,
    instrument_id: str | None,
    limit: int,
):
    repo = _repo()
    snapshots = repo.list_opportunity_snapshots(instrument_id=instrument_id, limit=limit)
    try:
        market_provider = build_market_data_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    instrument_ids = list(dict.fromkeys(snapshot.instrument_id for snapshot in snapshots))
    all_bars = market_provider.get_daily_bars(
        instrument_ids,
        start=date(1900, 1, 1),
        end=date(2100, 1, 1),
    )

    pairs = []
    for snapshot in snapshots:
        if not all_bars.empty and "instrument_id" in all_bars.columns:
            bars = all_bars.loc[all_bars["instrument_id"] == snapshot.instrument_id]
        else:
            bars = all_bars
        pairs.append((snapshot, compute_opportunity_outcome(snapshot, bars)))
    data_health = {
        "provider": provider,
        "snapshots": str(len(snapshots)),
        "outcomes": str(len(pairs)),
    }
    provider_errors = getattr(market_provider, "last_errors", [])
    if provider_errors:
        data_health["errors"] = " | ".join(provider_errors[:3])
    return pairs, data_health


def _attach_existing_instrument_label(payload: dict[str, object]) -> dict[str, object]:
    current_label = payload.get("instrument_label")
    if isinstance(current_label, str) and current_label.strip():
        return payload
    payload["instrument_label"] = None
    return payload


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
    position_risks = _position_risks(mode)
    selected_position_risk = next(
        (risk for risk in position_risks if risk.instrument_id == selected.instrument_id),
        None,
    )
    selected_paper_trade = _paper_trade_for_instrument(selected.instrument_id)

    answer = answer_question(
        request.question,
        context={
            "instrument_id": selected.instrument_id,
            "instrument_label": selected.instrument_label or format_instrument_label(selected.instrument_id),
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
            "position_risk": (
                selected_position_risk.model_dump(mode="json") if selected_position_risk else None
            ),
            "position_risks": [risk.model_dump(mode="json") for risk in position_risks],
            "paper_trade": (
                selected_paper_trade.model_dump(mode="json") if selected_paper_trade else None
            ),
            "provider": mode,
            "data_health": result.data_health,
        },
    )
    return AgentQueryResponse(answer=answer)


def _paper_trade_for_instrument(instrument_id: str):
    return next(
        (
            trade
            for trade in _paper_repo().list_trades(limit=1000)
            if trade.instrument_id == instrument_id
        ),
        None,
    )


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
        "tradability_label": card.tradability.label if card.tradability else None,
        "tradability_summary": card.tradability.summary if card.tradability else None,
    }
