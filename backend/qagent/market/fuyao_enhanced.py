from __future__ import annotations

from datetime import date, timedelta

from qagent.domain.models import (
    AShareDragonTigerInsight,
    AShareEnhancedSnapshot,
    AShareFundFlowInsight,
    AShareLimitSentiment,
    AShareResearchCoverage,
    AShareRiskEventProfile,
)
from qagent.providers.fuyao import FuyaoClient, to_fuyao_thscode
from qagent.storage.astock_enhanced_cache import AShareEnhancedCacheRepository

LIMIT_POOL_PAGE_SIZE = 200
MAX_LIMIT_POOL_PAGES = 10


class FuyaoAShareEnhancedDataProvider:
    """Research-only card enrichment backed by published Fuyao API contracts."""

    name = "fuyao_official_enhanced"

    def __init__(
        self,
        client: FuyaoClient,
        *,
        cache: AShareEnhancedCacheRepository | None = None,
        cache_ttl: timedelta = timedelta(hours=6),
    ):
        self.client = client
        self.cache = cache
        self.cache_ttl = cache_ttl
        self.last_errors: list[str] = []
        self.cache_hits = 0
        self.cache_misses = 0
        self.source_request_ids: dict[str, str] = {}
        self.source_timestamps: dict[str, str] = {}
        self.coverage_health: dict[str, str] = {}
        self.capability_status: dict[str, str] = {
            "fund_flow": "unsupported",
            "announcements": "unsupported",
            "index_constituents": "available_current_only",
            "turnover": "available_in_market_bars",
            "fundamentals": "available_not_ingested_point_in_time",
            "valuations": "available_latest_only",
            "dragon_tiger": "not_requested",
            "limit_sentiment": "not_requested",
        }

    def get_snapshots(
        self,
        instrument_ids: list[str],
        as_of: date,
    ) -> dict[str, AShareEnhancedSnapshot]:
        self.last_errors = []
        requested = [
            instrument_id
            for instrument_id in dict.fromkeys(instrument_ids)
            if _is_supported_stock(instrument_id)
        ]
        snapshots: dict[str, AShareEnhancedSnapshot] = {}
        uncached: list[str] = []
        for instrument_id in requested:
            cached = self._load_cached(instrument_id, as_of)
            if cached is None:
                uncached.append(instrument_id)
            else:
                snapshots[instrument_id] = cached
        if not uncached:
            return snapshots

        dragon_data = self._load_section(
            "dragon_tiger",
            lambda: self.client.get_dragon_tiger(trade_date=as_of),
        )
        limit_data, limit_complete = self._load_limit_pool(as_of)
        if dragon_data is None and limit_data is None:
            return snapshots

        dragon_by_thscode = _items_by_thscode(dragon_data, "stock_items")
        limit_by_thscode = _items_by_thscode(limit_data, "item")
        limit_total = _nested_int(limit_data, "pagination", "total")

        for instrument_id in uncached:
            thscode = to_fuyao_thscode(instrument_id)
            snapshot = _build_snapshot(
                instrument_id=instrument_id,
                thscode=thscode,
                as_of=as_of,
                dragon_row=dragon_by_thscode.get(thscode),
                limit_row=limit_by_thscode.get(thscode),
                dragon_available=dragon_data is not None,
                limit_available=limit_data is not None,
                limit_complete=limit_complete,
                limit_total=limit_total,
            )
            snapshots[instrument_id] = snapshot
            # Do not turn a transient partial source into a six-hour negative cache.
            # Retrying incomplete snapshots lets a later page/source recover naturally.
            if dragon_data is not None and limit_complete:
                self._save_cached(instrument_id, snapshot)
        return snapshots

    def _load_limit_pool(self, as_of: date) -> tuple[dict | None, bool]:
        first = self._load_section(
            "limit_sentiment",
            lambda: self.client.get_limit_up_pool(
                trade_date=as_of,
                page=1,
                size=LIMIT_POOL_PAGE_SIZE,
            ),
        )
        if first is None:
            self.coverage_health.update(
                {
                    "limit_sentiment_pages_requested": "1",
                    "limit_sentiment_pages_succeeded": "0",
                    "limit_sentiment_rows": "0",
                    "limit_sentiment_total": "0",
                    "limit_sentiment_coverage": "0.000000",
                    "limit_sentiment_complete": "false",
                }
            )
            return None, False

        rows = list(first.get("item") or [])
        total = _nested_int(first, "pagination", "total")
        declared_pages = _nested_int(first, "pagination", "pages")
        required_pages = max(declared_pages, _ceiling_division(total, LIMIT_POOL_PAGE_SIZE), 1)
        bounded_pages = min(required_pages, MAX_LIMIT_POOL_PAGES)
        requested_pages = 1
        succeeded_pages = 1
        complete = required_pages <= MAX_LIMIT_POOL_PAGES
        for page in range(2, bounded_pages + 1):
            requested_pages += 1
            try:
                data = self.client.get_limit_up_pool(
                    trade_date=as_of,
                    page=page,
                    size=LIMIT_POOL_PAGE_SIZE,
                )
            except Exception as exc:
                self.capability_status["limit_sentiment"] = "partial_page_error"
                self.last_errors.append(f"limit_sentiment page {page}: {exc}")
                complete = False
                break
            self._capture_source_metadata(f"limit_sentiment_page_{page}")
            succeeded_pages += 1
            page_rows = data.get("item") if isinstance(data, dict) else None
            if isinstance(page_rows, list):
                rows.extend(page_rows)

        if required_pages > MAX_LIMIT_POOL_PAGES:
            self.capability_status["limit_sentiment"] = "partial_page_limit"
            complete = False
        if total > len(rows):
            complete = False
            if self.capability_status["limit_sentiment"] == "ready":
                self.capability_status["limit_sentiment"] = "partial_rows"
        coverage = min(len(rows) / total, 1.0) if total else 1.0
        self.coverage_health.update(
            {
                "limit_sentiment_pages_requested": str(requested_pages),
                "limit_sentiment_pages_succeeded": str(succeeded_pages),
                "limit_sentiment_page_limit": str(MAX_LIMIT_POOL_PAGES),
                "limit_sentiment_rows": str(len(rows)),
                "limit_sentiment_total": str(total),
                "limit_sentiment_coverage": f"{coverage:.6f}",
                "limit_sentiment_complete": str(complete).lower(),
            }
        )
        combined = dict(first)
        combined["item"] = rows
        return combined, complete

    def _load_section(self, capability: str, loader) -> dict | None:
        try:
            data = loader()
        except Exception as exc:
            self.capability_status[capability] = "error"
            self.last_errors.append(f"{capability}: {exc}")
            return None
        self.capability_status[capability] = "ready"
        self._capture_source_metadata(capability)
        return data

    def _capture_source_metadata(self, capability: str) -> None:
        metadata = getattr(self.client, "last_request", None)
        if metadata is not None:
            if metadata.request_id:
                self.source_request_ids[capability] = metadata.request_id
            if metadata.timestamp:
                self.source_timestamps[capability] = metadata.timestamp

    def _load_cached(
        self,
        instrument_id: str,
        as_of: date,
    ) -> AShareEnhancedSnapshot | None:
        if self.cache is None:
            return None
        try:
            cached = self.cache.load_snapshot(
                provider=self.name,
                instrument_id=instrument_id,
                as_of=as_of,
                max_age=self.cache_ttl,
            )
        except Exception as exc:
            self.last_errors.append(f"{instrument_id}: cache read: {exc}")
            self.cache_misses += 1
            return None
        if cached is None:
            self.cache_misses += 1
            return None
        self.cache_hits += 1
        return cached

    def _save_cached(self, instrument_id: str, snapshot: AShareEnhancedSnapshot) -> None:
        if self.cache is None:
            return
        try:
            self.cache.save_snapshot(snapshot, instrument_id)
        except Exception as exc:
            self.last_errors.append(f"{instrument_id}: cache write: {exc}")


