from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
import time
from typing import Callable

import akshare as ak
import httpx
import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.cn_context import infer_etf_exposure


EASTMONEY_FUND_BASE = "https://fundf10.eastmoney.com"
ETF_EXPOSURE_CACHE_VERSION = 3
ETF_EXPOSURE_CACHE_TTL_SECONDS = 24 * 60 * 60


class EtfConstituent(BaseModel):
    instrument_id: str
    symbol: str
    name: str
    weight_pct: float


class EtfIndustryExposure(BaseModel):
    name: str
    weight_pct: float


class EtfExposureProfile(BaseModel):
    instrument_id: str
    symbol: str
    fund_name: str
    fund_type: str | None = None
    tracking_index: str | None = None
    exposure_group: str | None = None
    exposure_category: str
    market_scope: str
    style_exposure: str | None = None
    holdings: list[EtfConstituent] = Field(default_factory=list)
    holdings_as_of: date | None = None
    holdings_coverage_pct: float = 0.0
    holdings_scope: str = "unavailable"
    industries: list[EtfIndustryExposure] = Field(default_factory=list)
    industries_as_of: date | None = None
    source_provider: str = "eastmoney_fund_disclosure"
    source_url: str
    fetched_at: datetime
    data_status: str
    errors: list[str] = Field(default_factory=list)


class EtfSharedConstituent(BaseModel):
    instrument_id: str
    name: str
    minimum_weight_pct: float


class EtfExposureOverlap(BaseModel):
    left_instrument_id: str
    right_instrument_id: str
    same_tracking_index: bool
    disclosed_overlap_lower_bound_pct: float | None = None
    shared_constituents: list[EtfSharedConstituent] = Field(default_factory=list)
    status: str


class EtfExposureResponse(BaseModel):
    profiles: list[EtfExposureProfile]
    overlaps: list[EtfExposureOverlap]
    data_health: dict[str, str] = Field(default_factory=dict)


