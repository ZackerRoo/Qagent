from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

import akshare as ak
import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.cn_universe_tokens import is_cn_universe_token, resolve_cn_universe_token
from qagent.market.instruments import register_cn_instrument_names
from qagent.market.universe import DEFAULT_A_SHARE_STARTER_UNIVERSE


CN_ALL_TOKEN = "CN:ALL"
DEFAULT_A_SHARE_SCAN_LIMIT = 120
DEFAULT_A_SHARE_MIN_PRICE = Decimal("1")
DEFAULT_A_SHARE_MIN_TURNOVER = Decimal("50000000")


class AShareUniverseSelection(BaseModel):
    symbols: list[str]
    names: dict[str, str] = Field(default_factory=dict)
    total_count: int
    eligible_count: int
    selected_count: int
    source: str
    filters: list[str] = Field(default_factory=list)
    excluded_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ResolvedSymbols(BaseModel):
    symbols: list[str]
    data_health: dict[str, str] = Field(default_factory=dict)
    is_dynamic: bool = False


def build_a_share_universe(
    limit: int = DEFAULT_A_SHARE_SCAN_LIMIT,
    min_price: Decimal | int | str = DEFAULT_A_SHARE_MIN_PRICE,
    min_turnover: Decimal | int | str = DEFAULT_A_SHARE_MIN_TURNOVER,
) -> AShareUniverseSelection:
    raw = ak.stock_zh_a_spot_em()
    records = _normalize_spot_frame(raw)
    total = len(records)
    excluded: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    price_floor = _decimal(min_price) or DEFAULT_A_SHARE_MIN_PRICE
    turnover_floor = _decimal(min_turnover) or DEFAULT_A_SHARE_MIN_TURNOVER

    for record in records:
        reason = _exclusion_reason(record, price_floor, turnover_floor)
        if reason:
            excluded[reason] += 1
            continue
        eligible.append(record)

    eligible.sort(key=lambda item: item["turnover"] or Decimal("0"), reverse=True)
    selected = eligible[: max(limit, 0)]
    names = {item["symbol"]: item["name"] for item in records if item["name"]}
    register_cn_instrument_names(names)

    return AShareUniverseSelection(
        symbols=[f"CN:{item['symbol']}" for item in selected],
        names=names,
        total_count=total,
        eligible_count=len(eligible),
        selected_count=len(selected),
        source="akshare_spot_em",
        filters=[
            "exclude ST/*ST/delisting-risk names",
            f"latest price >= {price_floor}",
            f"turnover >= {turnover_floor}",
            f"top {max(limit, 0)} by turnover",
        ],
        excluded_counts=dict(excluded),
    )


def resolve_symbol_tokens(
    symbols: list[str],
    limit: int = DEFAULT_A_SHARE_SCAN_LIMIT,
    min_price: Decimal | int | str = DEFAULT_A_SHARE_MIN_PRICE,
    min_turnover: Decimal | int | str = DEFAULT_A_SHARE_MIN_TURNOVER,
) -> ResolvedSymbols:
    normalized = _dedupe_symbols(symbols)
    token_symbols = [
        symbol for symbol in normalized if symbol == CN_ALL_TOKEN or is_cn_universe_token(symbol)
    ]
    if not token_symbols:
        return ResolvedSymbols(symbols=normalized)

    manual = [
        symbol
        for symbol in normalized
        if symbol != CN_ALL_TOKEN and not is_cn_universe_token(symbol)
    ]
    resolved = list(manual)
    health_items: list[dict[str, str]] = []
    is_dynamic = False
    for token in token_symbols:
        token_resolution = (
            _resolve_all_a_share_token(limit, min_price, min_turnover)
            if token == CN_ALL_TOKEN
            else resolve_cn_universe_token(token, limit=limit)
        )
        resolved.extend(token_resolution.symbols)
        health_items.append(token_resolution.data_health)
        is_dynamic = is_dynamic or token_resolution.is_dynamic

    return ResolvedSymbols(
        symbols=_dedupe_symbols(resolved),
        data_health=_merge_universe_health(health_items),
        is_dynamic=is_dynamic,
    )


