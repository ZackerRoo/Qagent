import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.instruments import register_cn_instrument_names
from qagent.config import get_settings
from qagent.providers.datahubco import DatahubcoClient
from qagent.providers.tushare_relay import TushareRelayClient


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

_CACHE_VERSION = 2
_CACHE_TTL_SECONDS = 12 * 60 * 60
_MEMORY_CACHE: dict[tuple[bool], tuple[float, TradableInstrumentCatalog]] = {}
_DATAHUBCO_PAGE_LIMIT = 5000
_DATAHUBCO_MIN_STOCKS = 5500
_DATAHUBCO_MIN_ETFS = 1000
_CODE = re.compile(r"(\d{6})\.(SH|SZ|BJ)\Z")


def load_cn_tradable_instruments(
    *,
    include_full_etfs: bool = True,
    use_cache: bool = False,
    prefer_datahubco: bool = False,
) -> TradableInstrumentCatalog:
    if prefer_datahubco:
        try:
            return _load_datahubco_catalog(include_full_etfs=include_full_etfs)
        except (RuntimeError, ValueError) as exc:
            fallback = load_cn_tradable_instruments(
                include_full_etfs=include_full_etfs, use_cache=use_cache,
            )
            return fallback.model_copy(deep=True, update={"data_health": {
                **fallback.data_health,
                "tradable_preferred_source": "datahubco",
                "tradable_datahubco_status": "rejected",
                "tradable_datahubco_error": str(exc)[:200],
            }})
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
    stock_source_status = "live" if stock_names else "fallback"
    etf_names = _load_etf_names(errors) if include_full_etfs else FALLBACK_ETF_NAMES
    etf_source_status = (
        "live" if include_full_etfs and etf_names else "fallback" if include_full_etfs else "core"
    )
    if not stock_names:
        stock_names = FALLBACK_STOCK_NAMES
    if not etf_names:
        etf_names = FALLBACK_ETF_NAMES

    stock_names, excluded_stocks = _exclude_inactive_names(stock_names, asset_type="stock")
    etf_names, excluded_etfs = _exclude_inactive_names(etf_names, asset_type="etf")
    excluded = [*excluded_stocks, *excluded_etfs]

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
        "tradable_stock_source_status": stock_source_status,
        "tradable_etf_source_status": etf_source_status,
        "tradable_etf_coverage": "full" if include_full_etfs else "core",
        "tradable_cache": "miss" if use_cache else "off",
        "tradable_inactive_excluded": str(len(excluded)),
    }
    if excluded:
        data_health["tradable_inactive_samples"] = ",".join(
            f"CN:{symbol}:{name}" for symbol, name in excluded[:20]
        )
    if errors:
        data_health["tradable_errors"] = " | ".join(errors[:3])
    catalog = TradableInstrumentCatalog(items=items, data_health=data_health)
    if use_cache:
        _MEMORY_CACHE[cache_key] = (time.time(), catalog)
        _write_disk_cache(include_full_etfs, catalog)
    return catalog


def _datahubco_enabled() -> bool:
    settings = get_settings()
    return bool(settings.tradable_datahubco_enabled and settings.datahubco_enabled
                and settings.datahubco_key
                and settings.datahubco_allow_insecure_http)


def _build_datahubco_client() -> DatahubcoClient:
    settings = get_settings()
    if not _datahubco_enabled():
        raise ValueError("datahubco_not_enabled")
    return DatahubcoClient(settings.datahubco_key.get_secret_value(),
                           allow_insecure_http=True, timeout_seconds=30)


def _validated_code(row: dict[str, object], exchange: str, *, asset_type: str) -> str:
    raw = row.get("ts_code")
    match = _CODE.fullmatch(raw) if isinstance(raw, str) else None
    if match is None or match.group(2) != exchange:
        raise ValueError(f"invalid_{asset_type}_code")
    symbol = match.group(1)
    if _exchange(symbol) != exchange:
        raise ValueError(f"invalid_{asset_type}_exchange")
    return symbol


def _validated_name(row: dict[str, object], *, asset_type: str) -> str:
    name = row.get("name")
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError(f"invalid_{asset_type}_name")
    return name