class EtfExposureService:
    def __init__(
        self,
        *,
        http_get: Callable[..., object] = httpx.get,
        industry_loader: Callable[..., pd.DataFrame] = ak.fund_portfolio_industry_allocation_em,
        cache_dir: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_ttl_seconds: int = ETF_EXPOSURE_CACHE_TTL_SECONDS,
    ) -> None:
        self.http_get = http_get
        self.industry_loader = industry_loader
        self.cache_dir = cache_dir or _default_cache_dir()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache_ttl_seconds = cache_ttl_seconds

    def build_response(
        self,
        instruments: list[tuple[str, str]],
        *,
        max_workers: int = 4,
    ) -> EtfExposureResponse:
        unique = list(dict.fromkeys(instruments))
        if not unique:
            return EtfExposureResponse(
                profiles=[],
                overlaps=[],
                data_health={
                    "etf_exposure_profiles": "0",
                    "etf_exposure_source": "eastmoney_fund_disclosure",
                },
            )
        with ThreadPoolExecutor(max_workers=min(max_workers, len(unique))) as executor:
            profiles = list(executor.map(lambda item: self.load_profile(*item), unique))
        overlaps = build_etf_overlaps(profiles)
        return EtfExposureResponse(
            profiles=profiles,
            overlaps=overlaps,
            data_health={
                "etf_exposure_profiles": str(len(profiles)),
                "etf_exposure_complete": str(
                    sum(profile.data_status == "complete" for profile in profiles)
                ),
                "etf_exposure_partial": str(
                    sum(profile.data_status == "partial" for profile in profiles)
                ),
                "etf_exposure_unavailable": str(
                    sum(profile.data_status == "unavailable" for profile in profiles)
                ),
                "etf_exposure_overlaps": str(len(overlaps)),
                "etf_exposure_source": "eastmoney_fund_disclosure",
                "etf_holdings_scope": "latest_quarterly_top10",
            },
        )

    def load_profile(self, instrument_id: str, fund_name: str) -> EtfExposureProfile:
        symbol = _cn_symbol(instrument_id)
        if symbol is None:
            return self._unavailable_profile(
                instrument_id=instrument_id,
                symbol="",
                fund_name=fund_name,
                errors=["unsupported_instrument_id"],
            )
        cached = self._read_cache(symbol)
        if cached is not None:
            return cached

        errors: list[str] = []
        metadata: dict[str, str] = {}
        holdings: list[EtfConstituent] = []
        holdings_as_of = None
        industries: list[EtfIndustryExposure] = []
        industries_as_of = None
        try:
            metadata = self._load_basic_metadata(symbol)
        except Exception as exc:
            errors.append(f"basic_metadata:{type(exc).__name__}")
        try:
            holdings, holdings_as_of = self._load_holdings(symbol)
        except Exception as exc:
            errors.append(f"holdings:{type(exc).__name__}")
        try:
            industries, industries_as_of = self._load_industries(symbol)
        except Exception as exc:
            errors.append(f"industries:{type(exc).__name__}")

        resolved_name = metadata.get("基金简称") or fund_name or instrument_id
        tracking_index = _clean_tracking_index(metadata.get("跟踪标的"))
        inferred = infer_etf_exposure(f"{resolved_name} {tracking_index or ''}")
        exposure_group = inferred.group if inferred is not None else None
        profile = EtfExposureProfile(
            instrument_id=instrument_id,
            symbol=symbol,
            fund_name=resolved_name,
            fund_type=metadata.get("基金类型") or None,
            tracking_index=tracking_index,
            exposure_group=exposure_group,
            exposure_category=_exposure_category(exposure_group),
            market_scope=_market_scope(exposure_group, metadata.get("基金类型")),
            style_exposure=inferred.theme if inferred is not None else None,
            holdings=holdings,
            holdings_as_of=holdings_as_of,
            holdings_coverage_pct=round(sum(item.weight_pct for item in holdings), 4),
            holdings_scope="latest_quarterly_top10" if holdings else "unavailable",
            industries=industries,
            industries_as_of=industries_as_of,
            source_url=f"{EASTMONEY_FUND_BASE}/jbgk_{symbol}.html",
            fetched_at=self.clock(),
            data_status=_profile_status(
                metadata,
                holdings,
                holdings_as_of,
                industries,
                industries_as_of,
            ),
            errors=errors,
        )
        self._write_cache(profile)
        return profile

    def _load_basic_metadata(self, symbol: str) -> dict[str, str]:
        url = f"{EASTMONEY_FUND_BASE}/jbgk_{symbol}.html"
        response = self.http_get(url, headers=_request_headers(symbol), timeout=15, follow_redirects=True)
        _raise_for_status(response)
        tables = pd.read_html(StringIO(str(response.text)))
        for table in tables:
            metadata = _table_key_values(table)
            if "基金代码" in metadata and ("跟踪标的" in metadata or "基金类型" in metadata):
                return metadata
        raise ValueError("fund basic metadata table is missing")

    def _load_holdings(self, symbol: str) -> tuple[list[EtfConstituent], date | None]:
        current_year = self.clock().year
        for year in (current_year, current_year - 1):
            response = self.http_get(
                f"{EASTMONEY_FUND_BASE}/FundArchivesDatas.aspx",
                params={
                    "type": "jjcc",
                    "code": symbol,
                    "topline": "10000",
                    "year": str(year),
                    "month": "",
                    "rt": "0.5",
                },
                headers=_request_headers(symbol),
                timeout=15,
                follow_redirects=True,
            )
            _raise_for_status(response)
            content = _fund_archive_content(str(response.text))
            if not content:
                continue
            tables = pd.read_html(StringIO(content), converters={"股票代码": str})
            if not tables:
                continue
            as_of_dates = [date.fromisoformat(value) for value in _DISCLOSURE_DATE_RE.findall(content)]
            holdings = _normalize_holdings(tables[0])
            if holdings:
                return holdings[:10], as_of_dates[0] if as_of_dates else None
        return [], None

    def _load_industries(self, symbol: str) -> tuple[list[EtfIndustryExposure], date | None]:
        current_year = self.clock().year
        for year in (current_year, current_year - 1):
            raw = self.industry_loader(symbol=symbol, date=str(year))
            if raw.empty:
                continue
            date_column = _optional_column(raw, ["截止时间", "报告期", "日期"])
            as_of = None
            latest = raw
            if date_column is not None:
                parsed = pd.to_datetime(raw[date_column], errors="coerce")
                if parsed.notna().any():
                    latest_value = parsed.max()
                    latest = raw.loc[parsed == latest_value]
                    as_of = latest_value.date()
            industries = _normalize_industries(latest)
            if industries:
                return industries[:8], as_of
        return [], None

    def _read_cache(self, symbol: str) -> EtfExposureProfile | None:
        path = self.cache_dir / f"{symbol}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != ETF_EXPOSURE_CACHE_VERSION:
                return None
            if time.time() - float(payload.get("created_at", 0)) > self.cache_ttl_seconds:
                return None
            return EtfExposureProfile.model_validate(payload["profile"])
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _write_cache(self, profile: EtfExposureProfile) -> None:
        path = self.cache_dir / f"{profile.symbol}.json"
        temporary = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    {
                        "version": ETF_EXPOSURE_CACHE_VERSION,
                        "created_at": time.time(),
                        "profile": profile.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _unavailable_profile(
        self,
        *,
        instrument_id: str,
        symbol: str,
        fund_name: str,
        errors: list[str],
    ) -> EtfExposureProfile:
        return EtfExposureProfile(
            instrument_id=instrument_id,
            symbol=symbol,
            fund_name=fund_name or instrument_id,
            exposure_category="unknown",
            market_scope="未知",
            source_url=f"{EASTMONEY_FUND_BASE}/jbgk_{symbol}.html" if symbol else "",
            fetched_at=self.clock(),
            data_status="unavailable",
            errors=errors,
        )


def build_etf_overlaps(profiles: list[EtfExposureProfile]) -> list[EtfExposureOverlap]:
    overlaps: list[EtfExposureOverlap] = []
    for left_index, left in enumerate(profiles):
        for right in profiles[left_index + 1 :]:
            same_tracking_index = bool(
                left.tracking_index
                and right.tracking_index
                and left.tracking_index == right.tracking_index
            )
            left_holdings = {item.instrument_id: item for item in left.holdings}
            right_holdings = {item.instrument_id: item for item in right.holdings}
            shared = []
            for instrument_id in left_holdings.keys() & right_holdings.keys():
                minimum_weight = min(
                    left_holdings[instrument_id].weight_pct,
                    right_holdings[instrument_id].weight_pct,
                )
                shared.append(
                    EtfSharedConstituent(
                        instrument_id=instrument_id,
                        name=left_holdings[instrument_id].name
                        or right_holdings[instrument_id].name,
                        minimum_weight_pct=round(minimum_weight, 4),
                    )
                )
            shared.sort(key=lambda item: (-item.minimum_weight_pct, item.instrument_id))
            overlap_lower_bound = (
                round(sum(item.minimum_weight_pct for item in shared), 4)
                if left.holdings and right.holdings
                else None
            )
            status = "measured" if overlap_lower_bound is not None else "unavailable"
            overlaps.append(
                EtfExposureOverlap(
                    left_instrument_id=left.instrument_id,
                    right_instrument_id=right.instrument_id,
                    same_tracking_index=same_tracking_index,
                    disclosed_overlap_lower_bound_pct=overlap_lower_bound,
                    shared_constituents=shared[:5],
                    status=status,
                )
            )
    overlaps.sort(
        key=lambda item: (
            not item.same_tracking_index,
            -(item.disclosed_overlap_lower_bound_pct or -1),
            item.left_instrument_id,
            item.right_instrument_id,
        )
    )
    return overlaps


def _default_cache_dir() -> Path:
    root = Path(os.getenv("QAGENT_ETF_EXPOSURE_CACHE_DIR", ".qagent-cache/etf-exposure"))
    return root / f"v{ETF_EXPOSURE_CACHE_VERSION}"


def _request_headers(symbol: str) -> dict[str, str]:
    return {
        "Referer": f"{EASTMONEY_FUND_BASE}/ccmx_{symbol}.html",
        "User-Agent": "Mozilla/5.0 (compatible; Qagent/1.0; ETF exposure research)",
    }


def _raise_for_status(response: object) -> None:
    method = getattr(response, "raise_for_status", None)
    if callable(method):
        method()


def _table_key_values(table: pd.DataFrame) -> dict[str, str]:
    values: dict[str, str] = {}
    for _, row in table.iterrows():
        cells = [_text(value) for value in row.tolist()]
        for index in range(0, len(cells) - 1, 2):
            key = cells[index]
            value = cells[index + 1]
            if key and value:
                values[key] = value
    return values


def _fund_archive_content(payload: str) -> str:
    start = payload.find("{")
    end = payload.rfind("}")
    if start < 0 or end <= start:
        return ""
    from akshare.utils import demjson

    decoded = demjson.decode(payload[start : end + 1])
    content = decoded.get("content") if isinstance(decoded, dict) else None
    return str(content or "")


def _normalize_holdings(table: pd.DataFrame) -> list[EtfConstituent]:
    code_column = _optional_column(table, ["股票代码", "证券代码", "成分券代码"])
    name_column = _optional_column(table, ["股票名称", "证券简称", "成分券名称"])
    weight_column = _optional_column(table, ["占净值 比例", "占净值比例", "权重"])
    if code_column is None or name_column is None or weight_column is None:
        return []
    holdings = []
    for _, row in table.iterrows():
        symbol = _digits(row.get(code_column))
        weight = _percentage(row.get(weight_column))
        if len(symbol) != 6 or weight is None:
            continue
        holdings.append(
            EtfConstituent(
                instrument_id=f"CN:{symbol}",
                symbol=symbol,
                name=_text(row.get(name_column)),
                weight_pct=weight,
            )
        )
    holdings.sort(key=lambda item: (-item.weight_pct, item.symbol))
    return holdings


def _normalize_industries(table: pd.DataFrame) -> list[EtfIndustryExposure]:
    name_column = _optional_column(table, ["行业类别", "行业名称", "行业"])
    weight_column = _optional_column(table, ["占净值比例", "占比", "权重"])
    if name_column is None or weight_column is None:
        return []
    industries = []
    for _, row in table.iterrows():
        name = _text(row.get(name_column))
        weight = _percentage(row.get(weight_column))
        if not name or weight is None:
            continue
        industries.append(EtfIndustryExposure(name=name, weight_pct=weight))
    industries.sort(key=lambda item: (-item.weight_pct, item.name))
    return industries


def _optional_column(table: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {str(column).replace(" ", "").strip().lower(): str(column) for column in table.columns}
    for candidate in candidates:
        match = normalized.get(candidate.replace(" ", "").strip().lower())
        if match is not None:
            return match
    return None


def _clean_tracking_index(value: str | None) -> str | None:
    normalized = _text(value)
    return normalized if normalized and normalized not in {"--", "---"} else None


def _profile_status(
    metadata: dict[str, str],
    holdings: list[EtfConstituent],
    holdings_as_of: date | None,
    industries: list[EtfIndustryExposure],
    industries_as_of: date | None,
) -> str:
    if metadata and holdings and holdings_as_of and industries and industries_as_of:
        return "complete"
    if metadata or holdings or industries:
        return "partial"
    return "unavailable"


def _exposure_category(group: str | None) -> str:
    if not group:
        return "unknown"
    if group.startswith("宽基ETF:"):
        return "broad"
    if group.startswith(("策略ETF:", "主题ETF:")):
        return "strategy"
    if group.startswith("跨境ETF:"):
        return "cross_border"
    if group.startswith("商品ETF:"):
        return "commodity"
    if group.startswith("债券ETF:") or group == "货币ETF":
        return "fixed_income"
    return "industry"


def _market_scope(group: str | None, fund_type: str | None) -> str:
    if group and group.startswith("跨境ETF:"):
        return group.split(":", 1)[1]
    if group and group.startswith("商品ETF:"):
        return "商品"
    if group and (group.startswith("债券ETF:") or group == "货币ETF"):
        return "中国固收/现金"
    if fund_type and "海外" in fund_type:
        return "海外"
    if group:
        return "中国A股"
    return "未知"


def _cn_symbol(instrument_id: str) -> str | None:
    normalized = instrument_id.strip().upper()
    if not normalized.startswith("CN:"):
        return None
    symbol = normalized.split(":", 1)[1]
    return symbol if len(symbol) == 6 and symbol.isdigit() else None


def _digits(value: object) -> str:
    text = _text(value)
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(character for character in text if character.isdigit())
    return digits.zfill(6) if digits else ""


def _percentage(value: object) -> float | None:
    text = _text(value).replace("%", "").replace(",", "")
    try:
        return round(float(text), 4)
    except ValueError:
        return None


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


_DISCLOSURE_DATE_RE = re.compile(
    r"截止至[：:].{0,160}?(\d{4}-\d{2}-\d{2})",
    re.DOTALL,
)
