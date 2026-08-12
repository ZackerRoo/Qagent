from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import date
import math
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.providers.fuyao import FuyaoClient, FuyaoProviderError, from_fuyao_thscode
from qagent.storage.fuyao_research import FuyaoResearchRepository, FuyaoResearchSnapshot


FUYAO_MARKET_SENTIMENT_CONTRACT = "fuyao-market-sentiment-v1"
EXPECTED_MARKET_SECTIONS = (
    "limit_up_pool",
    "limit_up_ladder",
    "hot_stock_list",
    "skyrocket_list",
    "anomaly_analysis",
    "dragon_tiger",
)


class FuyaoMarketTheme(BaseModel):
    name: str
    mentions: int
    leaders: list[str] = Field(default_factory=list)


class FuyaoMarketSignal(BaseModel):
    instrument_id: str
    instrument_label: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    limit_up: bool = False
    board_count: int = 0
    hot_rank: int | None = None
    skyrocket_rank: int | None = None
    anomaly_count: int = 0
    dragon_tiger: bool = False
    themes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class FuyaoMarketSentiment(BaseModel):
    contract: str = FUYAO_MARKET_SENTIMENT_CONTRACT
    trade_date: date
    state: Literal["very_active", "active", "balanced", "quiet"]
    activity_score: float = Field(ge=0.0, le=1.0)
    limit_up_count: int
    max_board_count: int
    hot_stock_count: int
    skyrocket_count: int
    anomaly_count: int
    dragon_tiger_count: int
    section_coverage: float = Field(ge=0.0, le=1.0)
    available_sections: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)
    top_themes: list[FuyaoMarketTheme] = Field(default_factory=list)
    leaders: list[FuyaoMarketSignal] = Field(default_factory=list)
    signals: list[FuyaoMarketSignal] = Field(default_factory=list)
    source_timestamps: list[str] = Field(default_factory=list)
    classification: str = "research_only"
    decision_weight_applied: bool = False
    paper_order_side_effect: bool = False


class FuyaoMarketCaptureResult(BaseModel):
    status: str
    response: dict[str, Any]
    snapshot: FuyaoResearchSnapshot | None = None
    errors: list[dict[str, object]] = Field(default_factory=list)


def capture_fuyao_market_research(
    session_factory: sessionmaker[Session],
    *,
    client: FuyaoClient,
    trade_date: date,
    period: Literal["day", "hour"] = "day",
    include_historical_hot_list: bool = False,
    reuse_existing: bool = False,
) -> FuyaoMarketCaptureResult:
    repository = FuyaoResearchRepository(session_factory)
    identity = {
        "period": period,
        "trade_date": trade_date.isoformat(),
    }
    if reuse_existing:
        existing = repository.latest(research_type="market", identity=identity)
        if existing is not None and _snapshot_is_complete(existing):
            return FuyaoMarketCaptureResult(
                status="existing",
                response=_stored_response(existing, freshness="stored"),
                snapshot=existing,
            )

    sections, errors = collect_fuyao_market_sections(
        client,
        period=period,
        trade_date=trade_date,
        include_historical_hot_list=include_historical_hot_list,
    )
    raw_sections_available = any(name in sections for name in EXPECTED_MARKET_SECTIONS)
    if not raw_sections_available:
        existing = repository.latest(research_type="market", identity=identity)
        if existing is not None:
            response = _stored_response(existing, freshness="stored_fallback")
            response["status"] = "stale"
            response["errors"] = errors
            return FuyaoMarketCaptureResult(
                status="stored_fallback",
                response=response,
                snapshot=existing,
                errors=errors,
            )
        sentiment = build_fuyao_market_sentiment({}, trade_date=trade_date)
        response = _market_response(
            identity=identity,
            sections={"derived_sentiment": sentiment.model_dump(mode="json")},
            errors=errors,
            client=client,
        )
        response["status"] = "unavailable"
        response["freshness"] = "unavailable"
        response["snapshot"] = None
        return FuyaoMarketCaptureResult(
            status="unavailable",
            response=response,
            errors=errors,
        )
    sentiment = build_fuyao_market_sentiment(sections, trade_date=trade_date)
    sections["derived_sentiment"] = sentiment.model_dump(mode="json")
    response = _market_response(
        identity=identity,
        sections=sections,
        errors=errors,
        client=client,
    )
    snapshot = repository.append(
        research_type="market",
        identity=identity,
        payload=_persisted_payload(response),
        source_request_id=_optional_text(response["source"].get("request_id")),
        source_timestamp=_optional_text(response["source"].get("data_timestamp")),
    )
    response["snapshot"] = _snapshot_reference(snapshot)
    return FuyaoMarketCaptureResult(
        status="recorded" if sections else "unavailable",
        response=response,
        snapshot=snapshot,
        errors=errors,
    )


