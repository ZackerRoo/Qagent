from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import baostock as bs

from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndexMembership,
    HistoricalIndexSnapshot,
    HistoricalIndustrySnapshot,
    HistoricalInventoryManifest,
    HistoricalInstrumentProfile,
    HistoricalTradabilityPoint,
    normalize_and_validate_historical_profile,
)
from qagent.market.calendars import trading_sessions_in_range
from qagent.providers.baostock_session import serialized_baostock_session
from qagent.providers.free_cn import FreeCnMarketDataProvider, _bounded_network_calls
from qagent.strategy_data.models import FundamentalSnapshot
from qagent.strategy_data.providers import BaseStrategyDataProvider


INDEX_QUERIES = {
    "CN:000016.IDX": "query_sz50_stocks",
    "CN:000300.IDX": "query_hs300_stocks",
    "CN:000905.IDX": "query_zz500_stocks",
}
REQUIRED_BENCHMARK_IDS = (
    "CN:000300.IDX",
    "CN:000905.IDX",
    "CN:399006.IDX",
    "CN:000688.IDX",
)
BAOSTOCK_INVENTORY_TYPES = {"1": "stock", "5": "etf"}
BAOSTOCK_NON_INVENTORY_TYPES = {"2", "3", "4"}
BAOSTOCK_LISTING_STATUSES = {"0": "delisted", "1": "active"}


