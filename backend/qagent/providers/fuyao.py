from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from http.client import RemoteDisconnected
import math
import time
from threading import Lock
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from qagent.providers.base import MINUTE_BAR_COLUMNS
from qagent.providers.free_cn import BAR_COLUMNS


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FUYAO_SNAPSHOT_COLUMNS = [
    "instrument_id",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "previous_close",
    "price_change",
    "price_change_ratio_pct",
    "volume",
    "turnover",
    "provider",
    "thscode",
    "ticker",
]
RETRIABLE_BUSINESS_CODES = {4001, 5002, 5003}
FUYAO_NEGATIVE_CAPABILITY_TTL_SECONDS = 60 * 60
FINANCIAL_STATEMENT_PATHS = {
    "income": "/api/a-share/financials/income-statements",
    "balance": "/api/a-share/financials/balance-sheets",
    "cash_flow": "/api/a-share/financials/cash-flow-statements",
}
FUYAO_CAPABILITY_GROUPS: list[dict[str, object]] = [
    {
        "id": "market_data",
        "name": "A股行情与交易日历",
        "capabilities": [
            "realtime_snapshot",
            "daily_ohlcv_raw",
            "daily_ohlcv_forward_adjusted",
            "trading_calendar",
            "corporate_actions",
            "ticker_search",
            "ticker_catalog",
        ],
        "qagent_role": "live_snapshot_and_tertiary_daily_fallback",
    },
    {
        "id": "fundamentals",
        "name": "财务与估值研究",
        "capabilities": [
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
            "financial_indicators",
            "valuation_snapshot",
        ],
        "qagent_role": "research_enrichment_only",
    },
    {
        "id": "index",
        "name": "指数与板块",
        "capabilities": [
            "index_catalog",
            "index_constituents",
            "index_snapshot",
            "index_daily_ohlcv",
        ],
        "qagent_role": "research_and_market_data_fallback",
    },
    {
        "id": "fund",
        "name": "ETF与基金",
        "capabilities": [
            "fund_profile",
            "fund_holdings",
            "fund_holders",
            "fund_nav",
            "fund_returns",
            "etf_snapshot",
            "etf_daily_ohlcv",
        ],
        "qagent_role": "etf_research_and_market_data_fallback",
    },
    {
        "id": "special_data",
        "name": "市场情绪与异动",
        "capabilities": [
            "limit_up_pool",
            "limit_up_ladder",
            "hot_stock_list",
            "skyrocket_list",
            "hot_stock_history",
            "hot_stock_rank_trend",
            "anomaly_analysis",
            "dragon_tiger",
        ],
        "qagent_role": "research_enrichment_only",
    },
]


class FuyaoProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | str,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True)
class _FuyaoNegativeCapability:
    code: int
    message: str
    request_id: str | None
    expires_at: float


_FUYAO_NEGATIVE_CAPABILITY_CACHE: dict[tuple[str, str, str], _FuyaoNegativeCapability] = {}
# Shared by clients in one process so independently constructed API/research clients can reuse it.
# Spawned full-market workers retain their own isolated copy for that worker's lifetime.
_FUYAO_NEGATIVE_CAPABILITY_LOCK = Lock()


@dataclass(frozen=True)
class FuyaoRequestMetadata:
    request_id: str | None
    timestamp_ms: int | None
    timestamp: str | None
    path: str


@dataclass(frozen=True)
class FuyaoTelemetrySnapshot:
    requests: int
    attempts: int
    successes: int
    errors: int
    retries: int
    latency_ms_total: float
    latency_ms_last: float | None
    last_path: str | None
    last_request_id: str | None
    last_data_timestamp: str | None
    last_error_code: str | None
    last_completed_at: str | None
    error_categories: dict[str, int]
    negative_capability_skips: int
    negative_capability_expired: int
    negative_capability_reprobes: int
    negative_capability_success_clears: int
    degraded_snapshot_rows: int
    degraded_snapshot_fields: dict[str, int]


