from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from qagent.providers.fuyao import FuyaoClient, FuyaoProviderError, from_fuyao_thscode
from qagent.storage.fuyao_research import FuyaoResearchRepository, FuyaoResearchSnapshot


FUYAO_THEME_STRENGTH_CONTRACT = "fuyao-theme-strength-v1"
THEME_TAGS: tuple[Literal["industry", "cn_concept"], ...] = ("industry", "cn_concept")
BENCHMARK_THSCODE = "000300.SH"
DEFAULT_LEADING_THEME_LIMIT = 8
DEFAULT_CONSTITUENT_DISPLAY_LIMIT = 30


class FuyaoThemeConstituent(BaseModel):
    instrument_id: str
    label: str | None = None


class FuyaoThemeStrength(BaseModel):
    thscode: str
    name: str
    category: Literal["industry", "cn_concept"]
    last_price: float | None = None
    change_pct: float | None = None
    relative_1d_pct: float | None = None
    relative_5d_pct: float | None = None
    relative_20d_pct: float | None = None
    constituent_count: int | None = None
    constituents: list[FuyaoThemeConstituent] = Field(default_factory=list)
    constituent_snapshot: bool = False


class FuyaoThemeStrengthSnapshot(BaseModel):
    contract: str = FUYAO_THEME_STRENGTH_CONTRACT
    trade_date: date
    benchmark_thscode: str = BENCHMARK_THSCODE
    benchmark_last_price: float | None = None
    benchmark_change_pct: float | None = None
    catalog_count: int = 0
    snapshot_count: int = 0
    coverage: float = Field(ge=0.0, le=1.0)
    leading_theme_limit: int
    themes: list[FuyaoThemeStrength] = Field(default_factory=list)
    leading_themes: list[FuyaoThemeStrength] = Field(default_factory=list)
    classification: str = "research_only"
    decision_weight_applied: bool = False
    paper_order_side_effect: bool = False


class FuyaoThemeCaptureResult(BaseModel):
    status: Literal["recorded", "existing", "partial", "unavailable", "stored_fallback"]
    response: dict[str, Any]
    snapshot: FuyaoResearchSnapshot | None = None
    errors: list[dict[str, object]] = Field(default_factory=list)


def capture_fuyao_theme_strength(
    session_factory: sessionmaker[Session],
    *,
    client: FuyaoClient,
    trade_date: date,
    leading_theme_limit: int = DEFAULT_LEADING_THEME_LIMIT,
    constituent_display_limit: int = DEFAULT_CONSTITUENT_DISPLAY_LIMIT,
    reuse_existing: bool = False,
) -> FuyaoThemeCaptureResult:
    if leading_theme_limit <= 0 or leading_theme_limit > 20:
        raise ValueError("leading_theme_limit must be between 1 and 20")
    if constituent_display_limit <= 0 or constituent_display_limit > 100:
        raise ValueError("constituent_display_limit must be between 1 and 100")

    repository = FuyaoResearchRepository(session_factory)
    identity = {
        "contract": FUYAO_THEME_STRENGTH_CONTRACT,
        "trade_date": trade_date.isoformat(),
        "tags": list(THEME_TAGS),
        "leading_theme_limit": leading_theme_limit,
        "constituent_display_limit": constituent_display_limit,
    }
    if reuse_existing:
        existing = repository.latest(research_type="theme_strength", identity=identity)
        if existing is not None and _snapshot_is_usable(existing):
            return FuyaoThemeCaptureResult(
                status="existing",
                response=_stored_response(existing, freshness="stored"),
                snapshot=existing,
            )

    catalogs: dict[str, list[dict[str, str]]] = {}
    errors: list[dict[str, object]] = []
    for tag in THEME_TAGS:
        try:
            catalogs[tag] = _catalog_items(client.get_index_catalog(tag))
        except (ValueError, FuyaoProviderError) as exc:
            errors.append(_error(tag, exc))

    catalog_entries = [
        {**item, "category": tag}
        for tag, items in catalogs.items()
        for item in items
    ]
    if not catalog_entries:
        existing = repository.latest(research_type="theme_strength", identity=identity)
        if existing is not None:
            response = _stored_response(existing, freshness="stored_fallback")
            response["status"] = "stale"
            response["errors"] = errors
            return FuyaoThemeCaptureResult(
                status="stored_fallback",
                response=response,
                snapshot=existing,
                errors=errors,
            )
        return FuyaoThemeCaptureResult(
            status="unavailable",
            response=_response(identity, None, errors, client=client, freshness="unavailable"),
            errors=errors,
        )

    snapshots: dict[str, Mapping[str, object]] = {}
    thscodes = sorted({str(item["thscode"]) for item in catalog_entries} | {BENCHMARK_THSCODE})
    for offset in range(0, len(thscodes), 50):
        batch = thscodes[offset : offset + 50]
        try:
            for item in _snapshot_items(client.get_index_snapshot_data(batch)):
                thscode = _text(item.get("thscode"))
                if thscode:
                    snapshots[thscode] = item
        except (ValueError, FuyaoProviderError) as exc:
            errors.append(_error(f"snapshot:{offset // 50}", exc))

    benchmark = snapshots.get(BENCHMARK_THSCODE)
    benchmark_last_price = _number(benchmark.get("last_price")) if benchmark else None
    benchmark_change_pct = _number(benchmark.get("price_change_ratio_pct")) if benchmark else None
    previous = _prior_snapshots(repository, trade_date)
    themes = [
        _theme_strength(
            item,
            snapshots.get(str(item["thscode"])),
            benchmark_change_pct=benchmark_change_pct,
            benchmark_last_price=benchmark_last_price,
            previous=previous,
        )
        for item in catalog_entries
    ]
    available = [item for item in themes if item.last_price is not None]
    leading = sorted(
        available,
        key=lambda item: (
            item.relative_1d_pct if item.relative_1d_pct is not None else -float("inf"),
            item.change_pct if item.change_pct is not None else -float("inf"),
            item.name,
        ),
        reverse=True,
    )[:leading_theme_limit]
    _capture_leading_constituents(
        client,
        leading,
        errors=errors,
        display_limit=constituent_display_limit,
    )

    report = FuyaoThemeStrengthSnapshot(
        trade_date=trade_date,
        benchmark_last_price=benchmark_last_price,
        benchmark_change_pct=benchmark_change_pct,
        catalog_count=len(themes),
        snapshot_count=len(available),
        coverage=round(len(available) / len(themes), 6) if themes else 0.0,
        leading_theme_limit=leading_theme_limit,
        themes=themes,
        leading_themes=leading,
    )
    response = _response(identity, report, errors, client=client, freshness="live")
    snapshot = repository.append(
        research_type="theme_strength",
        identity=identity,
        payload=response,
        source_request_id=_client_request_id(client),
        source_timestamp=datetime.now(timezone.utc).isoformat(),
    )
    response["snapshot"] = _snapshot_reference(snapshot)
    return FuyaoThemeCaptureResult(
        status="recorded" if not errors else "partial",
        response=response,
        snapshot=snapshot,
        errors=errors,
    )