def latest_fuyao_market_research(
    session_factory: sessionmaker[Session],
) -> dict[str, Any] | None:
    latest = FuyaoResearchRepository(session_factory).latest_for_type("market")
    return _stored_response(latest, freshness="stored") if latest is not None else None


def collect_fuyao_market_sections(
    client: FuyaoClient,
    *,
    period: Literal["day", "hour"],
    trade_date: date,
    include_historical_hot_list: bool,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    sections: dict[str, object] = {}
    errors: list[dict[str, object]] = []
    loaders: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("limit_up_pool", lambda: client.get_limit_up_pool(trade_date=trade_date)),
        ("limit_up_ladder", client.get_limit_up_ladder),
        ("hot_stock_list", lambda: client.get_hot_stock_list(period=period)),
        ("skyrocket_list", lambda: client.get_skyrocket_list(period=period)),
        ("anomaly_analysis", client.get_anomaly_analysis),
        ("dragon_tiger", lambda: client.get_dragon_tiger(trade_date=trade_date)),
    ]
    if include_historical_hot_list:
        loaders.append(
            ("hot_stock_history", lambda: client.get_hot_stock_history(trade_date))
        )
    for name, loader in loaders:
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
    return sections, errors


def build_fuyao_market_sentiment(
    sections: Mapping[str, object],
    *,
    trade_date: date,
) -> FuyaoMarketSentiment:
    limit_up_items = _items(sections.get("limit_up_pool"))
    ladder_items = _items(sections.get("limit_up_ladder"))
    hot_items = _items(sections.get("hot_stock_list"))
    skyrocket_items = _items(sections.get("skyrocket_list"))
    anomaly_items = _items(sections.get("anomaly_analysis"))
    dragon_items = _dragon_tiger_items(sections.get("dragon_tiger"))

    labels: dict[str, str] = {}
    signal_parts: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "limit_up": False,
            "board_count": 0,
            "hot_rank": None,
            "skyrocket_rank": None,
            "anomaly_count": 0,
            "dragon_tiger": False,
            "themes": set(),
            "evidence": [],
        }
    )
    theme_mentions: Counter[str] = Counter()
    theme_leaders: dict[str, list[str]] = defaultdict(list)

    for item in limit_up_items:
        instrument_id = _instrument_id(item)
        if instrument_id is None:
            continue
        _remember_label(labels, instrument_id, item)
        part = signal_parts[instrument_id]
        part["limit_up"] = True
        board_count = _safe_int(item.get("continue_day_cnt")) or 1
        part["board_count"] = max(_safe_int(part["board_count"]) or 0, board_count)
        evidence = part["evidence"]
        assert isinstance(evidence, list)
        evidence.append("limit_up")
        for theme in _themes_from_reason(item.get("limit_up_reason")):
            _add_theme(part, theme)
            theme_mentions[theme] += 1
            _append_unique(theme_leaders[theme], labels.get(instrument_id, instrument_id))

    for item in ladder_items:
        boards = item.get("boards")
        if not isinstance(boards, Mapping):
            continue
        for board_items in boards.values():
            if not isinstance(board_items, list):
                continue
            for board_item in board_items:
                if not isinstance(board_item, Mapping):
                    continue
                instrument_id = _instrument_id(board_item)
                if instrument_id is None:
                    continue
                _remember_label(labels, instrument_id, board_item)
                board_count = _safe_int(board_item.get("board_num")) or 0
                part = signal_parts[instrument_id]
                part["board_count"] = max(
                    _safe_int(part["board_count"]) or 0,
                    board_count,
                )
                evidence = part["evidence"]
                assert isinstance(evidence, list)
                evidence.append(f"{board_count}_board")

    for key, items in (("hot_rank", hot_items), ("skyrocket_rank", skyrocket_items)):
        for item in items:
            instrument_id = _instrument_id(item)
            rank = _safe_int(item.get("rank"))
            if instrument_id is None or rank is None:
                continue
            _remember_label(labels, instrument_id, item)
            signal_parts[instrument_id][key] = rank
            evidence = signal_parts[instrument_id]["evidence"]
            assert isinstance(evidence, list)
            evidence.append("hot_rank" if key == "hot_rank" else "skyrocket_rank")

    for item in anomaly_items:
        instrument_id = _instrument_id(item)
        if instrument_id is None:
            continue
        _remember_label(labels, instrument_id, item)
        part = signal_parts[instrument_id]
        part["anomaly_count"] = (_safe_int(part["anomaly_count"]) or 0) + 1
        evidence = part["evidence"]
        assert isinstance(evidence, list)
        evidence.append("anomaly")
        keywords = item.get("keyword_list")
        for theme in keywords if isinstance(keywords, list) else []:
            normalized = _normalize_theme(theme)
            if normalized is None:
                continue
            _add_theme(part, normalized)
            theme_mentions[normalized] += 1
            _append_unique(theme_leaders[normalized], labels.get(instrument_id, instrument_id))

    for item in dragon_items:
        instrument_id = _instrument_id(item)
        if instrument_id is None:
            continue
        _remember_label(labels, instrument_id, item)
        part = signal_parts[instrument_id]
        part["dragon_tiger"] = True
        hot_rank = _safe_int(item.get("hot_rank"))
        current_hot_rank = _safe_int(part.get("hot_rank"))
        if hot_rank is not None and (
            current_hot_rank is None or hot_rank < current_hot_rank
        ):
            part["hot_rank"] = hot_rank
        evidence = part["evidence"]
        assert isinstance(evidence, list)
        evidence.append("dragon_tiger")
        dragon_themes = [
            *_themes_from_concepts(item.get("concept_list")),
            *_themes_from_reason(item.get("limit_reason")),
        ]
        for theme in dict.fromkeys(dragon_themes):
            _add_theme(part, theme)
            theme_mentions[theme] += 1
            _append_unique(theme_leaders[theme], labels.get(instrument_id, instrument_id))

    signals = [
        _build_signal(instrument_id, labels.get(instrument_id), values)
        for instrument_id, values in signal_parts.items()
    ]
    signals.sort(key=lambda item: (item.score, item.board_count, item.instrument_id), reverse=True)
    signals = signals[:120]
    available = [name for name in EXPECTED_MARKET_SECTIONS if name in sections]
    missing = [name for name in EXPECTED_MARKET_SECTIONS if name not in sections]
    max_board = max((item.board_count for item in signals), default=0)
    limit_up_count = _section_item_total(
        sections.get("limit_up_pool"),
        fallback=len(limit_up_items),
    )
    dragon_tiger_count = len(
        {
            instrument_id
            for item in dragon_items
            if (instrument_id := _instrument_id(item)) is not None
        }
    )
    activity_score = _activity_score(
        limit_up_count=limit_up_count,
        max_board_count=max_board,
        hot_stock_count=len(hot_items),
        skyrocket_count=len(skyrocket_items),
        anomaly_count=len(anomaly_items),
        dragon_tiger_count=dragon_tiger_count,
    )
    themes = [
        FuyaoMarketTheme(
            name=name,
            mentions=count,
            leaders=theme_leaders.get(name, [])[:3],
        )
        for name, count in theme_mentions.most_common(10)
    ]
    return FuyaoMarketSentiment(
        trade_date=trade_date,
        state=_activity_state(activity_score),
        activity_score=activity_score,
        limit_up_count=limit_up_count,
        max_board_count=max_board,
        hot_stock_count=len(hot_items),
        skyrocket_count=len(skyrocket_items),
        anomaly_count=len(anomaly_items),
        dragon_tiger_count=dragon_tiger_count,
        section_coverage=round(len(available) / len(EXPECTED_MARKET_SECTIONS), 6),
        available_sections=available,
        missing_sections=missing,
        top_themes=themes,
        leaders=signals[:8],
        signals=signals,
        source_timestamps=_source_timestamps(sections),
    )