class FuyaoClient:
    """Validated read-only client for the currently published Fuyao REST API."""

    name = "fuyao"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://fuyao.aicubes.cn",
        request_timeout_seconds: int = 8,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.25,
        negative_capability_ttl_seconds: float = FUYAO_NEGATIVE_CAPABILITY_TTL_SECONDS,
        session: requests.Session | None = None,
    ):
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("Fuyao API key is required")
        self.api_key = normalized_key
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = max(1, request_timeout_seconds)
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.negative_capability_ttl_seconds = max(0.0, negative_capability_ttl_seconds)
        self.session = session or requests.Session()
        self.last_errors: list[str] = []
        self.last_request: FuyaoRequestMetadata | None = None
        self._telemetry_lock = Lock()
        self.reset_telemetry()

    def reset_telemetry(self) -> None:
        with self._telemetry_lock:
            self._telemetry_requests = 0
            self._telemetry_attempts = 0
            self._telemetry_successes = 0
            self._telemetry_errors = 0
            self._telemetry_retries = 0
            self._telemetry_latency_ms_total = 0.0
            self._telemetry_latency_ms_last: float | None = None
            self._telemetry_last_path: str | None = None
            self._telemetry_last_request_id: str | None = None
            self._telemetry_last_data_timestamp: str | None = None
            self._telemetry_last_error_code: str | None = None
            self._telemetry_last_completed_at: str | None = None
            self._telemetry_error_categories: Counter[str] = Counter()
            self._telemetry_negative_capability_skips = 0
            self._telemetry_negative_capability_expired = 0
            self._telemetry_negative_capability_reprobes = 0
            self._telemetry_negative_capability_success_clears = 0
            self._telemetry_degraded_snapshot_rows = 0
            self._telemetry_degraded_snapshot_fields: Counter[str] = Counter()

    def telemetry_snapshot(self) -> FuyaoTelemetrySnapshot:
        with self._telemetry_lock:
            return FuyaoTelemetrySnapshot(
                requests=self._telemetry_requests,
                attempts=self._telemetry_attempts,
                successes=self._telemetry_successes,
                errors=self._telemetry_errors,
                retries=self._telemetry_retries,
                latency_ms_total=round(self._telemetry_latency_ms_total, 3),
                latency_ms_last=(
                    round(self._telemetry_latency_ms_last, 3)
                    if self._telemetry_latency_ms_last is not None
                    else None
                ),
                last_path=self._telemetry_last_path,
                last_request_id=self._telemetry_last_request_id,
                last_data_timestamp=self._telemetry_last_data_timestamp,
                last_error_code=self._telemetry_last_error_code,
                last_completed_at=self._telemetry_last_completed_at,
                error_categories=dict(self._telemetry_error_categories),
                negative_capability_skips=self._telemetry_negative_capability_skips,
                negative_capability_expired=self._telemetry_negative_capability_expired,
                negative_capability_reprobes=self._telemetry_negative_capability_reprobes,
                negative_capability_success_clears=(
                    self._telemetry_negative_capability_success_clears
                ),
                degraded_snapshot_rows=self._telemetry_degraded_snapshot_rows,
                degraded_snapshot_fields=dict(self._telemetry_degraded_snapshot_fields),
            )

    def request_data(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        payload = self._request_payload(path, params=params)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FuyaoProviderError(
                f"Fuyao returned an invalid data object for {path}",
                code="invalid_response",
                request_id=_request_id(payload),
            )
        self.last_request = _request_metadata(payload, path)
        return data

    def search_tickers(
        self,
        query: str,
        *,
        exchange: str | None = None,
        asset_type: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("ticker search query must not be empty")
        if limit <= 0 or limit > 50:
            raise ValueError("ticker search limit must be between 1 and 50")
        return self.request_data(
            "/api/meta/tickers/search",
            params={
                "q": normalized_query,
                "exchange": exchange,
                "asset_type": asset_type,
                "limit": limit,
            },
        )

    def list_tickers(
        self,
        *,
        asset_type: str | None = None,
        limit: int = 1_000,
        offset: int = 0,
    ) -> dict[str, Any]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("ticker list limit must be between 1 and 10000")
        if offset < 0:
            raise ValueError("ticker list offset must not be negative")
        return self.request_data(
            "/api/meta/tickers/list",
            params={"asset_type": asset_type, "limit": limit, "offset": offset},
        )

    def get_stock_snapshot_data(self, thscodes: list[str]) -> dict[str, Any]:
        _validate_batch_size(thscodes, maximum=50, label="stock snapshot")
        return self.request_data(
            "/api/a-share/prices/snapshot",
            params={"thscodes": _joined_thscodes(thscodes)},
        )

    def get_stock_market_page(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        if limit <= 0 or limit > 1_000:
            raise ValueError("stock market page limit must be between 1 and 1000")
        if offset < 0:
            raise ValueError("stock market page offset must not be negative")
        return self.request_data(
            "/api/a-share/prices/snapshot",
            params={"limit": limit, "offset": offset},
        )

    def get_stock_history_data(
        self,
        thscode: str,
        start: date,
        end: date,
        *,
        adjust: Literal["none", "forward", "backward"] = "forward",
    ) -> dict[str, Any]:
        return self.request_data(
            "/api/a-share/prices/historical",
            params=_history_params(thscode, start, end, adjust=adjust),
        )

    def get_trading_days(self) -> dict[str, Any]:
        return self.request_data("/api/a-share/calendar/trading-days")

    def get_corporate_actions(
        self,
        thscode: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        return self.request_data(
            "/api/a-share/corporate-actions/adjustment-factors",
            params={
                "thscode": _normalized_thscode(thscode),
                "from": start.isoformat() if start else None,
                "to": end.isoformat() if end else None,
            },
        )

    def get_financial_statements(
        self,
        thscode: str,
        statement: Literal["income", "balance", "cash_flow"],
        *,
        period: Literal["annual", "quarterly"] = "quarterly",
        start: date | None = None,
        end: date | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        path = FINANCIAL_STATEMENT_PATHS.get(statement)
        if path is None:
            raise ValueError(f"unsupported financial statement: {statement}")
        if period not in {"annual", "quarterly"}:
            raise ValueError("financial statement period must be annual or quarterly")
        if limit <= 0 or limit > 100:
            raise ValueError("financial statement limit must be between 1 and 100")
        return self.request_data(
            path,
            params={
                "thscode": _normalized_thscode(thscode),
                "period": period,
                "start": _date_to_epoch_ms(start) if start else None,
                "end": _date_to_epoch_ms(end, end_of_day=True) if end else None,
                "limit": limit,
            },
        )

    def get_financial_indicators(self, thscode: str, report: str) -> dict[str, Any]:
        if not _valid_report(report):
            raise ValueError("financial report must match yyyy-1 through yyyy-4")
        return self.request_data(
            "/api/a-share/financials/indicators",
            params={"thscode": _normalized_thscode(thscode), "report": report},
        )

    def get_latest_financial_indicators(
        self,
        thscode: str,
        *,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for report in financial_report_candidates(as_of or date.today()):
            try:
                data = self.get_financial_indicators(thscode, report)
            except FuyaoProviderError as exc:
                errors.append(f"{report}: code={exc.code}")
                continue
            if _financial_indicator_values(data):
                return data
        raise FuyaoProviderError(
            "Fuyao returned no usable financial indicators for recent reports"
            + (f" ({'; '.join(errors[:3])})" if errors else ""),
            code="no_recent_financial_indicators",
            request_id=self.last_request.request_id if self.last_request else None,
        )

    def get_valuations(self, thscodes: list[str]) -> dict[str, Any]:
        normalized = list(dict.fromkeys(_normalized_thscode(value) for value in thscodes))
        if len(normalized) > 100:
            raise ValueError("valuation snapshot accepts at most 100 symbols")
        return self.request_data(
            "/api/a-share/valuations/snapshot",
            params={"thscodes": _joined_thscodes(normalized)},
        )

    def get_index_catalog(
        self,
        tag: Literal["cn_concept", "region", "tszs", "industry"],
    ) -> dict[str, Any]:
        if tag not in {"cn_concept", "region", "tszs", "industry"}:
            raise ValueError(f"unsupported index catalog tag: {tag}")
        return self.request_data(
            "/api/a-share-index/catalog/ths-index-list",
            params={"tag": tag},
        )

    def get_index_constituents(self, thscode: str) -> dict[str, Any]:
        return self.request_data(
            "/api/a-share-index/constituents/ths-stock-list",
            params={"thscode": _normalized_thscode(thscode, allow_ti=True)},
        )

    def get_index_snapshot_data(self, thscodes: list[str]) -> dict[str, Any]:
        _validate_batch_size(thscodes, maximum=50, label="index snapshot")
        return self.request_data(
            "/api/a-share-index/prices/snapshot",
            params={"thscodes": _joined_thscodes(thscodes, allow_ti=True)},
        )

    def get_index_history_data(
        self,
        thscode: str,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        return self.request_data(
            "/api/a-share-index/prices/historical",
            params=_history_params(thscode, start, end, allow_ti=True),
        )

    def get_limit_up_pool(
        self,
        *,
        trade_date: date | None = None,
        page: int = 1,
        size: int = 200,
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("limit-up page must be at least 1")
        if size < 1 or size > 200:
            raise ValueError("limit-up size must be between 1 and 200")
        return self.request_data(
            "/api/a-share/special-data/limit-up-pool",
            params={
                "date_ms": _date_to_epoch_ms(trade_date) if trade_date else None,
                "page": page,
                "size": size,
            },
        )

    def get_limit_up_ladder(self) -> dict[str, Any]:
        return self.request_data("/api/a-share/special-data/limit-up-ladder")

    def get_hot_stock_list(self, *, period: Literal["day", "hour"] = "day") -> dict[str, Any]:
        return self._get_ranked_list("hot-stock-list", period)

    def get_skyrocket_list(self, *, period: Literal["day", "hour"] = "day") -> dict[str, Any]:
        return self._get_ranked_list("skyrocket-list", period)

    def get_hot_stock_history(self, trade_date: date) -> dict[str, Any]:
        return self.request_data(
            "/api/a-share/special-data/hot-stock-list-history",
            params={"date": trade_date.isoformat()},
        )

    def get_hot_stock_rank_trend(
        self,
        thscode: str,
        *,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        if end < start:
            raise ValueError("hot-stock trend end date must be on or after start date")
        return self.request_data(
            "/api/a-share/special-data/hot-stock-rank-trend",
            params={
                "thscode": _normalized_thscode(thscode),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )

    def get_anomaly_analysis(
        self,
        *,
        thscodes: list[str] | None = None,
        tag_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        if thscodes:
            if len(thscodes) > 50:
                raise ValueError("anomaly stock query accepts at most 50 symbols")
            return self.request_data(
                "/api/a-share/special-data/anomaly-analysis-stock",
                params={"thscodes": _joined_thscodes(thscodes)},
            )
        return self.request_data(
            "/api/a-share/special-data/anomaly-analysis-list",
            params={"tag_codes": ",".join(tag_codes or []) or None},
        )

    def get_dragon_tiger(
        self,
        *,
        board_type: Literal["all", "org", "hot_money"] = "all",
        trade_date: date | None = None,
    ) -> dict[str, Any]:
        if board_type not in {"all", "org", "hot_money"}:
            raise ValueError(f"unsupported dragon tiger board type: {board_type}")
        return self.request_data(
            "/api/a-share/special-data/dragon-tiger-list",
            params={
                "board_type": board_type,
                "date": trade_date.isoformat() if trade_date else None,
            },
        )

    def get_fund_profile(self, thscode: str, *, fund_type: str = "exchange") -> dict[str, Any]:
        return self._get_fund_data("/api/fund/profile/detail", thscode, fund_type=fund_type)

    def get_fund_holdings(self, thscode: str, *, fund_type: str = "exchange") -> dict[str, Any]:
        return self._get_fund_data("/api/fund/portfolio/holdings", thscode, fund_type=fund_type)

    def get_fund_holders(
        self,
        thscode: str,
        *,
        fund_type: str = "exchange",
        merge_scope: str = "all",
    ) -> dict[str, Any]:
        data = self._get_fund_data(
            "/api/fund/holders/detail",
            thscode,
            fund_type=fund_type,
            extra={"merge_scope": merge_scope},
        )
        return data

    def get_fund_snapshot_data(self, thscode: str) -> dict[str, Any]:
        return self.request_data(
            "/api/fund/market/snapshot",
            params={"thscode": _normalized_thscode(thscode)},
        )

    def get_fund_history_data(
        self,
        thscode: str,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        return self.request_data(
            "/api/fund/market/historical",
            params=_history_params(thscode, start, end),
        )

    def get_fund_nav(
        self,
        thscode: str,
        *,
        fund_type: str = "exchange",
        range_name: str = "year",
        nav_type: str = "unit",
    ) -> dict[str, Any]:
        return self._get_fund_data(
            "/api/fund/performance/nav",
            thscode,
            fund_type=fund_type,
            extra={"range": range_name, "nav_type": nav_type},
        )

    def get_fund_returns(self, thscode: str, *, fund_type: str = "exchange") -> dict[str, Any]:
        return self._get_fund_data(
            "/api/fund/performance/returns",
            thscode,
            fund_type=fund_type,
        )

    def _get_ranked_list(self, endpoint: str, period: str) -> dict[str, Any]:
        if period not in {"day", "hour"}:
            raise ValueError("ranked-list period must be day or hour")
        return self.request_data(
            f"/api/a-share/special-data/{endpoint}",
            params={"period": period},
        )

    def _get_fund_data(
        self,
        path: str,
        thscode: str,
        *,
        fund_type: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if fund_type not in {"otc", "exchange", "reits"}:
            raise ValueError(f"unsupported fund type: {fund_type}")
        return self.request_data(
            path,
            params={
                "fund_type": fund_type,
                "thscode": _normalized_thscode(thscode, allow_of=True),
                **(extra or {}),
            },
        )

    def _request_payload(
        self,
        path: str,
        *,
        params: dict[str, object] | None,
    ) -> dict[str, Any]:
        endpoint = f"{self.base_url}{path}"
        request_params = {key: value for key, value in (params or {}).items() if value is not None}
        capability_key = _negative_capability_key(self.base_url, path, request_params)
        self._raise_if_negative_capability_cached(capability_key, path)
        last_error: FuyaoProviderError | None = None
        started_at = time.perf_counter()
        self._telemetry_begin(path)
        for attempt in range(1, self.max_attempts + 1):
            self._telemetry_attempt()
            try:
                response = self.session.get(
                    endpoint,
                    headers={"X-api-key": self.api_key},
                    params=request_params,
                    timeout=self.request_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, RemoteDisconnected, ValueError) as exc:
                error_category = (
                    "timeout" if isinstance(exc, requests.Timeout) else "transport_error"
                )
                last_error = FuyaoProviderError(
                    f"Fuyao request failed at the transport layer for {path}",
                    code="transport_error",
                )
                if attempt < self.max_attempts:
                    self._telemetry_retry()
                    self._sleep_before_retry(attempt)
                    continue
                self._telemetry_finish(
                    started_at,
                    success=False,
                    error_code=last_error.code,
                    error_category=error_category,
                )
                raise last_error from exc

            if not isinstance(payload, dict):
                last_error = FuyaoProviderError(
                    f"Fuyao returned a non-object response for {path}",
                    code="invalid_response",
                )
                self._telemetry_finish(
                    started_at,
                    success=False,
                    error_code=last_error.code,
                    error_category="incomplete_response",
                )
                raise last_error
            code = payload.get("code")
            if code == 0:
                if capability_key is not None:
                    with _FUYAO_NEGATIVE_CAPABILITY_LOCK:
                        cleared = _FUYAO_NEGATIVE_CAPABILITY_CACHE.pop(capability_key, None)
                    if cleared is not None:
                        self._telemetry_negative_capability(success_clear=True)
                metadata = _request_metadata(payload, path)
                self._telemetry_finish(
                    started_at,
                    success=True,
                    request_id=metadata.request_id,
                    data_timestamp=metadata.timestamp,
                )
                return payload

            request_id = _request_id(payload)
            message = _safe_message(payload.get("message"), self.api_key)
            last_error = FuyaoProviderError(
                f"Fuyao API rejected {path}: code={code}, message={message}",
                code=code if isinstance(code, int) else "invalid_response",
                request_id=request_id,
            )
            if (
                capability_key is not None
                and self.negative_capability_ttl_seconds > 0
                and _negative_capability_reason(code, message)
            ):
                with _FUYAO_NEGATIVE_CAPABILITY_LOCK:
                    now = time.monotonic()
                    expired_keys = [
                        key
                        for key, value in _FUYAO_NEGATIVE_CAPABILITY_CACHE.items()
                        if value.expires_at <= now and key != capability_key
                    ]
                    for key in expired_keys:
                        _FUYAO_NEGATIVE_CAPABILITY_CACHE.pop(key, None)
                    _FUYAO_NEGATIVE_CAPABILITY_CACHE[capability_key] = _FuyaoNegativeCapability(
                        code=code,
                        message=message,
                        request_id=request_id,
                        expires_at=(now + self.negative_capability_ttl_seconds),
                    )
            if code in RETRIABLE_BUSINESS_CODES and attempt < self.max_attempts:
                self._telemetry_retry()
                self._sleep_before_retry(attempt)
                continue
            self._telemetry_finish(
                started_at,
                success=False,
                request_id=request_id,
                error_code=last_error.code,
                error_category=_fuyao_error_category(last_error.code, message),
            )
            raise last_error

        assert last_error is not None
        self._telemetry_finish(
            started_at,
            success=False,
            request_id=last_error.request_id,
            error_code=last_error.code,
            error_category=_fuyao_error_category(last_error.code, str(last_error)),
        )
        raise last_error

    def _raise_if_negative_capability_cached(
        self,
        capability_key: tuple[str, str, str] | None,
        path: str,
    ) -> None:
        if capability_key is None or self.negative_capability_ttl_seconds <= 0:
            return
        with _FUYAO_NEGATIVE_CAPABILITY_LOCK:
            cached = _FUYAO_NEGATIVE_CAPABILITY_CACHE.get(capability_key)
        if cached is None:
            return
        if cached.expires_at <= time.monotonic():
            self._telemetry_negative_capability(expired=True, reprobe=True)
            return
        self._telemetry_negative_capability(skip=True)
        raise FuyaoProviderError(
            f"Fuyao soft negative capability cache skipped {path}: {cached.message}",
            code=cached.code,
            request_id=cached.request_id,
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds:
            time.sleep(self.retry_backoff_seconds * attempt)

    def _telemetry_begin(self, path: str) -> None:
        with self._telemetry_lock:
            self._telemetry_requests += 1
            self._telemetry_last_path = path

    def _telemetry_attempt(self) -> None:
        with self._telemetry_lock:
            self._telemetry_attempts += 1

    def _telemetry_retry(self) -> None:
        with self._telemetry_lock:
            self._telemetry_retries += 1

    def _telemetry_negative_capability(
        self,
        *,
        skip: bool = False,
        expired: bool = False,
        reprobe: bool = False,
        success_clear: bool = False,
    ) -> None:
        with self._telemetry_lock:
            self._telemetry_negative_capability_skips += int(skip)
            self._telemetry_negative_capability_expired += int(expired)
            self._telemetry_negative_capability_reprobes += int(reprobe)
            self._telemetry_negative_capability_success_clears += int(success_clear)

    def _telemetry_snapshot_degradation(self, fields: tuple[str, ...]) -> None:
        if not fields:
            return
        with self._telemetry_lock:
            self._telemetry_degraded_snapshot_rows += 1
            self._telemetry_degraded_snapshot_fields.update(fields)

    def _record_snapshot_degradations(
        self,
        degradations: list[tuple[str, tuple[str, ...]]],
    ) -> None:
        for _instrument_id, fields in degradations:
            self._telemetry_snapshot_degradation(fields)

    def _telemetry_finish(
        self,
        started_at: float,
        *,
        success: bool,
        request_id: str | None = None,
        data_timestamp: str | None = None,
        error_code: int | str | None = None,
        error_category: str | None = None,
    ) -> None:
        elapsed_ms = max((time.perf_counter() - started_at) * 1_000, 0.0)
        with self._telemetry_lock:
            if success:
                self._telemetry_successes += 1
                self._telemetry_last_error_code = None
            else:
                self._telemetry_errors += 1
                self._telemetry_error_categories[error_category or "unknown"] += 1
                self._telemetry_last_error_code = (
                    str(error_code) if error_code is not None else "unknown"
                )
            self._telemetry_latency_ms_total += elapsed_ms
            self._telemetry_latency_ms_last = elapsed_ms
            self._telemetry_last_request_id = request_id
            self._telemetry_last_data_timestamp = data_timestamp
            self._telemetry_last_completed_at = datetime.now(timezone.utc).isoformat()


class FuyaoSnapshotProvider(FuyaoClient):
    """Strict stock quote probe retained for diagnostics and configuration checks."""

    name = "fuyao_snapshot"

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        self.last_errors = []
        self.last_request = None
        request_map = _request_map(instrument_ids)
        if not request_map:
            return pd.DataFrame(columns=FUYAO_SNAPSHOT_COLUMNS)

        payload = self._request_snapshot(list(request_map))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FuyaoProviderError(
                "Fuyao returned an invalid snapshot envelope",
                code="invalid_response",
                request_id=_request_id(payload),
            )
        timestamp_ms = data.get("timestamp")
        items = data.get("item")
        if not isinstance(timestamp_ms, int) or not isinstance(items, list):
            raise FuyaoProviderError(
                "Fuyao returned invalid snapshot metadata",
                code="invalid_response",
                request_id=_request_id(payload),
            )

        timestamp = _timestamp_iso(timestamp_ms)
        self.last_request = FuyaoRequestMetadata(
            request_id=_request_id(payload),
            timestamp_ms=timestamp_ms,
            timestamp=timestamp,
            path="/api/a-share/prices/snapshot",
        )
        degradations: list[tuple[str, tuple[str, ...]]] = []
        rows = _strict_snapshot_rows(
            items,
            request_map=request_map,
            timestamp=timestamp,
            provider_name=self.name,
            request_id=self.last_request.request_id,
            degradation_sink=degradations,
        )
        self._record_snapshot_degradations(degradations)
        return pd.DataFrame(rows, columns=FUYAO_SNAPSHOT_COLUMNS)

    def _request_snapshot(self, thscodes: list[str]) -> dict[str, Any]:
        return self._request_payload(
            "/api/a-share/prices/snapshot",
            params={"thscodes": ",".join(thscodes)},
        )


class FuyaoMarketDataProvider(FuyaoClient):
    """Qagent market-data adapter for Fuyao stock, index and ETF endpoints."""

    name = "fuyao_market"

    def get_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        self.last_errors = []
        frames: list[pd.DataFrame] = []
        for instrument_id in dict.fromkeys(instrument_ids):
            try:
                frame = self._load_daily_bars(instrument_id, start, end)
            except Exception as exc:
                self.last_errors.append(f"{instrument_id}: fuyao: {exc}")
                continue
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["instrument_id", "trade_date"], keep="last")
            .sort_values(["instrument_id", "trade_date"])
            .reset_index(drop=True)[BAR_COLUMNS]
        )

    def get_historical_daily_bars(
        self,
        instrument_ids: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        return self.get_daily_bars(instrument_ids, start, end)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        self.last_errors = []
        rows_by_instrument: dict[str, dict[str, Any]] = {}
        requested = list(dict.fromkeys(value.strip().upper() for value in instrument_ids))
        grouped: dict[str, list[str]] = {"stock": [], "index": [], "etf": []}
        for instrument_id in requested:
            if _is_index_instrument(instrument_id):
                grouped["index"].append(instrument_id)
            elif _is_etf_instrument(instrument_id):
                grouped["etf"].append(instrument_id)
            else:
                grouped["stock"].append(instrument_id)

        for batch in _batches(grouped["stock"], 50):
            self._load_snapshot_group(batch, self.get_stock_snapshot_data, rows_by_instrument)
        for batch in _batches(grouped["index"], 50):
            self._load_snapshot_group(batch, self.get_index_snapshot_data, rows_by_instrument)
        for instrument_id in grouped["etf"]:
            self._load_snapshot_group(
                [instrument_id],
                lambda thscodes: self.get_fund_snapshot_data(thscodes[0]),
                rows_by_instrument,
            )

        rows = [rows_by_instrument[item] for item in requested if item in rows_by_instrument]
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.DataFrame(rows, columns=BAR_COLUMNS)

    def get_minute_bars(
        self,
        instrument_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        del instrument_ids, start, end
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)

    def _load_daily_bars(self, instrument_id: str, start: date, end: date) -> pd.DataFrame:
        if end < start:
            raise ValueError("end date must be on or after start date")
        thscode = to_fuyao_thscode(instrument_id)
        frames: list[pd.DataFrame] = []
        for chunk_start, chunk_end in _date_chunks(start, end):
            if _is_index_instrument(instrument_id):
                raw = _normalize_history_data(
                    self.get_index_history_data(thscode, chunk_start, chunk_end),
                    chunk_start,
                    chunk_end,
                )
                normalized = _single_price_history(raw, adjustment_type="none")
                provider_name = "fuyao_index"
            elif _is_etf_instrument(instrument_id):
                raw = _normalize_history_data(
                    self.get_fund_history_data(thscode, chunk_start, chunk_end),
                    chunk_start,
                    chunk_end,
                )
                normalized = _single_price_history(raw, adjustment_type="none")
                provider_name = "fuyao_etf_unadjusted"
            else:
                raw = _normalize_history_data(
                    self.get_stock_history_data(
                        thscode,
                        chunk_start,
                        chunk_end,
                        adjust="none",
                    ),
                    chunk_start,
                    chunk_end,
                )
                adjusted = _normalize_history_data(
                    self.get_stock_history_data(
                        thscode,
                        chunk_start,
                        chunk_end,
                        adjust="forward",
                    ),
                    chunk_start,
                    chunk_end,
                )
                normalized = _paired_price_history(raw, adjusted)
                provider_name = "fuyao_stock_paired"
            if normalized.empty:
                continue
            normalized["instrument_id"] = instrument_id
            normalized["provider"] = provider_name
            frames.append(normalized[BAR_COLUMNS])
        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def _load_snapshot_group(
        self,
        instrument_ids: list[str],
        loader,
        rows_by_instrument: dict[str, dict[str, Any]],
    ) -> None:
        if not instrument_ids:
            return
        request_map = _request_map(instrument_ids)
        try:
            data = loader(list(request_map))
            timestamp_ms = data.get("timestamp")
            items = data.get("item")
            if not isinstance(timestamp_ms, int) or not isinstance(items, list):
                raise FuyaoProviderError(
                    "Fuyao returned invalid snapshot metadata",
                    code="invalid_response",
                    request_id=self.last_request.request_id if self.last_request else None,
                )
            timestamp = _timestamp_iso(timestamp_ms)
            degradations: list[tuple[str, tuple[str, ...]]] = []
            rows = _strict_snapshot_rows(
                items,
                request_map=request_map,
                timestamp=timestamp,
                provider_name="fuyao_realtime",
                request_id=self.last_request.request_id if self.last_request else None,
                degradation_sink=degradations,
            )
        except Exception as exc:
            if len(instrument_ids) > 1:
                midpoint = len(instrument_ids) // 2
                self._load_snapshot_group(
                    instrument_ids[:midpoint],
                    loader,
                    rows_by_instrument,
                )
                self._load_snapshot_group(
                    instrument_ids[midpoint:],
                    loader,
                    rows_by_instrument,
                )
                return
            self.last_errors.extend(f"{item}: fuyao snapshot: {exc}" for item in instrument_ids)
            return
        self._record_snapshot_degradations(degradations)
        trade_date = datetime.fromtimestamp(timestamp_ms / 1000, tz=SHANGHAI_TZ).date()
        for row in rows:
            instrument_id = str(row["instrument_id"])
            rows_by_instrument[instrument_id] = {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "turnover": row["turnover"],
                "provider": row["provider"],
                "adjusted_open": None,
                "adjusted_high": None,
                "adjusted_low": None,
                "adjusted_close": None,
                "adjustment_factor": None,
                "adjustment_type": None,
            }


def reset_fuyao_telemetry(provider: object) -> int:
    clients = _fuyao_clients(provider)
    for client in clients:
        client.reset_telemetry()
    return len(clients)


def fuyao_telemetry_data_health(provider: object) -> dict[str, str]:
    snapshots = [client.telemetry_snapshot() for client in _fuyao_clients(provider)]
    if not snapshots:
        return {}

    requests = sum(item.requests for item in snapshots)
    attempts = sum(item.attempts for item in snapshots)
    successes = sum(item.successes for item in snapshots)
    errors = sum(item.errors for item in snapshots)
    retries = sum(item.retries for item in snapshots)
    negative_capability_skips = sum(item.negative_capability_skips for item in snapshots)
    negative_capability_expired = sum(item.negative_capability_expired for item in snapshots)
    negative_capability_reprobes = sum(item.negative_capability_reprobes for item in snapshots)
    negative_capability_success_clears = sum(
        item.negative_capability_success_clears for item in snapshots
    )
    degraded_snapshot_rows = sum(item.degraded_snapshot_rows for item in snapshots)
    degraded_snapshot_fields: Counter[str] = Counter()
    latency_total = sum(item.latency_ms_total for item in snapshots)
    error_categories: Counter[str] = Counter()
    for item in snapshots:
        error_categories.update(item.error_categories)
        degraded_snapshot_fields.update(item.degraded_snapshot_fields)
    latest = max(
        snapshots,
        key=lambda item: item.last_completed_at or "",
    )
    state = (
        "idle"
        if requests == 0
        else "error"
        if errors and not successes
        else "partial"
        if errors
        else "ready"
    )
    health = {
        "fuyao_telemetry": state,
        "fuyao_clients": str(len(snapshots)),
        "fuyao_requests": str(requests),
        "fuyao_attempts": str(attempts),
        "fuyao_successes": str(successes),
        "fuyao_errors": str(errors),
        "fuyao_error_category_mix": _count_mix(error_categories),
        "fuyao_retries": str(retries),
        "fuyao_negative_capability_skips": str(negative_capability_skips),
        "fuyao_negative_capability_expired": str(negative_capability_expired),
        "fuyao_negative_capability_reprobes": str(negative_capability_reprobes),
        "fuyao_negative_capability_success_clears": str(negative_capability_success_clears),
        "fuyao_degraded_snapshot_rows": str(degraded_snapshot_rows),
        "fuyao_degraded_snapshot_field_mix": _count_mix(degraded_snapshot_fields),
        "fuyao_latency_ms_total": f"{latency_total:.3f}",
        "fuyao_latency_ms_average": (f"{latency_total / requests:.3f}" if requests else "0.000"),
    }
    optional = {
        "fuyao_latency_ms_last": latest.latency_ms_last,
        "fuyao_last_path": latest.last_path,
        "fuyao_last_request_id": latest.last_request_id,
        "fuyao_last_data_timestamp": latest.last_data_timestamp,
        "fuyao_last_error_code": latest.last_error_code,
        "fuyao_last_completed_at": latest.last_completed_at,
    }
    health.update(
        {
            key: f"{value:.3f}" if isinstance(value, float) else str(value)
            for key, value in optional.items()
            if value is not None
        }
    )
    return health


def _fuyao_error_category(code: int | str | None, message: str | None) -> str:
    normalized = str(message or "").strip().lower()
    normalized_code = str(code or "").strip().lower()
    if normalized_code == "transport_error":
        return "transport_error"
    if normalized_code == "invalid_response":
        return "incomplete_response"
    if normalized_code in {"4001", "429"} or any(
        value in normalized for value in ("rate limit", "too many requests", "限流", "频率")
    ):
        return "rate_limited"
    if any(value in normalized for value in ("timeout", "timed out", "超时")):
        return "timeout"
    if any(value in normalized for value in ("not found", "不存在", "无此", "无数据")):
        return "symbol_not_found"
    if any(value in normalized for value in ("unsupported", "not support", "不支持")):
        return "unsupported_asset"
    if any(value in normalized for value in ("parameter", "argument", "参数")):
        return "invalid_parameter"
    if normalized_code in {"2001", "401", "403"} or any(
        value in normalized for value in ("unauthorized", "forbidden", "api key", "鉴权")
    ):
        return "authentication"
    return f"business_{normalized_code}" if normalized_code else "business_error"


def _negative_capability_reason(code: object, message: str) -> str | None:
    """Return a cacheable reason only for Fuyao's explicit permanent symbol errors."""

    normalized = " ".join(message.strip().lower().split())
    if code == 1002 and "unknown" in normalized and "thscode" in normalized:
        return "unknown thscode"
    if code == 3001 and "fund" in normalized and "not found" in normalized:
        return "fund not found"
    unsupported_asset = "unsupported" in normalized and "asset" in normalized
    unsupported_fund_market_data = "fund" in normalized and "not support market data" in normalized
    if code == 3004 and (unsupported_asset or unsupported_fund_market_data):
        return "unsupported asset"
    return None


def _negative_capability_key(
    base_url: str,
    path: str,
    params: dict[str, object],
) -> tuple[str, str, str] | None:
    """Scope soft negatives to one endpoint and one unambiguous symbol."""

    raw_symbol = params.get("thscode")
    if isinstance(raw_symbol, str):
        symbol = raw_symbol.strip().upper()
    else:
        raw_symbols = params.get("thscodes")
        if not isinstance(raw_symbols, str):
            return None
        symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
        if len(symbols) != 1:
            return None
        symbol = symbols[0]
    if not symbol:
        return None
    return base_url.rstrip("/"), path, symbol


def _count_mix(counts: Counter[str]) -> str:
    return ",".join(
        f"{key}={count}"
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def _fuyao_clients(provider: object) -> list[FuyaoClient]:
    stack = [provider]
    seen: set[int] = set()
    clients: list[FuyaoClient] = []
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, FuyaoClient):
            clients.append(current)
        for attribute in (
            "provider",
            "market_data_provider",
            "snapshot_provider",
            "primary",
            "fallback",
        ):
            child = getattr(current, attribute, None)
            if child is not None:
                stack.append(child)
        providers_by_market = getattr(current, "providers_by_market", None)
        if isinstance(providers_by_market, dict):
            stack.extend(providers_by_market.values())
    return clients


def fuyao_capability_manifest(*, configured: bool) -> dict[str, object]:
    return {
        "provider_id": "fuyao",
        "configured": configured,
        "source_role": "read_only_market_and_research_data",
        "execution_enabled": False,
        "decision_weight_applied": False,
        "groups": FUYAO_CAPABILITY_GROUPS,
        "supported_intervals": ["1d"],
        "minute_bars_supported": False,
        "full_market_export": {
            "api_key_supported": False,
            "browser_session_required": True,
            "qagent_automatic_import_enabled": False,
        },
        "planned_or_unavailable": [
            "minute_bars",
            "stock_basic_profile",
            "index_weights",
            "stock_to_index_membership",
        ],
    }


def financial_report_candidates(as_of: date) -> list[str]:
    current_year = as_of.year
    current_quarter = min(4, max(1, (as_of.month - 1) // 3 + 1))
    reports: list[str] = []
    for year in range(current_year, current_year - 3, -1):
        quarter = current_quarter if year == current_year else 4
        reports.extend(f"{year}-{value}" for value in range(quarter, 0, -1))
    return reports


def to_fuyao_thscode(instrument_id: str) -> str:
    value = instrument_id.strip().upper()
    if not value.startswith("CN:"):
        raise ValueError(f"Fuyao supports CN instruments only: {instrument_id}")
    symbol = value.split(":", 1)[1]
    is_index = symbol.endswith(".IDX")
    if is_index:
        symbol = symbol.removesuffix(".IDX")

    if "." in symbol:
        ticker, exchange = symbol.rsplit(".", 1)
        if exchange not in {"SH", "SZ", "BJ"}:
            raise ValueError(f"Unsupported CN exchange suffix: {exchange}")
    else:
        ticker = symbol
        if is_index:
            exchange = "SZ" if ticker.startswith("399") else "SH"
        elif ticker.startswith(("4", "8", "920")):
            exchange = "BJ"
        elif ticker.startswith(("5", "6", "9")):
            exchange = "SH"
        else:
            exchange = "SZ"

    if len(ticker) != 6 or not ticker.isdigit():
        raise ValueError(f"Unsupported CN instrument code: {instrument_id}")
    return f"{ticker}.{exchange}"


def from_fuyao_thscode(thscode: str, *, is_index: bool = False) -> str:
    normalized = _normalized_thscode(thscode)
    ticker = normalized.split(".", 1)[0]
    return f"CN:{ticker}{'.IDX' if is_index else ''}"


def _strict_snapshot_rows(
    items: list[object],
    *,
    request_map: dict[str, str],
    timestamp: str,
    provider_name: str,
    request_id: str | None,
    degradation_sink: list[tuple[str, tuple[str, ...]]] | None = None,
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise FuyaoProviderError(
                "Fuyao returned a non-object snapshot item",
                code="invalid_response",
                request_id=request_id,
            )
        thscode = str(item.get("thscode", "")).strip().upper()
        if thscode not in request_map:
            raise FuyaoProviderError(
                f"Fuyao returned unexpected symbol {thscode or '<empty>'}",
                code="invalid_response",
                request_id=request_id,
            )
        if thscode in rows:
            raise FuyaoProviderError(
                f"Fuyao returned duplicate symbol {thscode}",
                code="invalid_response",
                request_id=request_id,
            )
        row, degraded_fields = _normalize_snapshot_item(
            item,
            instrument_id=request_map[thscode],
            timestamp=timestamp,
            provider_name=provider_name,
        )
        rows[thscode] = row
        if degraded_fields and degradation_sink is not None:
            degradation_sink.append((request_map[thscode], degraded_fields))
    missing = [thscode for thscode in request_map if thscode not in rows]
    if missing:
        raise FuyaoProviderError(
            f"Fuyao snapshot was incomplete; missing {', '.join(missing)}",
            code="invalid_response",
            request_id=request_id,
        )
    return [rows[thscode] for thscode in request_map]


def _normalize_snapshot_item(
    item: dict[str, Any],
    *,
    instrument_id: str,
    timestamp: str,
    provider_name: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    thscode = str(item["thscode"]).strip().upper()
    degraded_fields: list[str] = []
    close = _required_snapshot_price(item, "last_price")
    previous_close = _optional_snapshot_number(
        item,
        "prev_price",
        degraded_fields,
        positive=True,
    )
    open_price = _optional_snapshot_number(
        item,
        "open_price",
        degraded_fields,
        positive=True,
    )
    if open_price is None:
        # Previous close is the closest session-boundary proxy. A valid last price
        # is the final fallback when the upstream open and previous close are unusable.
        open_price = previous_close if previous_close is not None else close

    high = _optional_snapshot_number(
        item,
        "high_price",
        degraded_fields,
        positive=True,
    )
    minimum_high = max(open_price, close)
    if high is None or high < minimum_high:
        _mark_degraded(degraded_fields, "high_price")
        high = minimum_high

    low = _optional_snapshot_number(
        item,
        "low_price",
        degraded_fields,
        positive=True,
    )
    maximum_low = min(open_price, close)
    if low is None or low > maximum_low:
        _mark_degraded(degraded_fields, "low_price")
        low = maximum_low

    price_change = _optional_snapshot_number(item, "price_change", degraded_fields)
    if price_change is None and previous_close is not None:
        price_change = close - previous_close
    change_ratio = _optional_snapshot_number(
        item,
        "price_change_ratio_pct",
        degraded_fields,
    )
    if change_ratio is None and previous_close is not None:
        change_ratio = (close - previous_close) / previous_close * 100

    row = {
        "instrument_id": instrument_id,
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "previous_close": previous_close,
        "price_change": price_change,
        "price_change_ratio_pct": change_ratio,
        "volume": _optional_snapshot_number(
            item,
            "volume",
            degraded_fields,
            non_negative=True,
        ),
        "turnover": _optional_snapshot_number(
            item,
            "turnover",
            degraded_fields,
            non_negative=True,
        ),
        "provider": provider_name,
        "thscode": thscode,
        "ticker": str(item.get("ticker", thscode.split(".", 1)[0])).strip(),
    }
    return row, tuple(degraded_fields)


def _normalize_history_data(data: dict[str, Any], start: date, end: date) -> pd.DataFrame:
    items = data.get("item")
    if not isinstance(items, list):
        raise FuyaoProviderError(
            "Fuyao history response is missing data.item",
            code="invalid_response",
        )
    rows: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            raise FuyaoProviderError(
                "Fuyao history returned a non-object bar",
                code="invalid_response",
            )
        date_ms = item.get("date_ms")
        if not isinstance(date_ms, int | float):
            raise FuyaoProviderError(
                "Fuyao history bar is missing date_ms",
                code="invalid_response",
            )
        trade_date = datetime.fromtimestamp(float(date_ms) / 1000, tz=SHANGHAI_TZ).date()
        if not start <= trade_date <= end:
            continue
        row = {
            "trade_date": trade_date,
            "open": _finite_number(item.get("open_price")),
            "high": _finite_number(item.get("high_price")),
            "low": _finite_number(item.get("low_price")),
            "close": _finite_number(item.get("close_price")),
            "volume": _finite_number(item.get("volume"), allow_none=True),
            "turnover": _finite_number(item.get("turnover"), allow_none=True),
        }
        if _valid_ohlc_row(row):
            rows.append(row)
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "open", "high", "low", "close", "volume", "turnover"]
        )
    return (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def _paired_price_history(raw: pd.DataFrame, adjusted: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    adjusted_columns = adjusted[["trade_date", "open", "high", "low", "close"]].rename(
        columns={
            "open": "adjusted_open",
            "high": "adjusted_high",
            "low": "adjusted_low",
            "close": "adjusted_close",
        }
    )
    merged = raw.merge(adjusted_columns, on="trade_date", how="left", validate="one_to_one")
    raw_close = pd.to_numeric(merged["close"], errors="coerce")
    adjusted_close = pd.to_numeric(merged["adjusted_close"], errors="coerce")
    merged["adjustment_factor"] = adjusted_close.div(raw_close.where(raw_close.ne(0)))
    merged["adjustment_type"] = merged["adjusted_close"].map(
        lambda value: "qfq" if pd.notna(value) else None
    )
    for column in ("instrument_id", "provider"):
        merged[column] = None
    return merged[BAR_COLUMNS]


def _single_price_history(raw: pd.DataFrame, *, adjustment_type: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    normalized = raw.copy()
    for field in ("open", "high", "low", "close"):
        normalized[f"adjusted_{field}"] = normalized[field]
    normalized["adjustment_factor"] = 1.0
    normalized["adjustment_type"] = adjustment_type
    normalized["instrument_id"] = None
    normalized["provider"] = None
    return normalized[BAR_COLUMNS]


def _request_map(instrument_ids: list[str]) -> dict[str, str]:
    normalized_ids = list(dict.fromkeys(item.strip().upper() for item in instrument_ids))
    request_map: dict[str, str] = {}
    for instrument_id in normalized_ids:
        thscode = to_fuyao_thscode(instrument_id)
        if thscode in request_map:
            raise ValueError(f"Duplicate Fuyao symbol after normalization: {thscode}")
        request_map[thscode] = instrument_id
    return request_map


def _history_params(
    thscode: str,
    start: date,
    end: date,
    *,
    adjust: str | None = None,
    allow_ti: bool = False,
) -> dict[str, object]:
    if end < start:
        raise ValueError("history end date must be on or after start date")
    return {
        "thscode": _normalized_thscode(thscode, allow_ti=allow_ti),
        "interval": "1d",
        "start": _date_to_epoch_ms(start),
        "end": _date_to_epoch_ms(end, end_of_day=True),
        "adjust": adjust,
    }


def _date_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    maximum_span = timedelta(days=365 * 4 + 300)
    while cursor <= end:
        chunk_end = min(end, cursor + maximum_span)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _date_to_epoch_ms(value: date, *, end_of_day: bool = False) -> int:
    moment = datetime.combine(
        value,
        datetime_time.max if end_of_day else datetime_time.min,
        tzinfo=SHANGHAI_TZ,
    )
    return int(moment.timestamp() * 1000)


def _normalized_thscode(
    value: str,
    *,
    allow_ti: bool = False,
    allow_of: bool = False,
) -> str:
    normalized = value.strip().upper()
    allowed = {"SH", "SZ", "BJ"}
    if allow_ti:
        allowed.add("TI")
    if allow_of:
        allowed.add("OF")
    if "." not in normalized:
        raise ValueError(f"Fuyao symbol must include a market suffix: {value}")
    ticker, suffix = normalized.rsplit(".", 1)
    if len(ticker) != 6 or not ticker.isdigit() or suffix not in allowed:
        raise ValueError(f"Unsupported Fuyao symbol: {value}")
    return normalized


def _joined_thscodes(values: list[str], *, allow_ti: bool = False) -> str:
    normalized = list(
        dict.fromkeys(_normalized_thscode(value, allow_ti=allow_ti) for value in values)
    )
    if not normalized:
        raise ValueError("at least one Fuyao symbol is required")
    return ",".join(normalized)


def _request_metadata(payload: dict[str, Any], path: str) -> FuyaoRequestMetadata:
    data = payload.get("data")
    raw_timestamp = data.get("timestamp") if isinstance(data, dict) else None
    timestamp_ms = raw_timestamp if isinstance(raw_timestamp, int) else None
    return FuyaoRequestMetadata(
        request_id=_request_id(payload),
        timestamp_ms=timestamp_ms,
        timestamp=_timestamp_iso(timestamp_ms) if timestamp_ms is not None else None,
        path=path,
    )


def _timestamp_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=SHANGHAI_TZ).isoformat()


def _number(item: dict[str, Any], field: str) -> float:
    return _finite_number(item.get(field), field=field)


def _required_snapshot_price(item: dict[str, Any], field: str) -> float:
    number = _number(item, field)
    if number <= 0:
        raise FuyaoProviderError(
            f"Fuyao critical field {field} must be positive",
            code="invalid_response",
        )
    return number


def _optional_snapshot_number(
    item: dict[str, Any],
    field: str,
    degraded_fields: list[str],
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float | None:
    try:
        number = _number(item, field)
    except FuyaoProviderError:
        _mark_degraded(degraded_fields, field)
        return None
    if (positive and number <= 0) or (non_negative and number < 0):
        _mark_degraded(degraded_fields, field)
        return None
    return number


def _mark_degraded(degraded_fields: list[str], field: str) -> None:
    if field not in degraded_fields:
        degraded_fields.append(field)


def _finite_number(
    value: object,
    *,
    field: str = "value",
    allow_none: bool = False,
) -> float | None:
    if allow_none and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FuyaoProviderError(
            f"Fuyao field {field} was not numeric",
            code="invalid_response",
        )
    number = float(value)
    if not math.isfinite(number):
        raise FuyaoProviderError(
            f"Fuyao field {field} was not finite",
            code="invalid_response",
        )
    return number


def _valid_ohlc_row(row: dict[str, object]) -> bool:
    open_price = float(row["open"])
    high_price = float(row["high"])
    low_price = float(row["low"])
    close_price = float(row["close"])
    volume = row.get("volume")
    return (
        open_price > 0
        and low_price > 0
        and high_price >= max(open_price, close_price)
        and low_price <= min(open_price, close_price)
        and (volume is None or float(volume) >= 0)
    )


def _financial_indicator_values(data: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    abilities = data.get("abilities")
    if not isinstance(abilities, list):
        return values
    for ability in abilities:
        if not isinstance(ability, dict):
            continue
        indicators = ability.get("indicators")
        if not isinstance(indicators, list):
            continue
        for indicator in indicators:
            if not isinstance(indicator, dict):
                continue
            index_id = indicator.get("index_id")
            value = indicator.get("value")
            if isinstance(index_id, str) and value is not None:
                values[index_id] = str(value)
    return values


def _valid_report(report: str) -> bool:
    year, separator, quarter = report.partition("-")
    return (
        separator == "-" and len(year) == 4 and year.isdigit() and quarter in {"1", "2", "3", "4"}
    )


def _is_index_instrument(instrument_id: str) -> bool:
    return instrument_id.strip().upper().endswith(".IDX")


def _is_etf_instrument(instrument_id: str) -> bool:
    normalized = instrument_id.strip().upper()
    if not normalized.startswith("CN:"):
        return False
    symbol = normalized.split(":", 1)[1].split(".", 1)[0]
    return len(symbol) == 6 and symbol.startswith(
        ("15", "16", "18", "50", "51", "52", "55", "56", "58")
    )


def _validate_batch_size(values: list[str], *, maximum: int, label: str) -> None:
    count = len(dict.fromkeys(values))
    if count == 0:
        raise ValueError(f"{label} requires at least one symbol")
    if count > maximum:
        raise ValueError(f"{label} accepts at most {maximum} symbols")


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _request_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("request_id")
    return str(value) if value else None


def _safe_message(value: Any, api_key: str) -> str:
    message = str(value or "unknown error").replace(api_key, "[redacted]")
    return message[:200]