def _resolve_all_a_share_token(
    limit: int,
    min_price: Decimal | int | str,
    min_turnover: Decimal | int | str,
) -> ResolvedSymbols:
    try:
        selection = build_a_share_universe(
            limit=limit,
            min_price=min_price,
            min_turnover=min_turnover,
        )
    except Exception as exc:
        fallback = DEFAULT_A_SHARE_STARTER_UNIVERSE[: max(limit, 0)]
        return ResolvedSymbols(
            symbols=fallback,
            data_health={
                "universe": CN_ALL_TOKEN,
                "universe_source": "fallback",
                "universe_selected": str(len(fallback)),
                "universe_limit": str(limit),
                "universe_fallback": "cn_liquid_starter",
                "universe_error": str(exc),
            },
            is_dynamic=True,
        )
    data_health = {
        "universe": CN_ALL_TOKEN,
        "universe_source": selection.source,
        "universe_total": str(selection.total_count),
        "universe_eligible": str(selection.eligible_count),
        "universe_selected": str(selection.selected_count),
        "universe_limit": str(limit),
        "universe_filters": "; ".join(selection.filters),
    }
    if selection.excluded_counts:
        data_health["universe_excluded"] = ", ".join(
            f"{key}:{value}" for key, value in sorted(selection.excluded_counts.items())
        )
    if selection.warnings:
        data_health["universe_warnings"] = " | ".join(selection.warnings[:3])
    return ResolvedSymbols(symbols=selection.symbols, data_health=data_health, is_dynamic=True)


def _merge_universe_health(items: list[dict[str, str]]) -> dict[str, str]:
    if not items:
        return {}
    if len(items) == 1:
        return items[0]
    selected = 0
    for item in items:
        try:
            selected += int(item.get("universe_selected", "0"))
        except ValueError:
            pass
    merged = {
        "universe": ",".join(item.get("universe", "") for item in items if item.get("universe")),
        "universe_label": "、".join(
            item.get("universe_label", item.get("universe", "")) for item in items
        ),
        "universe_source": ",".join(
            item.get("universe_source", "") for item in items if item.get("universe_source")
        ),
        "universe_selected": str(selected),
    }
    errors = [item["universe_error"] for item in items if item.get("universe_error")]
    fallbacks = [item["universe_fallback"] for item in items if item.get("universe_fallback")]
    if errors:
        merged["universe_error"] = " | ".join(errors)
    if fallbacks:
        merged["universe_fallback"] = ",".join(fallbacks)
    return merged


def _normalize_spot_frame(raw: pd.DataFrame) -> list[dict[str, Any]]:
    if raw.empty:
        return []
    code_col = _column(raw, ["代码", "code", "symbol"])
    name_col = _column(raw, ["名称", "name"])
    price_col = _column(raw, ["最新价", "最新", "price", "close"])
    turnover_col = _column(raw, ["成交额", "turnover", "amount"])
    change_col = _optional_column(raw, ["涨跌幅", "change_pct"])
    records: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        symbol = _symbol(row.get(code_col))
        name = _text(row.get(name_col))
        if not symbol:
            records.append(
                {
                    "symbol": "",
                    "name": name,
                    "price": None,
                    "turnover": None,
                    "change_pct": None,
                }
            )
            continue
        records.append(
            {
                "symbol": symbol,
                "name": name,
                "price": _decimal(row.get(price_col)),
                "turnover": _decimal(row.get(turnover_col)),
                "change_pct": _decimal(row.get(change_col)) if change_col else None,
            }
        )
    return records


def _exclusion_reason(
    record: dict[str, Any],
    min_price: Decimal,
    min_turnover: Decimal,
) -> str | None:
    symbol = record["symbol"]
    name = record["name"]
    price = record["price"]
    turnover = record["turnover"]
    if len(symbol) != 6 or not symbol.isdigit():
        return "invalid_code"
    upper_name = name.upper()
    if "ST" in upper_name or "退" in name:
        return "st_or_delisting"
    if price is None or price < min_price:
        return "low_price"
    if turnover is None or turnover < min_turnover:
        return "low_turnover"
    return None


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    resolved = []
    seen = set()
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return resolved


def _column(frame: pd.DataFrame, candidates: list[str]) -> str:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    raise ValueError(f"missing required A-share universe column: {candidates[0]}")


def _optional_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    return None


def _symbol(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(6) if digits else ""


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _decimal(value: object) -> Decimal | None:
    text = _text(value).replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None