def _build_snapshot(
    *,
    instrument_id: str,
    thscode: str,
    as_of: date,
    dragon_row: dict | None,
    limit_row: dict | None,
    dragon_available: bool,
    limit_available: bool,
    limit_complete: bool,
    limit_total: int,
) -> AShareEnhancedSnapshot:
    del instrument_id, thscode
    dragon = _dragon_tiger_insight(dragon_row, as_of, available=dragon_available)
    limit = _limit_sentiment_insight(
        limit_row,
        as_of,
        available=limit_available,
        complete=limit_complete,
        market_total=limit_total,
    )
    scores = []
    if dragon_available:
        scores.append(dragon.score)
    if limit_available:
        scores.append(limit.score)
    score = sum(scores) / len(scores) if scores else 0.5
    signals: list[str] = []
    if (dragon.latest_net_buy_wan or 0) > 0:
        signals.append("dragon_tiger_net_buy")
    if limit.member_status == "limit_up":
        signals.append("limit_up_member")
    available_count = int(dragon_available) + int(limit_available)
    status = "ready" if available_count == 2 else "partial"
    return AShareEnhancedSnapshot(
        status=status,
        score=round(score, 4),
        provider=FuyaoAShareEnhancedDataProvider.name,
        as_of=as_of,
        fund_flow=AShareFundFlowInsight(
            trend="unsupported",
            score=0.5,
            summary="扶摇官方 API 当前未提供个股资金流字段（unsupported）。",
        ),
        dragon_tiger=dragon,
        limit_sentiment=limit,
        risk_events=AShareRiskEventProfile(
            score=0.5,
            summary="扶摇官方 API 当前未提供逐股风险事件接口（available_later）。",
        ),
        research_coverage=AShareResearchCoverage(
            score=0.5,
            summary="扶摇官方 API 当前未提供 A 股公告或研报覆盖接口（available_later）。",
        ),
        signals=signals,
        warnings=[],
        summary=(
            f"扶摇官方交易日增强：龙虎榜{_availability(dragon_available)}，"
            f"涨停池{_availability(limit_available)}；研究展示，不参与排序。"
        ),
    )