def latest_fuyao_theme_strength(
    session_factory: sessionmaker[Session],
) -> dict[str, Any] | None:
    snapshot = FuyaoResearchRepository(session_factory).latest_for_type("theme_strength")
    return _stored_response(snapshot, freshness="stored") if snapshot is not None else None


def _prior_snapshots(
    repository: FuyaoResearchRepository,
    trade_date: date,
) -> dict[int, tuple[dict[str, FuyaoThemeStrength], float | None]]:
    rows = [
        item
        for item in repository.list_for_type("theme_strength", limit=80)
        if _snapshot_trade_date(item) is not None and _snapshot_trade_date(item) < trade_date
    ]
    by_date: dict[date, FuyaoResearchSnapshot] = {}
    for item in rows:
        snapshot_date = _snapshot_trade_date(item)
        if snapshot_date is not None:
            by_date.setdefault(snapshot_date, item)
    ordered = sorted(by_date.items(), reverse=True)
    result: dict[int, tuple[dict[str, FuyaoThemeStrength], float | None]] = {}
    for horizon in (5, 20):
        if len(ordered) < horizon:
            continue
        payload = ordered[horizon - 1][1].payload
        report = _theme_report(payload)
        if report is None:
            continue
        result[horizon] = (
            {item.thscode: item for item in report.themes},
            report.benchmark_last_price,
        )
    return result


def _theme_strength(
    catalog_item: Mapping[str, str],
    snapshot: Mapping[str, object] | None,
    *,
    benchmark_change_pct: float | None,
    benchmark_last_price: float | None,
    previous: Mapping[int, tuple[dict[str, FuyaoThemeStrength], float | None]],
) -> FuyaoThemeStrength:
    thscode = str(catalog_item["thscode"])
    last_price = _number(snapshot.get("last_price")) if snapshot else None
    change_pct = _number(snapshot.get("price_change_ratio_pct")) if snapshot else None
    result = FuyaoThemeStrength(
        thscode=thscode,
        name=str(catalog_item["name"]),
        category=catalog_item["category"],  # type: ignore[arg-type]
        last_price=last_price,
        change_pct=change_pct,
        relative_1d_pct=_difference(change_pct, benchmark_change_pct),
    )
    for horizon, (prior_themes, prior_benchmark) in previous.items():
        prior = prior_themes.get(thscode)
        relative = _relative_return(last_price, prior.last_price if prior else None, benchmark_last_price, prior_benchmark)
        if horizon == 5:
            result.relative_5d_pct = relative
        else:
            result.relative_20d_pct = relative
    return result