def market_summary(sentiment: FuyaoMarketSentiment) -> dict[str, object]:
    return {
        "title": "A股市场情绪",
        "metrics": [
            _metric("activity_score", "活跃度", sentiment.activity_score * 100, "%"),
            _metric("limit_up_count", "涨停数量", sentiment.limit_up_count),
            _metric("max_board_count", "最高连板", sentiment.max_board_count, "板"),
            _metric("hot_stock_count", "热门股", sentiment.hot_stock_count),
            _metric("skyrocket_count", "飙升榜", sentiment.skyrocket_count),
            _metric("anomaly_count", "异动解读", sentiment.anomaly_count),
            _metric("dragon_tiger_count", "龙虎榜", sentiment.dragon_tiger_count),
            _metric("section_coverage", "数据覆盖", sentiment.section_coverage * 100, "%"),
        ],
        "notes": [
            f"情绪状态 {sentiment.state}",
            f"研究信号 {len(sentiment.signals)}，不参与当前排序或模拟成交",
        ],
    }


def _market_response(
    *,
    identity: dict[str, object],
    sections: dict[str, object],
    errors: list[dict[str, object]],
    client: FuyaoClient,
) -> dict[str, Any]:
    sentiment = FuyaoMarketSentiment.model_validate(sections["derived_sentiment"])
    return {
        "provider": "fuyao",
        "research_type": "market",
        "classification": "research_only",
        "decision_weight_applied": False,
        "paper_order_side_effect": False,
        "status": "ready" if not errors else "partial",
        "freshness": "live",
        "identity": identity,
        "sections": sections,
        "summary": market_summary(sentiment),
        "source": _client_source(client),
        "errors": errors,
    }