def _dragon_tiger_insight(
    row: dict | None,
    as_of: date,
    *,
    available: bool,
) -> AShareDragonTigerInsight:
    if not available:
        return AShareDragonTigerInsight(score=0.5, summary="龙虎榜来源请求失败，已隔离。")
    if row is None:
        return AShareDragonTigerInsight(
            score=0.5,
            recent_records=0,
            summary=f"{as_of.isoformat()} 未进入扶摇龙虎榜股票榜单。",
        )
    net_value = _float_or_none(row.get("net_value"))
    org_net_value = _float_or_none(row.get("org_net_value"))
    net_wan = net_value / 10_000 if net_value is not None else None
    org_wan = org_net_value / 10_000 if org_net_value is not None else None
    score = 0.5
    if net_value is not None:
        score += 0.15 if net_value > 0 else -0.1 if net_value < 0 else 0
    if org_net_value is not None:
        score += 0.08 if org_net_value > 0 else -0.05 if org_net_value < 0 else 0
    return AShareDragonTigerInsight(
        score=_clamp(score),
        recent_records=1,
        latest_date=as_of,
        latest_reason=str(row.get("limit_reason") or "") or None,
        latest_net_buy_wan=round(net_wan, 2) if net_wan is not None else None,
        institution_net_buy_wan=round(org_wan, 2) if org_wan is not None else None,
        summary=(
            f"{as_of.isoformat()} 龙虎榜净买入"
            + (f"{net_wan:.2f}万。" if net_wan is not None else "字段为空。")
        ),
    )


def _limit_sentiment_insight(
    row: dict | None,
    as_of: date,
    *,
    available: bool,
    complete: bool,
    market_total: int,
) -> AShareLimitSentiment:
    if not available:
        return AShareLimitSentiment(
            score=0.5,
            date=as_of,
            member_status="unknown_due_to_partial_source",
            summary="涨停池来源请求失败，已隔离。",
        )
    if row is None:
        if not complete:
            return AShareLimitSentiment(
                score=0.5,
                date=as_of,
                limit_up_count=market_total,
                member_status="unknown_due_to_partial_source",
                summary=(
                    f"{as_of.isoformat()} 涨停池分页不完整，"
                    "无法判断该股是否入池。"
                ),
            )
        return AShareLimitSentiment(
            score=0.5,
            date=as_of,
            limit_up_count=market_total,
            member_status="none",
            summary=f"{as_of.isoformat()} 未进入扶摇涨停池。",
        )
    continue_days = _int_or_zero(row.get("continue_day_cnt"))
    return AShareLimitSentiment(
        score=_clamp(0.62 + min(0.18, continue_days * 0.03)),
        date=as_of,
        limit_up_count=market_total,
        max_height=continue_days,
        member_status="limit_up",
        member_reason=str(row.get("limit_up_reason") or "") or None,
        summary=(
            f"{as_of.isoformat()} 进入扶摇涨停池，"
            f"连板计数{continue_days}。"
        ),
    )


def _items_by_thscode(data: dict | None, key: str) -> dict[str, dict]:
    if not isinstance(data, dict):
        return {}
    rows = data.get(key)
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("thscode") or "").strip().upper(): row
        for row in rows
        if isinstance(row, dict) and row.get("thscode")
    }


def _nested_int(data: dict | None, outer: str, inner: str) -> int:
    if not isinstance(data, dict) or not isinstance(data.get(outer), dict):
        return 0
    return _int_or_zero(data[outer].get(inner))


def _ceiling_division(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor if value > 0 else 0


def _is_supported_stock(instrument_id: str) -> bool:
    if not instrument_id.startswith("CN:") or instrument_id.upper().endswith(".IDX"):
        return False
    code = instrument_id.split(":", 1)[1]
    return len(code) == 6 and code.isdigit() and not code.startswith(
        ("15", "16", "50", "51", "52", "56", "58")
    )


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)


def _availability(available: bool) -> str:
    return "ready" if available else "error"