def _valid_listing_date(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        return False
    try:
        return datetime.strptime(value, "%Y%m%d").date() <= datetime.now(
            ZoneInfo("Asia/Shanghai")
        ).date()
    except ValueError:
        return False


def _load_datahubco_catalog(*, include_full_etfs: bool) -> TradableInstrumentCatalog:
    client = _build_datahubco_client()
    stocks: dict[str, str] = {}
    for query_exchange, suffix in (("SSE", "SH"), ("SZSE", "SZ"), ("BSE", "BJ")):
        table = client.query("stock_basic", exchange=query_exchange, list_status="L",
                             limit=_DATAHUBCO_PAGE_LIMIT)
        if not table.rows or len(table.rows) >= _DATAHUBCO_PAGE_LIMIT:
            raise ValueError(f"incomplete_stock_page:{query_exchange}")
        for row in table.rows:
            # The documented stock_basic payload omits list_status even when filtered.
            if not _valid_listing_date(row.get("list_date")):
                raise ValueError("invalid_stock_listing_date")
            symbol = _validated_code(row, suffix, asset_type="stock")
            name = _validated_name(row, asset_type="stock")
            if symbol in stocks:
                raise ValueError("duplicate_stock_code")
            stocks[symbol] = name
    if len(stocks) < _DATAHUBCO_MIN_STOCKS:
        raise ValueError(f"incomplete_stock_coverage:{len(stocks)}")

    etfs: dict[str, str] = {}
    etf_source = "core"
    etf_status = "core"
    etf_error = ""
    if include_full_etfs:
        try:
            etfs = _load_promax_etfs(stocks)
            etf_source = "tushare_relay_promax_etf_basic"
            etf_status = "live"
        except (RuntimeError, ValueError) as exc:
            etf_error = str(exc)[:200]
            etfs = _load_etf_names([])
            etfs, _ = _exclude_inactive_names(etfs, asset_type="etf")
            if etfs:
                etf_source = "akshare_fund_etf_spot_em"
                etf_status = "live"
            else:
                raise ValueError(f"etf_sources_unavailable:{etf_error}") from None
    else:
        etfs = FALLBACK_ETF_NAMES.copy()
        etf_source = "core_fallback"

    names = {**stocks, **etfs}
    register_cn_instrument_names(names)
    items = [_instrument(symbol, name, "stock", "datahubco_stock_basic")
             for symbol, name in stocks.items()]
    items.extend(_instrument(symbol, name, "etf", etf_source)
                 for symbol, name in etfs.items() if symbol not in stocks)
    return TradableInstrumentCatalog(items=items, data_health={
        "tradable_market": "CN", "tradable_a_shares": str(len(stocks)),
        "tradable_etfs": str(len(etfs)), "tradable_total": str(len(items)),
        "tradable_source": "datahubco", "tradable_preferred_source": "datahubco",
        "tradable_datahubco_status": "accepted",
        "tradable_stock_source_status": "live",
        "tradable_etf_source_status": etf_status,
        "tradable_etf_source": etf_source,
        "tradable_etf_coverage": "full" if include_full_etfs else "core",
        "tradable_etf_source_error": etf_error,
        "tradable_cache": "off",
    })


def _load_promax_etfs(stocks: dict[str, str]) -> dict[str, str]:
    settings = get_settings()
    if not settings.tushare_relay_research_enabled or not settings.tushare_relay_key:
        raise ValueError("promax_not_enabled")
    client = TushareRelayClient(settings.tushare_relay_key.get_secret_value(),
                                timeout_seconds=30, retries=0)
    table = client.query("etf_basic", list_status="L", limit=_DATAHUBCO_PAGE_LIMIT,
                         fields="ts_code,extname,csname,list_status,list_date")
    if not table.rows or len(table.rows) >= _DATAHUBCO_PAGE_LIMIT:
        raise ValueError("incomplete_etf_page")
    etfs: dict[str, str] = {}
    for row in table.rows:
        code = row.get("ts_code")
        match = _CODE.fullmatch(code) if isinstance(code, str) else None
        if match is None or match.group(2) not in {"SH", "SZ"}:
            raise ValueError("invalid_etf_code")
        symbol = _validated_code(row, match.group(2), asset_type="etf")
        if row.get("list_status") != "L" or not _valid_listing_date(row.get("list_date")):
            raise ValueError("invalid_etf_listing_status")
        name = _validated_name({"name": row.get("extname") or row.get("csname")},
                               asset_type="etf")
        if symbol in etfs or symbol in stocks:
            raise ValueError("duplicate_etf_code")
        etfs[symbol] = name
    if len(etfs) < _DATAHUBCO_MIN_ETFS:
        raise ValueError(f"incomplete_etf_coverage:{len(etfs)}")
    return etfs


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
    return _normalize_code_name_frame(
        raw, ["代码", "code", "基金代码"], ["名称", "name", "基金简称"]
    )


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


def _exclude_inactive_names(
    names: dict[str, str],
    *,
    asset_type: str,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    active: dict[str, str] = {}
    excluded: list[tuple[str, str]] = []
    for symbol, name in names.items():
        if _is_inactive_name(name, asset_type=asset_type):
            excluded.append((symbol, name))
        else:
            active[symbol] = name
    return active, excluded


def _is_inactive_name(name: str, *, asset_type: str) -> bool:
    """Identify terminal exchange names without treating ordinary ST names as inactive."""

    normalized = "".join(name.upper().split())
    if asset_type != "stock":
        return False
    return normalized.startswith("退市") or normalized.endswith("退")


def _instrument(symbol: str, name: str, asset_type: str, source: str) -> TradableInstrument:
    instrument_id = f"CN:{symbol}"
    exchange = _exchange(symbol)
    return TradableInstrument(
        instrument_id=instrument_id,
        symbol=symbol,
        name=name,
        label=f"{name} {symbol}.{exchange}",
        asset_type=asset_type,
        exchange=exchange,
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