def _stored_response(
    snapshot: FuyaoResearchSnapshot,
    *,
    freshness: str,
) -> dict[str, Any]:
    response = dict(snapshot.payload)
    errors = response.get("errors")
    response.update(
        {
            "status": "partial" if isinstance(errors, list) and errors else "ready",
            "freshness": freshness,
            "snapshot": _snapshot_reference(snapshot),
        }
    )
    return response


def _snapshot_is_complete(snapshot: FuyaoResearchSnapshot) -> bool:
    payload = snapshot.payload
    errors = payload.get("errors")
    sections = payload.get("sections")
    return (
        (not isinstance(errors, list) or not errors)
        and isinstance(sections, dict)
        and all(name in sections for name in EXPECTED_MARKET_SECTIONS)
    )


def _persisted_payload(response: dict[str, Any]) -> dict[str, Any]:
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


def _snapshot_reference(snapshot: FuyaoResearchSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "payload_digest": snapshot.payload_digest,
        "observed_at": snapshot.observed_at.isoformat(),
        "source_timestamp": snapshot.source_timestamp,
        "source_request_id": snapshot.source_request_id,
        "persisted": True,
    }


def _client_source(client: FuyaoClient) -> dict[str, object]:
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


def _build_signal(
    instrument_id: str,
    label: str | None,
    values: Mapping[str, object],
) -> FuyaoMarketSignal:
    board_count = _safe_int(values.get("board_count")) or 0
    hot_rank = _safe_int(values.get("hot_rank"))
    skyrocket_rank = _safe_int(values.get("skyrocket_rank"))
    anomaly_count = _safe_int(values.get("anomaly_count")) or 0
    score = 0.0
    if bool(values.get("limit_up")):
        score += 0.30
    score += min(max(board_count - 1, 0), 6) * 0.05
    if hot_rank is not None:
        score += max(0.0, 1.0 - (hot_rank - 1) / 30.0) * 0.22
    if skyrocket_rank is not None:
        score += max(0.0, 1.0 - (skyrocket_rank - 1) / 30.0) * 0.18
    score += min(anomaly_count, 2) * 0.05
    if bool(values.get("dragon_tiger")):
        score += 0.10
    themes = values.get("themes")
    evidence = values.get("evidence")
    return FuyaoMarketSignal(
        instrument_id=instrument_id,
        instrument_label=label,
        score=round(min(score, 1.0), 6),
        limit_up=bool(values.get("limit_up")),
        board_count=board_count,
        hot_rank=hot_rank,
        skyrocket_rank=skyrocket_rank,
        anomaly_count=anomaly_count,
        dragon_tiger=bool(values.get("dragon_tiger")),
        themes=sorted(themes)[:8] if isinstance(themes, set) else [],
        evidence=list(dict.fromkeys(evidence)) if isinstance(evidence, list) else [],
    )