class HistoricalEvidenceProvider(Protocol):
    name: str
    last_errors: list[str]

    def get_evidence(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> HistoricalEvidenceBundle:
        ...

    def list_historical_instruments(
        self,
        effective_through: date,
    ) -> list[HistoricalInstrumentProfile]:
        ...

    def get_lifecycle_manifest(self) -> HistoricalInventoryManifest:
        ...

    def get_benchmark_series(
        self,
        ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, Any]:
        ...


class BaoStockHistoricalEvidenceProvider:
    name = "baostock_historical_evidence"

    def __init__(
        self,
        client=bs,
        request_timeout_seconds: int = 6,
        *,
        benchmark_provider=None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.request_timeout_seconds = request_timeout_seconds
        self.benchmark_provider = benchmark_provider or FreeCnMarketDataProvider(
            request_timeout_seconds=request_timeout_seconds
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.last_errors: list[str] = []
        self._lifecycle_manifest = HistoricalInventoryManifest(
            status="partial",
            expected_count=None,
            effective_through=date.min,
            error="historical inventory has not been requested",
            fetched_at=self._clock(),
            source_provider="baostock",
        )

    def list_historical_instruments(
        self,
        effective_through: date,
    ) -> list[HistoricalInstrumentProfile]:
        fetched_at = self._clock()
        self.last_errors = []
        rows: list[dict[str, str]] = []
        response_complete = False
        logged_in = False
        with serialized_baostock_session():
            try:
                login = self._call(self.client.login)
                if getattr(login, "error_code", "1") != "0":
                    self.last_errors.append(
                        "baostock inventory login: "
                        f"{getattr(login, 'error_msg', 'login failed')}"
                    )
                else:
                    logged_in = True
                    result = self._call(
                        self.client.query_stock_basic,
                        code="",
                        code_name="",
                    )
                    response_complete = getattr(result, "error_code", "1") == "0"
                    rows = self._rows(result, "historical inventory")
            except Exception as exc:
                self.last_errors.append(f"historical inventory: {exc}")
            finally:
                if logged_in:
                    try:
                        self._call(self.client.logout)
                    except Exception as exc:
                        self.last_errors.append(f"baostock inventory logout: {exc}")

        profiles: list[HistoricalInstrumentProfile] = []
        expected_count = 0 if response_complete else None
        for row_number, row in enumerate(rows, start=1):
            raw_security_type = _text(row.get("type"))
            if raw_security_type in BAOSTOCK_NON_INVENTORY_TYPES:
                continue
            if raw_security_type not in BAOSTOCK_INVENTORY_TYPES:
                expected_count = None
                self.last_errors.append(
                    f"historical inventory row {row_number}: unknown security type"
                )
                continue
            if expected_count is not None:
                expected_count += 1
            instrument_id = _from_baostock_code(row.get("code"))
            if instrument_id is None:
                self.last_errors.append(
                    f"historical inventory row {row_number}: invalid instrument code"
                )
                continue
            listing_date = _date(row.get("ipoDate"))
            raw_listing_status = _text(row.get("status"))
            listing_status = BAOSTOCK_LISTING_STATUSES.get(raw_listing_status or "")
            delisting_date = _date(row.get("outDate"))
            if listing_date is None:
                self.last_errors.append(
                    f"historical inventory {instrument_id}: unknown listing date"
                )
            if listing_status is None:
                self.last_errors.append(
                    f"historical inventory {instrument_id}: unknown listing status"
                )
            if listing_status == "delisted" and delisting_date is None:
                self.last_errors.append(
                    f"historical inventory {instrument_id}: unknown delisting date"
                )
            profile, profile_errors = normalize_and_validate_historical_profile(
                HistoricalInstrumentProfile(
                    instrument_id=instrument_id,
                    name=_text(row.get("code_name")),
                    snapshot_date=effective_through,
                    listing_date=listing_date,
                    delisting_date=delisting_date,
                    security_type=BAOSTOCK_INVENTORY_TYPES[raw_security_type],
                    listing_status=listing_status,
                    provider="baostock",
                ),
                effective_through,
            )
            self.last_errors.extend(
                f"historical inventory {error}" for error in profile_errors
            )
            if not profile_errors:
                profiles.append(profile)

        if expected_count == 0:
            self.last_errors.append("historical inventory: empty provider response")
        if expected_count is not None and len(profiles) != expected_count:
            self.last_errors.append(
                "historical inventory count mismatch: "
                f"expected={expected_count}, normalized={len(profiles)}"
            )
        error = "; ".join(dict.fromkeys(self.last_errors)) or None
        status = (
            "ready"
            if expected_count is not None
            and expected_count > 0
            and len(profiles) == expected_count
            and error is None
            else "partial"
        )
        self._lifecycle_manifest = HistoricalInventoryManifest(
            status=status,
            expected_count=expected_count,
            effective_through=effective_through,
            error=error,
            fetched_at=fetched_at,
            source_provider="baostock",
        )
        return profiles

    def get_lifecycle_manifest(self) -> HistoricalInventoryManifest:
        return self._lifecycle_manifest

    def get_benchmark_series(
        self,
        ids: list[str],
        start: date,
        end: date,
    ) -> dict[str, Any]:
        normalized_ids = list(dict.fromkeys(item.strip().upper() for item in ids))
        unsupported = [
            index_id
            for index_id in normalized_ids
            if index_id not in REQUIRED_BENCHMARK_IDS
        ]
        if unsupported:
            raise ValueError(
                "unsupported benchmark ID: " + ", ".join(unsupported)
            )
        series: dict[str, Any] = {}
        for index_id in normalized_ids:
            try:
                series[index_id] = self.benchmark_provider.get_daily_bars(
                    [index_id], start, end
                )
            except Exception as exc:
                self.last_errors.append(f"benchmark {index_id}: {exc}")
        return series

    def get_evidence(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> HistoricalEvidenceBundle:
        symbols = sorted({item for item in instrument_ids if item.startswith("CN:")})
        selected = set(symbols)
        snapshot_dates = historical_snapshot_dates(start, end)
        bundle = HistoricalEvidenceBundle()
        self.last_errors = []
        if not symbols:
            return bundle

        with serialized_baostock_session():
            login = self._call(self.client.login)
            if getattr(login, "error_code", "1") != "0":
                message = getattr(login, "error_msg", "login failed")
                bundle.errors.append(f"baostock login: {message}")
                self.last_errors = list(bundle.errors)
                return bundle
            try:
                bundle.tradability.extend(
                    self._load_tradability(instrument_id, start, end)
                    for instrument_id in symbols
                )
                bundle.tradability = [
                    point
                    for group in bundle.tradability
                    for point in (group if isinstance(group, list) else [group])
                ]
                bundle.profiles = self._load_profiles(end)
                for snapshot_date in snapshot_dates:
                    bundle.industries.extend(
                        self._load_industries(selected, snapshot_date)
                    )
                    snapshots, memberships = self._load_index_snapshots(
                        selected,
                        snapshot_date,
                    )
                    bundle.index_snapshots.extend(snapshots)
                    bundle.index_memberships.extend(memberships)
            finally:
                self._call(self.client.logout)

        bundle.errors = list(self.last_errors)
        bundle.data_health = {
            "historical_evidence_provider": self.name,
            "historical_evidence_tradability": str(len(bundle.tradability)),
            "historical_evidence_profiles": str(len(bundle.profiles)),
            "historical_evidence_industries": str(len(bundle.industries)),
            "historical_evidence_index_snapshots": str(len(bundle.index_snapshots)),
            "historical_evidence_index_memberships": str(
                len(bundle.index_memberships)
            ),
            "historical_evidence_errors": str(len(bundle.errors)),
        }
        return bundle

    def _load_tradability(
        self,
        instrument_id: str,
        start: date,
        end: date,
    ) -> list[HistoricalTradabilityPoint]:
        result = self._call(
            self.client.query_history_k_data_plus,
            _to_baostock_code(instrument_id),
            "date,code,tradestatus,pctChg,isST",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            frequency="d",
            adjustflag="2",
        )
        rows = self._rows(result, f"tradability {instrument_id}")
        return [
            HistoricalTradabilityPoint(
                instrument_id=instrument_id,
                trade_date=_date(row.get("date")) or start,
                trading_status=(
                    "trading" if _text(row.get("tradestatus")) == "1" else "suspended"
                ),
                is_st=_bool_flag(row.get("isST")),
                pct_change_pct=_float(row.get("pctChg")),
                provider="baostock",
            )
            for row in rows
            if _date(row.get("date")) is not None
        ]

    def _load_profiles(
        self,
        snapshot_date: date,
    ) -> list[HistoricalInstrumentProfile]:
        result = self._call(self.client.query_stock_basic, code="", code_name="")
        rows = self._rows(result, "instrument profiles")
        profiles: list[HistoricalInstrumentProfile] = []
        for row in rows:
            instrument_id = _from_baostock_code(row.get("code"))
            security_type = _text(row.get("type"))
            if instrument_id is None or security_type not in {"1", "5"}:
                continue
            profiles.append(
                HistoricalInstrumentProfile(
                    instrument_id=instrument_id,
                    name=_text(row.get("code_name")),
                    snapshot_date=snapshot_date,
                    listing_date=_date(row.get("ipoDate")),
                    delisting_date=_date(row.get("outDate")),
                    security_type=security_type,
                    listing_status=_text(row.get("status")),
                    provider="baostock",
                )
            )
        return profiles

    def _load_industries(
        self,
        selected: set[str],
        snapshot_date: date,
    ) -> list[HistoricalIndustrySnapshot]:
        result = self._call(
            self.client.query_stock_industry,
            code="",
            date=snapshot_date.isoformat(),
        )
        rows = self._rows(result, f"industry {snapshot_date.isoformat()}")
        snapshots: list[HistoricalIndustrySnapshot] = []
        for row in rows:
            instrument_id = _from_baostock_code(row.get("code"))
            industry = _text(row.get("industry"))
            if instrument_id not in selected or not industry:
                continue
            snapshots.append(
                HistoricalIndustrySnapshot(
                    instrument_id=instrument_id,
                    snapshot_date=_date(row.get("updateDate")) or snapshot_date,
                    industry=industry,
                    classification=_text(row.get("industryClassification")),
                    provider="baostock",
                )
            )
        return snapshots

    def _load_index_snapshots(
        self,
        selected: set[str],
        snapshot_date: date,
    ) -> tuple[list[HistoricalIndexSnapshot], list[HistoricalIndexMembership]]:
        snapshots: list[HistoricalIndexSnapshot] = []
        memberships: list[HistoricalIndexMembership] = []
        for index_id, method_name in INDEX_QUERIES.items():
            result = self._call(
                getattr(self.client, method_name),
                date=snapshot_date.isoformat(),
            )
            rows = self._rows(
                result,
                f"index {index_id} {snapshot_date.isoformat()}",
                record_error=False,
            )
            error_code = getattr(result, "error_code", "1")
            error = None if error_code == "0" else getattr(result, "error_msg", "query failed")
            if error is None and not rows:
                error = "empty membership snapshot"
            snapshots.append(
                HistoricalIndexSnapshot(
                    index_id=index_id,
                    snapshot_date=snapshot_date,
                    status="ready" if error is None else "failed",
                    member_count=len(rows),
                    provider="baostock",
                    error=error,
                )
            )
            if error:
                self.last_errors.append(
                    f"index {index_id} {snapshot_date.isoformat()}: {error}"
                )
                continue
            for row in rows:
                instrument_id = _from_baostock_code(row.get("code"))
                if instrument_id not in selected:
                    continue
                memberships.append(
                    HistoricalIndexMembership(
                        index_id=index_id,
                        snapshot_date=snapshot_date,
                        instrument_id=instrument_id,
                        provider="baostock",
                    )
                )
        return snapshots, memberships

    def _rows(
        self,
        result,
        label: str,
        *,
        record_error: bool = True,
    ) -> list[dict[str, str]]:
        if getattr(result, "error_code", "1") != "0":
            if record_error:
                self.last_errors.append(
                    f"{label}: {getattr(result, 'error_msg', 'query failed')}"
                )
            return []
        fields = list(getattr(result, "fields", []) or [])
        rows: list[dict[str, str]] = []
        with _bounded_network_calls(self.request_timeout_seconds):
            while result.next():
                values = result.get_row_data()
                rows.append(dict(zip(fields, values, strict=False)))
        return rows

    def _call(self, fn, *args, **kwargs):
        with _bounded_network_calls(self.request_timeout_seconds):
            return fn(*args, **kwargs)


class BaoStockHistoricalFundamentalProvider(BaseStrategyDataProvider):
    name = "baostock_point_in_time"

    def __init__(self, client=bs, request_timeout_seconds: int = 6):
        super().__init__()
        self.client = client
        self.request_timeout_seconds = request_timeout_seconds

    def get_fundamentals(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        stocks = sorted(
            instrument_id
            for instrument_id in set(instrument_ids)
            if _is_stock_instrument(instrument_id)
        )
        self.last_errors = []
        if not stocks:
            return []

        snapshots: list[FundamentalSnapshot] = []
        with serialized_baostock_session():
            login = self._call(self.client.login)
            if getattr(login, "error_code", "1") != "0":
                self.last_errors.append(
                    f"baostock fundamentals login: "
                    f"{getattr(login, 'error_msg', 'login failed')}"
                )
                return []
            try:
                for instrument_id in stocks:
                    snapshots.extend(
                        self._load_instrument_fundamentals(
                            instrument_id,
                            start,
                            end,
                        )
                    )
            finally:
                self._call(self.client.logout)
        return sorted(snapshots, key=lambda item: (item.instrument_id, item.as_of_date))

    def _load_instrument_fundamentals(
        self,
        instrument_id: str,
        start: date,
        end: date,
    ) -> list[FundamentalSnapshot]:
        code = _to_baostock_code(instrument_id)
        profit_rows: list[dict[str, str]] = []
        growth_rows: list[dict[str, str]] = []
        for year in range(start.year - 1, end.year + 1):
            for quarter in range(1, 5):
                profit_rows.extend(
                    self._query_rows(
                        self.client.query_profit_data,
                        f"profit {instrument_id} {year}Q{quarter}",
                        code,
                        year=year,
                        quarter=quarter,
                    )
                )
                growth_rows.extend(
                    self._query_rows(
                        self.client.query_growth_data,
                        f"growth {instrument_id} {year}Q{quarter}",
                        code,
                        year=year,
                        quarter=quarter,
                    )
                )
        price_rows = self._query_rows(
            self.client.query_history_k_data_plus,
            f"valuation {instrument_id}",
            code,
            "date,close,peTTM,psTTM",
            start_date=(start - timedelta(days=14)).isoformat(),
            end_date=end.isoformat(),
            frequency="d",
            adjustflag="3",
        )
        return _fundamental_snapshots_from_rows(
            instrument_id,
            profit_rows,
            growth_rows,
            price_rows,
            start,
            end,
            self.name,
        )

    def _query_rows(self, fn, label: str, *args, **kwargs) -> list[dict[str, str]]:
        result = self._call(fn, *args, **kwargs)
        if getattr(result, "error_code", "1") != "0":
            self.last_errors.append(
                f"{label}: {getattr(result, 'error_msg', 'query failed')}"
            )
            return []
        return self._rows(result)

    def _rows(self, result) -> list[dict[str, str]]:
        fields = list(getattr(result, "fields", []) or [])
        rows: list[dict[str, str]] = []
        with _bounded_network_calls(self.request_timeout_seconds):
            while result.next():
                rows.append(
                    dict(zip(fields, result.get_row_data(), strict=False))
                )
        return rows

    def _call(self, fn, *args, **kwargs):
        with _bounded_network_calls(self.request_timeout_seconds):
            return fn(*args, **kwargs)


def build_historical_evidence_provider(mode: str) -> HistoricalEvidenceProvider | None:
    return BaoStockHistoricalEvidenceProvider() if mode.strip().lower() == "free" else None


def build_historical_fundamental_provider(mode: str) -> BaseStrategyDataProvider | None:
    return (
        BaoStockHistoricalFundamentalProvider()
        if mode.strip().lower() == "free"
        else None
    )


def historical_snapshot_dates(start: date, end: date) -> list[date]:
    sessions = trading_sessions_in_range(start, end)
    by_quarter: dict[tuple[int, int], date] = {}
    for session in sessions:
        by_quarter[(session.year, (session.month - 1) // 3 + 1)] = session
    return list(by_quarter.values())


def _to_baostock_code(instrument_id: str) -> str:
    symbol = instrument_id.split(":", 1)[-1].split(".", 1)[0]
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _from_baostock_code(value: object) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = re.fullmatch(r"(?:sh|sz|bj)\.(\d{6})", text.lower())
    return f"CN:{match.group(1)}" if match is not None else None


def _date(value: object) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: object) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bool_flag(value: object) -> bool | None:
    text = _text(value)
    if text == "1":
        return True
    if text == "0":
        return False
    return None


def _is_stock_instrument(instrument_id: str) -> bool:
    if not instrument_id.startswith("CN:") or instrument_id.endswith(".IDX"):
        return False
    symbol = instrument_id.split(":", 1)[-1]
    return not symbol.startswith(("15", "16", "51", "52", "56", "58"))


def _fundamental_snapshots_from_rows(
    instrument_id: str,
    profit_rows: list[dict[str, str]],
    growth_rows: list[dict[str, str]],
    price_rows: list[dict[str, str]],
    start: date,
    end: date,
    provider: str,
) -> list[FundamentalSnapshot]:
    profits = {
        stat_date: row
        for row in profit_rows
        if (stat_date := _date(row.get("statDate"))) is not None
    }
    growth = {
        stat_date: row
        for row in growth_rows
        if (stat_date := _date(row.get("statDate"))) is not None
    }
    prices = sorted(
        (
            trade_date,
            row,
        )
        for row in price_rows
        if (trade_date := _date(row.get("date"))) is not None
    )
    snapshots: list[FundamentalSnapshot] = []
    for stat_date, profit in sorted(profits.items()):
        growth_row = growth.get(stat_date, {})
        publication_dates = [
            value
            for value in [
                _date(profit.get("pubDate")),
                _date(growth_row.get("pubDate")),
            ]
            if value is not None
        ]
        if not publication_dates:
            continue
        as_of_date = max(publication_dates)
        if not start <= as_of_date <= end:
            continue
        prior = profits.get(_same_period_previous_year(stat_date), {})
        valuation = _latest_row_on_or_before(prices, as_of_date)
        total_shares = _decimal(profit.get("totalShare"))
        close = _decimal(valuation.get("close"))
        earnings_growth = _percent(growth_row.get("YOYNI"))
        if earnings_growth is None:
            earnings_growth = _growth_pct(
                _decimal(profit.get("netProfit")),
                _decimal(prior.get("netProfit")),
            )
        snapshots.append(
            FundamentalSnapshot(
                instrument_id=instrument_id,
                as_of_date=as_of_date,
                revenue_growth_pct=_growth_pct(
                    _decimal(profit.get("MBRevenue")),
                    _decimal(prior.get("MBRevenue")),
                ),
                earnings_growth_pct=earnings_growth,
                gross_margin_pct=_percent(profit.get("gpMargin")),
                net_margin_pct=_percent(profit.get("npMargin")),
                return_on_equity_pct=_percent(profit.get("roeAvg")),
                market_cap=(
                    close * total_shares
                    if close is not None and total_shares is not None
                    else None
                ),
                pe_ratio=_decimal(valuation.get("peTTM")),
                price_to_sales=_decimal(valuation.get("psTTM")),
                provider=provider,
            )
        )
    return snapshots


def _same_period_previous_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _latest_row_on_or_before(
    rows: list[tuple[date, dict[str, str]]],
    cutoff: date,
) -> dict[str, str]:
    eligible = [row for trade_date, row in rows if trade_date <= cutoff]
    return eligible[-1] if eligible else {}


def _decimal(value: object) -> Decimal | None:
    text = _text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _percent(value: object) -> Decimal | None:
    number = _decimal(value)
    return number * Decimal("100") if number is not None else None


def _growth_pct(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None or prior == 0:
        return None
    return ((current / abs(prior)) - Decimal("1")) * Decimal("100")