def _capture_leading_constituents(
    client: FuyaoClient,
    themes: list[FuyaoThemeStrength],
    *,
    errors: list[dict[str, object]],
    display_limit: int,
) -> None:
    for theme in themes:
        try:
            items = _constituents(client.get_index_constituents(theme.thscode))
        except (ValueError, FuyaoProviderError) as exc:
            errors.append(_error(f"constituents:{theme.thscode}", exc))
            continue
        theme.constituent_count = len(items)
        theme.constituents = items[:display_limit]
        theme.constituent_snapshot = True


def _catalog_items(payload: Mapping[str, object]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in _items(payload):
        thscode = _text(item.get("thscode"))
        name = _text(item.get("name"))
        if thscode and name:
            result.append({"thscode": thscode, "name": name})
    return result


def _snapshot_items(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    return _items(payload)


def _constituents(payload: Mapping[str, object]) -> list[FuyaoThemeConstituent]:
    result: list[FuyaoThemeConstituent] = []
    for item in _items(payload):
        thscode = _text(item.get("thscode"))
        if not thscode:
            continue
        try:
            instrument_id = from_fuyao_thscode(thscode)
        except ValueError:
            continue
        result.append(FuyaoThemeConstituent(instrument_id=instrument_id, label=_text(item.get("name"))))
    return result


def _items(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    data = payload.get("data")
    source = data if isinstance(data, Mapping) else payload
    values = source.get("item") if isinstance(source, Mapping) else None
    return [item for item in values if isinstance(item, Mapping)] if isinstance(values, list) else []


def _relative_return(
    current: float | None,
    previous: float | None,
    benchmark_current: float | None,
    benchmark_previous: float | None,
) -> float | None:
    if any(value is None or value <= 0 for value in (current, previous, benchmark_current, benchmark_previous)):
        return None
    theme_return = (current / previous - 1.0) * 100.0
    benchmark_return = (benchmark_current / benchmark_previous - 1.0) * 100.0
    return round(theme_return - benchmark_return, 6)


def _difference(value: float | None, benchmark: float | None) -> float | None:
    return round(value - benchmark, 6) if value is not None and benchmark is not None else None


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _snapshot_trade_date(snapshot: FuyaoResearchSnapshot) -> date | None:
    value = snapshot.identity.get("trade_date")
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _theme_report(payload: Mapping[str, object]) -> FuyaoThemeStrengthSnapshot | None:
    sections = payload.get("sections")
    if not isinstance(sections, Mapping):
        return None
    report = sections.get("theme_strength")
    try:
        return FuyaoThemeStrengthSnapshot.model_validate(report)
    except (TypeError, ValueError):
        return None


def _snapshot_is_usable(snapshot: FuyaoResearchSnapshot) -> bool:
    report = _theme_report(snapshot.payload)
    return report is not None and report.coverage > 0


def _response(
    identity: dict[str, object],
    report: FuyaoThemeStrengthSnapshot | None,
    errors: list[dict[str, object]],
    *,
    client: FuyaoClient,
    freshness: Literal["live", "stored", "stored_fallback", "unavailable"],
) -> dict[str, Any]:
    status = "unavailable" if report is None else "ready" if not errors else "partial"
    return {
        "provider": "fuyao",
        "research_type": "theme_strength",
        "classification": "research_only",
        "decision_weight_applied": False,
        "paper_order_side_effect": False,
        "status": status,
        "freshness": freshness,
        "identity": identity,
        "sections": {"theme_strength": report.model_dump(mode="json")} if report else {},
        "summary": {
            "headline": "Fuyao theme strength research",
            "metrics": [
                {"label": "theme_count", "value": report.catalog_count if report else 0},
                {"label": "coverage", "value": report.coverage if report else 0},
            ],
        },
        "source": {
            "request_id": _client_request_id(client),
            "data_timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "errors": errors,
    }


def _stored_response(snapshot: FuyaoResearchSnapshot, *, freshness: str) -> dict[str, Any]:
    response = dict(snapshot.payload)
    response["freshness"] = freshness
    response["snapshot"] = _snapshot_reference(snapshot)
    return response


def _snapshot_reference(snapshot: FuyaoResearchSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "payload_digest": snapshot.payload_digest,
        "observed_at": snapshot.observed_at.isoformat(),
    }


def _client_request_id(client: FuyaoClient) -> str | None:
    request = client.last_request
    return request.request_id if request is not None else None


def _error(section: str, exc: ValueError | FuyaoProviderError) -> dict[str, object]:
    error: dict[str, object] = {
        "section": section,
        "code": exc.code if isinstance(exc, FuyaoProviderError) else "invalid_request",
        "message": str(exc),
    }
    if isinstance(exc, FuyaoProviderError) and exc.request_id:
        error["request_id"] = exc.request_id
    return error