def _activity_score(
    *,
    limit_up_count: int,
    max_board_count: int,
    hot_stock_count: int,
    skyrocket_count: int,
    anomaly_count: int,
    dragon_tiger_count: int,
) -> float:
    score = (
        min(limit_up_count / 80.0, 1.0) * 0.34
        + min(max_board_count / 7.0, 1.0) * 0.22
        + min(hot_stock_count / 30.0, 1.0) * 0.10
        + min(skyrocket_count / 30.0, 1.0) * 0.10
        + min(anomaly_count / 250.0, 1.0) * 0.10
        + min(dragon_tiger_count / 50.0, 1.0) * 0.14
    )
    return round(min(score, 1.0), 6)


def _activity_state(score: float) -> Literal["very_active", "active", "balanced", "quiet"]:
    if score >= 0.75:
        return "very_active"
    if score >= 0.55:
        return "active"
    if score >= 0.35:
        return "balanced"
    return "quiet"


def _dragon_tiger_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    items: list[dict[str, Any]] = []
    for key in ("stock_items", "hot_money_items"):
        raw = value.get(key)
        if isinstance(raw, list):
            items.extend(item for item in raw if isinstance(item, dict))
    return items


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    raw = value.get("item")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _instrument_id(item: Mapping[str, object]) -> str | None:
    thscode = item.get("thscode")
    if not isinstance(thscode, str) or not thscode.strip():
        return None
    try:
        return from_fuyao_thscode(thscode)
    except (ValueError, FuyaoProviderError):
        return None


def _remember_label(
    labels: dict[str, str],
    instrument_id: str,
    item: Mapping[str, object],
) -> None:
    for key in ("name", "stock_name", "security_name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            labels[instrument_id] = value.strip()
            return


def _themes_from_reason(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    themes: list[str] = []
    for part in value.replace("/", "+").replace("；", "+").split("+"):
        normalized = _normalize_theme(part)
        if normalized is not None:
            themes.append(normalized)
    return list(dict.fromkeys(themes))


def _themes_from_concepts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    themes: list[str] = []
    for item in value:
        raw_name = item.get("name") if isinstance(item, Mapping) else item
        normalized = _normalize_theme(raw_name)
        if normalized is not None:
            themes.append(normalized)
    return list(dict.fromkeys(themes))


def _section_item_total(value: object, *, fallback: int) -> int:
    if not isinstance(value, Mapping):
        return fallback
    pagination = value.get("pagination")
    total = _safe_int(pagination.get("total")) if isinstance(pagination, Mapping) else None
    return max(total, fallback) if total is not None else fallback


def _normalize_theme(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("概念", "")
    if not text or len(text) > 20:
        return None
    return text


def _add_theme(values: dict[str, object], theme: str) -> None:
    themes = values["themes"]
    assert isinstance(themes, set)
    themes.add(theme)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _source_timestamps(sections: Mapping[str, object]) -> list[str]:
    values: list[str] = []
    for section in sections.values():
        if not isinstance(section, Mapping):
            continue
        timestamp = section.get("timestamp")
        if timestamp is not None:
            values.append(str(timestamp))
    return list(dict.fromkeys(values))


def _metric(
    key: str,
    label: str,
    value: float | int,
    unit: str | None = None,
) -> dict[str, object]:
    return {"key": key, "label": label, "value": value, "unit": unit}


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) else None


def _optional_text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None
