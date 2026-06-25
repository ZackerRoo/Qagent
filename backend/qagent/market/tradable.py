import json
import os
import time
from pathlib import Path

import akshare as ak
import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.instruments import format_instrument_label, register_cn_instrument_names


class TradableInstrument(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    label: str
    asset_type: str
    exchange: str
    source: str


class TradableInstrumentCatalog(BaseModel):
    items: list[TradableInstrument]
    data_health: dict[str, str] = Field(default_factory=dict)


FALLBACK_STOCK_NAMES = {
    "000001": "平安银行",
    "000063": "中兴通讯",
    "300750": "宁德时代",
    "600519": "贵州茅台",
    "688981": "中芯国际",
}

FALLBACK_ETF_NAMES = {
    "588000": "科创50ETF",
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "512100": "中证1000ETF",
    "159949": "创业板50ETF",
}

_CACHE_VERSION = 1
_CACHE_TTL_SECONDS = 12 * 60 * 60
_MEMORY_CACHE: dict[tuple[bool], tuple[float, TradableInstrumentCatalog]] = {}


def load_cn_tradable_instruments(
    *,
    include_full_etfs: bool = True,
    use_cache: bool = False,
) -> TradableInstrumentCatalog:
    cache_key = (include_full_etfs,)
    if use_cache:
        cached = _read_memory_cache(cache_key)
        if cached is not None:
            return cached
        cached = _read_disk_cache(include_full_etfs)
        if cached is not None:
            _MEMORY_CACHE[cache_key] = (time.time(), cached)
            return _with_cache_status(cached, "disk")

    errors: list[str] = []
    stock_names = _load_a_share_names(errors)
    etf_names = _load_etf_names(errors) if include_full_etfs else FALLBACK_ETF_NAMES
    if not stock_names:
        stock_names = FALLBACK_STOCK_NAMES
    if not etf_names:
        etf_names = FALLBACK_ETF_NAMES

    merged_names = {**stock_names, **etf_names}
    register_cn_instrument_names(merged_names)

    items = [
        _instrument(symbol, name, "stock", "akshare_stock_info_a_code_name")
        for symbol, name in stock_names.items()
    ]
    items.extend(
        _instrument(symbol, name, "etf", "akshare_fund_etf_spot_em")
        for symbol, name in etf_names.items()
        if symbol not in stock_names
    )

    data_health = {
        "tradable_market": "CN",
        "tradable_a_shares": str(len(stock_names)),
        "tradable_etfs": str(len(etf_names)),
        "tradable_total": str(len(items)),
        "tradable_source": "akshare",
        "tradable_etf_coverage": "full" if include_full_etfs else "core",
        "tradable_cache": "miss" if use_cache else "off",
    }
    if errors:
        data_health["tradable_errors"] = " | ".join(errors[:3])
    catalog = TradableInstrumentCatalog(items=items, data_health=data_health)
    if use_cache:
        _MEMORY_CACHE[cache_key] = (time.time(), catalog)
        _write_disk_cache(include_full_etfs, catalog)
    return catalog


def search_cn_tradable_instruments(
    query: str = "",
    limit: int = 50,
    *,
    include_full_etfs: bool = True,
    use_cache: bool = False,
) -> TradableInstrumentCatalog:
    catalog = load_cn_tradable_instruments(
        include_full_etfs=include_full_etfs,
        use_cache=use_cache,
    )
    normalized = query.strip().upper()
    if normalized:
        items = [item for item in catalog.items if _matches(item, normalized)]
        items.sort(key=lambda item: _match_rank(item, normalized))
    else:
        items = catalog.items
    capped = items[: max(limit, 0)]
    return TradableInstrumentCatalog(
        items=capped,
        data_health={
            **catalog.data_health,
            "tradable_matched": str(len(items)),
            "tradable_returned": str(len(capped)),
        },
    )


def _load_a_share_names(errors: list[str]) -> dict[str, str]:
    try:
        raw = ak.stock_info_a_code_name()
    except Exception as exc:
        errors.append(f"a_share_names: {exc}")
        return {}
    return _normalize_code_name_frame(raw, ["code", "代码", "symbol"], ["name", "名称"])


def _load_etf_names(errors: list[str]) -> dict[str, str]:
    try:
        raw = ak.fund_etf_spot_em()
    except Exception as exc:
        errors.append(f"etf_names: {exc}")
        return {}
    return _normalize_code_name_frame(raw, ["代码", "code", "基金代码"], ["名称", "name", "基金简称"])


def _normalize_code_name_frame(
    raw: pd.DataFrame,
    code_candidates: list[str],
    name_candidates: list[str],
) -> dict[str, str]:
    if raw.empty:
        return {}
    code_col = _column(raw, code_candidates)
    name_col = _column(raw, name_candidates)
    names: dict[str, str] = {}
    for _, row in raw.iterrows():
        symbol = _symbol(row.get(code_col))
        name = _text(row.get(name_col))
        if len(symbol) == 6 and symbol.isdigit() and name:
            names[symbol] = name
    return names


def _instrument(symbol: str, name: str, asset_type: str, source: str) -> TradableInstrument:
    instrument_id = f"CN:{symbol}"
    return TradableInstrument(
        instrument_id=instrument_id,
        symbol=symbol,
        name=name,
        label=format_instrument_label(instrument_id),
        asset_type=asset_type,
        exchange=_exchange(symbol),
        source=source,
    )


def _matches(item: TradableInstrument, query: str) -> bool:
    haystack = " ".join(
        [
            item.instrument_id,
            item.symbol,
            item.name,
            item.label,
            f"{item.symbol}.{item.exchange}",
            item.asset_type,
        ]
    ).upper()
    return query in haystack


def _match_rank(item: TradableInstrument, query: str) -> tuple[int, int, int, str]:
    symbol = item.symbol.upper()
    name = item.name.upper()
    label = item.label.upper()
    token = item.instrument_id.upper()
    exchange_label = f"{symbol}.{item.exchange}".upper()
    asset_rank = 0 if item.asset_type == "etf" else 1

    if query in {symbol, exchange_label, token}:
        return (0, asset_rank, 0, symbol)
    if query in {name, label}:
        return (1, asset_rank, len(name), symbol)
    if symbol.startswith(query):
        return (2, asset_rank, len(symbol), symbol)
    if name.startswith(query):
        return (3, asset_rank, len(name), symbol)
    if label.startswith(query):
        return (4, asset_rank, len(label), symbol)
    if query in name:
        return (5, asset_rank, name.index(query), symbol)
    if query in label:
        return (6, asset_rank, label.index(query), symbol)
    return (9, asset_rank, len(label), symbol)


def _column(frame: pd.DataFrame, candidates: list[str]) -> str:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    raise ValueError(f"missing required tradable instrument column: {candidates[0]}")


def _symbol(value: object) -> str:
    text = _text(value)
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


def _exchange(symbol: str) -> str:
    if symbol.startswith(("4", "8", "920")):
        return "BJ"
    if symbol.startswith(("5", "6")):
        return "SH"
    return "SZ"


def _read_memory_cache(
    cache_key: tuple[bool],
) -> TradableInstrumentCatalog | None:
    cached = _MEMORY_CACHE.get(cache_key)
    if cached is None:
        return None
    created_at, catalog = cached
    if time.time() - created_at > _CACHE_TTL_SECONDS:
        _MEMORY_CACHE.pop(cache_key, None)
        return None
    return _with_cache_status(catalog, "memory")


def _read_disk_cache(include_full_etfs: bool) -> TradableInstrumentCatalog | None:
    path = _cache_path(include_full_etfs)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != _CACHE_VERSION:
            return None
        created_at = float(payload.get("created_at", 0))
        if time.time() - created_at > _CACHE_TTL_SECONDS:
            return None
        catalog = TradableInstrumentCatalog(
            items=[TradableInstrument(**item) for item in payload.get("items", [])],
            data_health=dict(payload.get("data_health", {})),
        )
    except (OSError, TypeError, ValueError):
        return None
    _register_catalog_names(catalog)
    return catalog


def _write_disk_cache(include_full_etfs: bool, catalog: TradableInstrumentCatalog) -> None:
    path = _cache_path(include_full_etfs)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": _CACHE_VERSION,
                    "created_at": time.time(),
                    "include_full_etfs": include_full_etfs,
                    "items": [item.model_dump(mode="json") for item in catalog.items],
                    "data_health": catalog.data_health,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


def _cache_path(include_full_etfs: bool) -> Path:
    root = Path(os.getenv("QAGENT_TRADABLE_CACHE_DIR", ".qagent-cache"))
    suffix = "full_etf" if include_full_etfs else "core_etf"
    return root / f"cn_tradable_{suffix}.json"


def _with_cache_status(
    catalog: TradableInstrumentCatalog,
    status: str,
) -> TradableInstrumentCatalog:
    _register_catalog_names(catalog)
    return catalog.model_copy(
        deep=True,
        update={"data_health": {**catalog.data_health, "tradable_cache": status}},
    )


def _register_catalog_names(catalog: TradableInstrumentCatalog) -> None:
    register_cn_instrument_names({item.symbol: item.name for item in catalog.items})
