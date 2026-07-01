from __future__ import annotations

from datetime import date, datetime, timedelta
import random
import time
from typing import Protocol

import requests

from qagent.config import get_settings
from qagent.db import create_session_factory, initialize_database
from qagent.domain.models import (
    AShareDragonTigerInsight,
    AShareEnhancedSnapshot,
    AShareFundFlowInsight,
    AShareLimitSentiment,
    AShareResearchCoverage,
    AShareRiskEventProfile,
    OpportunityCard,
)
from qagent.storage.astock_enhanced_cache import AShareEnhancedCacheRepository

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_API = "https://reportapi.eastmoney.com/report/list"
ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"


class AShareEnhancedProvider(Protocol):
    name: str
    last_errors: list[str]

    def get_snapshots(
        self,
        instrument_ids: list[str],
        as_of: date,
    ) -> dict[str, AShareEnhancedSnapshot]:
        ...


class EmptyAShareEnhancedDataProvider:
    name = "a_share_enhanced_disabled"
    last_errors: list[str] = []

    def get_snapshots(
        self,
        instrument_ids: list[str],
        as_of: date,
    ) -> dict[str, AShareEnhancedSnapshot]:
        return {}


class AStockEnhancedDataProvider:
    name = "a_stock_data_reference"

    def __init__(
        self,
        client: requests.Session | None = None,
        cache: AShareEnhancedCacheRepository | None = None,
        cache_ttl: timedelta | None = None,
        min_request_interval_seconds: float = 1.05,
        request_timeout_seconds: int = 12,
    ):
        self.client = client or requests.Session()
        self.cache = cache
        self.cache_ttl = cache_ttl or timedelta(hours=6)
        self.min_request_interval_seconds = min_request_interval_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.last_errors: list[str] = []
        self.cache_hits = 0
        self.cache_misses = 0
        self._last_request_at = 0.0
        self._limit_pool_cache: dict[str, tuple[list[dict], list[dict], list[dict]]] = {}

    def get_snapshots(
        self,
        instrument_ids: list[str],
        as_of: date,
    ) -> dict[str, AShareEnhancedSnapshot]:
        self.last_errors = []
        snapshots: dict[str, AShareEnhancedSnapshot] = {}
        for instrument_id in instrument_ids:
            code = _cn_code(instrument_id)
            if code is None:
                continue
            try:
                cached = self._load_cached(instrument_id, as_of)
                if cached is not None:
                    snapshots[instrument_id] = cached
                    continue
                snapshot = self._snapshot(instrument_id, code, as_of)
                self._save_cached(instrument_id, snapshot)
                snapshots[instrument_id] = snapshot
            except Exception as exc:  # pragma: no cover - network/source guard
                self.last_errors.append(f"{instrument_id}: {exc}")
        return snapshots

    def _load_cached(self, instrument_id: str, as_of: date) -> AShareEnhancedSnapshot | None:
        if self.cache is None:
            return None
        cached = self.cache.load_snapshot(
            provider=self.name,
            instrument_id=instrument_id,
            as_of=as_of,
            max_age=self.cache_ttl,
        )
        if cached is None:
            self.cache_misses += 1
            return None
        self.cache_hits += 1
        return cached

    def _save_cached(self, instrument_id: str, snapshot: AShareEnhancedSnapshot) -> None:
        if self.cache is None:
            return
        self.cache.save_snapshot(snapshot, instrument_id)

    def _snapshot(self, instrument_id: str, code: str, as_of: date) -> AShareEnhancedSnapshot:
        fund_flow = self._fund_flow(code)
        dragon_tiger = self._dragon_tiger(code, as_of)
        limit_sentiment = self._limit_sentiment(code, as_of)
        risk_events = self._risk_events(code, as_of)
        research_coverage = self._research_coverage(code)
        signals = _snapshot_signals(
            fund_flow,
            dragon_tiger,
            limit_sentiment,
            risk_events,
            research_coverage,
        )
        warnings = list(risk_events.warnings)
        score = _clamp(
            fund_flow.score * 0.28
            + dragon_tiger.score * 0.18
            + limit_sentiment.score * 0.2
            + risk_events.score * 0.2
            + research_coverage.score * 0.14,
            0,
            1,
        )
        status = "ready" if signals else "watch"
        if risk_events.score < 0.45:
            status = "risk"
        return AShareEnhancedSnapshot(
            status=status,
            score=round(score, 4),
            provider=self.name,
            as_of=as_of,
            fund_flow=fund_flow,
            dragon_tiger=dragon_tiger,
            limit_sentiment=limit_sentiment,
            risk_events=risk_events,
            research_coverage=research_coverage,
            signals=signals,
            warnings=warnings,
            summary=_snapshot_summary(score, fund_flow, dragon_tiger, limit_sentiment, risk_events),
        )

    def _fund_flow(self, code: str) -> AShareFundFlowInsight:
        rows = self._stock_fund_flow_120d(code)
        recent = rows[-20:]
        if not recent:
            return AShareFundFlowInsight(
                trend="missing",
                score=0.45,
                summary="未取得近端资金流数据。",
            )
        main_total = sum(row["main_net"] for row in recent)
        super_total = sum(row["super_net"] for row in recent)
        latest = recent[-1]["main_net"]
        trend = "positive" if main_total > 0 and latest >= 0 else "negative" if main_total < 0 else "mixed"
        score = 0.5 + _bounded(main_total / 500_000_000, -0.25, 0.25)
        score += 0.07 if latest > 0 else -0.07 if latest < 0 else 0
        return AShareFundFlowInsight(
            trend=trend,
            score=round(_clamp(score, 0, 1), 4),
            lookback_days=len(recent),
            main_net_inflow_20d=round(main_total, 2),
            super_net_inflow_20d=round(super_total, 2),
            latest_main_net_inflow=round(latest, 2),
            summary=f"近{len(recent)}日主力净流入{main_total / 100_000_000:.2f}亿。",
        )

    def _stock_fund_flow_120d(self, code: str) -> list[dict[str, float | str]]:
        market_code = 1 if code.startswith("6") else 0
        payload = self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={
                "secid": f"{market_code}.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "lmt": "120",
            },
            headers={
                "User-Agent": UA,
                "Referer": "https://quote.eastmoney.com/",
                "Origin": "https://quote.eastmoney.com",
            },
        )
        rows = []
        for line in (payload.get("data") or {}).get("klines", []) or []:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            rows.append(
                {
                    "date": parts[0],
                    "main_net": _float(parts[1]),
                    "small_net": _float(parts[2]),
                    "mid_net": _float(parts[3]),
                    "large_net": _float(parts[4]),
                    "super_net": _float(parts[5]),
                }
            )
        return rows

    def _dragon_tiger(self, code: str, as_of: date) -> AShareDragonTigerInsight:
        start = as_of - timedelta(days=30)
        rows = self._datacenter(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_str=(
                f"(TRADE_DATE>='{start.isoformat()}')"
                f"(TRADE_DATE<='{as_of.isoformat()}')"
                f"(SECURITY_CODE=\"{code}\")"
            ),
            page_size=50,
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        records = [
            {
                "date": _date_from_text(row.get("TRADE_DATE")),
                "reason": str(row.get("EXPLANATION") or ""),
                "net_buy_wan": round(_float(row.get("BILLBOARD_NET_AMT")) / 10_000, 1),
            }
            for row in rows
        ]
        if not records:
            return AShareDragonTigerInsight(
                score=0.48,
                summary="近30日未发现龙虎榜记录。",
            )
        latest = records[0]
        net_buy = float(latest["net_buy_wan"])
        institution_net = self._institution_net_buy(code, latest["date"])
        score = 0.55 + (0.1 if net_buy > 0 else -0.08) + min(0.12, len(records) * 0.03)
        if institution_net and institution_net > 0:
            score += 0.07
        return AShareDragonTigerInsight(
            score=round(_clamp(score, 0, 1), 4),
            recent_records=len(records),
            latest_date=latest["date"],
            latest_reason=str(latest["reason"]),
            latest_net_buy_wan=net_buy,
            institution_net_buy_wan=institution_net,
            summary=f"近30日龙虎榜{len(records)}次，最近净买入{net_buy:.1f}万。",
        )

    def _institution_net_buy(self, code: str, trade_date: date | None) -> float | None:
        if trade_date is None:
            return None
        buy_rows = self._datacenter(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_str=f"(TRADE_DATE='{trade_date.isoformat()}')(SECURITY_CODE=\"{code}\")",
            page_size=10,
            sort_columns="BUY",
            sort_types="-1",
        )
        sell_rows = self._datacenter(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_str=f"(TRADE_DATE='{trade_date.isoformat()}')(SECURITY_CODE=\"{code}\")",
            page_size=10,
            sort_columns="SELL",
            sort_types="-1",
        )
        buy = sum(_float(row.get("BUY")) for row in buy_rows if str(row.get("OPERATEDEPT_CODE", "")) == "0")
        sell = sum(_float(row.get("SELL")) for row in sell_rows if str(row.get("OPERATEDEPT_CODE", "")) == "0")
        if buy == 0 and sell == 0:
            return None
        return round((buy - sell) / 10_000, 1)

    def _limit_sentiment(self, code: str, as_of: date) -> AShareLimitSentiment:
        zt, zb, dt = self._limit_pools(as_of)
        zt_n = len(zt)
        zb_n = len(zb)
        break_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0.0
        max_height = max((_int(row.get("lbc")) for row in zt), default=0)
        member = next((row for row in zt if str(row.get("c")) == code), None)
        member_status = "limit_up" if member else "break_board" if any(str(row.get("c")) == code for row in zb) else "limit_down" if any(str(row.get("c")) == code for row in dt) else "none"
        score = 0.5
        score += 0.12 if zt_n >= 40 else -0.08 if zt_n < 15 else 0.02
        score += 0.08 if break_rate <= 25 else -0.08 if break_rate >= 45 else 0
        score += 0.08 if max_height >= 4 else 0
        score += {"limit_up": 0.12, "break_board": -0.06, "limit_down": -0.18}.get(member_status, 0)
        member_reason = _limit_member_reason(member) if member else None
        return AShareLimitSentiment(
            score=round(_clamp(score, 0, 1), 4),
            date=as_of,
            limit_up_count=zt_n,
            break_board_count=zb_n,
            limit_down_count=len(dt),
            break_rate_pct=break_rate,
            max_height=max_height,
            member_status=member_status,
            member_reason=member_reason,
            summary=(
                f"涨停{zt_n}家，炸板率{break_rate:.1f}%，最高{max_height}连板"
                + (f"；该股状态：{member_status}。" if member_status != "none" else "。")
            ),
        )

    def _limit_pools(self, as_of: date) -> tuple[list[dict], list[dict], list[dict]]:
        date_key = as_of.strftime("%Y%m%d")
        cached = self._limit_pool_cache.get(date_key)
        if cached is not None:
            return cached
        pools = (
            self._limit_pool("getTopicZTPool", "fbt:asc", date_key),
            self._limit_pool("getTopicZBPool", "fbt:asc", date_key),
            self._limit_pool("getTopicDTPool", "fund:asc", date_key),
        )
        self._limit_pool_cache[date_key] = pools
        return pools

    def _limit_pool(self, endpoint: str, sort: str, date_key: str) -> list[dict]:
        payload = self._get_json(
            f"https://push2ex.eastmoney.com/{endpoint}",
            params={
                "ut": ZTB_UT,
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "pagesize": 10000,
                "sort": sort,
                "date": date_key,
            },
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
        )
        return (payload.get("data") or {}).get("pool") or []

    def _risk_events(self, code: str, as_of: date) -> AShareRiskEventProfile:
        upcoming = self._datacenter(
            "RPT_LIFT_STAGE",
            filter_str=(
                f"(SECURITY_CODE=\"{code}\")"
                f"(FREE_DATE>='{as_of.isoformat()}')"
                f"(FREE_DATE<='{(as_of + timedelta(days=90)).isoformat()}')"
            ),
            page_size=20,
            sort_columns="FREE_DATE",
            sort_types="1",
        )
        margin = self._datacenter(
            "RPTA_WEB_RZRQ_GGMX",
            filter_str=f'(SCODE="{code}")',
            page_size=30,
            sort_columns="DATE",
            sort_types="-1",
        )
        max_ratio = max((_float(row.get("FREE_RATIO")) for row in upcoming), default=0.0)
        margin_change = _margin_change_pct(margin)
        warnings = []
        score = 0.92
        if upcoming:
            warnings.append(f"未来90天有{len(upcoming)}批解禁，最高解禁比例{max_ratio:.2f}%。")
            score -= min(0.28, max_ratio / 20)
        if margin_change is not None and margin_change > 20:
            warnings.append(f"融资融券余额快速上升 {margin_change:.1f}%，波动风险增加。")
            score -= 0.08
        return AShareRiskEventProfile(
            score=round(_clamp(score, 0, 1), 4),
            upcoming_lockup_count=len(upcoming),
            max_lockup_ratio_pct=round(max_ratio, 2) if upcoming else None,
            margin_balance_change_pct=margin_change,
            warnings=warnings,
            summary="；".join(warnings) if warnings else "未发现近期解禁或融资融券异常风险。",
        )

    def _research_coverage(self, code: str) -> AShareResearchCoverage:
        payload = self._get_json(
            REPORT_API,
            params={
                "industryCode": "*",
                "pageSize": "20",
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": "2024-01-01",
                "endTime": "2030-01-01",
                "pageNo": "1",
                "fields": "",
                "qType": "0",
                "orgCode": "",
                "code": code,
                "rcode": "",
                "p": "1",
                "pageNum": "1",
                "pageNumber": "1",
            },
            headers={"Referer": "https://data.eastmoney.com/"},
            timeout=20,
        )
        rows = payload.get("data") or []
        if not rows:
            return AShareResearchCoverage(score=0.45, summary="未取得近端研报覆盖。")
        latest = rows[0]
        rating = str(latest.get("emRatingName") or "")
        score = 0.55 + min(0.18, len(rows) * 0.02)
        if any(word in rating for word in ["买入", "增持", "强烈推荐"]):
            score += 0.1
        return AShareResearchCoverage(
            score=round(_clamp(score, 0, 1), 4),
            report_count=len(rows),
            latest_report_date=_date_from_text(latest.get("publishDate")),
            latest_title=str(latest.get("title") or "") or None,
            latest_rating=rating or None,
            summary=f"近端研报{len(rows)}篇" + (f"，最新评级{rating}。" if rating else "。"),
        )

    def _datacenter(
        self,
        report_name: str,
        *,
        filter_str: str = "",
        page_size: int = 50,
        sort_columns: str = "",
        sort_types: str = "-1",
    ) -> list[dict]:
        payload = self._get_json(
            DATACENTER_URL,
            params={
                "reportName": report_name,
                "columns": "ALL",
                "filter": filter_str,
                "pageNumber": "1",
                "pageSize": str(page_size),
                "sortColumns": sort_columns,
                "sortTypes": sort_types,
                "source": "WEB",
                "client": "WEB",
            },
            timeout=15,
        )
        return (payload.get("result") or {}).get("data") or []

    def _get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: int | None = None,
    ) -> dict:
        self._throttle()
        response = self.client.get(
            url,
            params=params,
            headers=headers or {"User-Agent": UA},
            timeout=timeout or self.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _throttle(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        wait = self.min_request_interval_seconds - (time.time() - self._last_request_at)
        if wait > 0:
            time.sleep(wait + random.uniform(0.05, 0.2))
        self._last_request_at = time.time()


def build_a_share_enhanced_provider(
    mode: str,
    market_provider_name: str,
) -> AShareEnhancedProvider:
    settings = get_settings()
    if (
        not settings.a_share_enhanced_data_enabled
        or mode == "fixture"
        or market_provider_name == "fixture"
    ):
        return EmptyAShareEnhancedDataProvider()
    return AStockEnhancedDataProvider(
        cache=_build_default_cache(),
        cache_ttl=timedelta(hours=settings.a_share_enhanced_cache_ttl_hours),
        min_request_interval_seconds=settings.a_share_enhanced_min_interval_seconds,
        request_timeout_seconds=settings.a_share_enhanced_timeout_seconds,
    )


def _build_default_cache() -> AShareEnhancedCacheRepository:
    initialize_database()
    return AShareEnhancedCacheRepository(create_session_factory())


def apply_a_share_enhanced_to_cards(
    cards: list[OpportunityCard],
    snapshots: dict[str, AShareEnhancedSnapshot],
) -> None:
    for card in cards:
        snapshot = snapshots.get(card.instrument_id)
        if snapshot is None:
            continue
        card.a_share_enhanced = snapshot
        adjustment = _bounded((snapshot.score - 0.5) * 0.12, -0.06, 0.06)
        card.rank_score = round(_clamp(card.rank_score + adjustment, 0, 1), 4)
        card.quality_score = round(_clamp((card.quality_score or card.rank_score) + adjustment, 0, 1), 4)
        _add_unique(card.rank_reasons, f"A股增强数据：{snapshot.summary}")
        if snapshot.fund_flow.trend == "positive":
            _add_unique(card.rank_reasons, f"资金流确认：{snapshot.fund_flow.summary}")
        if (snapshot.dragon_tiger.latest_net_buy_wan or 0) > 0:
            _add_unique(card.rank_reasons, f"龙虎榜确认：{snapshot.dragon_tiger.summary}")
        if snapshot.limit_sentiment.member_status != "none":
            _add_unique(card.rank_reasons, f"涨停情绪：{snapshot.limit_sentiment.summary}")
        for warning in snapshot.warnings:
            _add_unique(card.calibration_notes, f"A股增强数据风险：{warning}")
        for signal in snapshot.signals:
            _add_unique(card.factor_flags, signal)


def summarize_a_share_enhanced_snapshots(
    snapshots: dict[str, AShareEnhancedSnapshot],
    provider: AShareEnhancedProvider,
    requested_count: int,
) -> dict[str, str]:
    values = list(snapshots.values())
    positive_flow = sum(1 for item in values if item.fund_flow.trend == "positive")
    dragon_recent = sum(1 for item in values if item.dragon_tiger.recent_records > 0)
    limit_members = sum(1 for item in values if item.limit_sentiment.member_status != "none")
    risk_warnings = sum(1 for item in values if item.warnings)
    score = sum(item.score for item in values) / len(values) if values else 0
    health = {
        "a_share_enhanced_provider": provider.name,
        "a_share_enhanced_requested": str(requested_count),
        "a_share_enhanced_snapshots": str(len(values)),
        "a_share_enhanced_score": f"{score:.2f}",
        "a_share_enhanced_fund_flow_positive": str(positive_flow),
        "a_share_enhanced_dragon_tiger_recent": str(dragon_recent),
        "a_share_enhanced_limit_members": str(limit_members),
        "a_share_enhanced_risk_warnings": str(risk_warnings),
    }
    if provider.last_errors:
        health["a_share_enhanced_errors"] = " | ".join(provider.last_errors[:3])
    cache_hits = getattr(provider, "cache_hits", None)
    cache_misses = getattr(provider, "cache_misses", None)
    if cache_hits is not None:
        health["a_share_enhanced_cache_hits"] = str(cache_hits)
    if cache_misses is not None:
        health["a_share_enhanced_cache_misses"] = str(cache_misses)
    return health


def _snapshot_signals(
    fund_flow: AShareFundFlowInsight,
    dragon_tiger: AShareDragonTigerInsight,
    limit_sentiment: AShareLimitSentiment,
    risk_events: AShareRiskEventProfile,
    research_coverage: AShareResearchCoverage,
) -> list[str]:
    signals: list[str] = []
    if fund_flow.trend == "positive":
        signals.append("fund_flow_positive")
    if (dragon_tiger.latest_net_buy_wan or 0) > 0:
        signals.append("dragon_tiger_net_buy")
    if limit_sentiment.member_status == "limit_up":
        signals.append("limit_up_member")
    if risk_events.warnings:
        signals.append("risk_event_watch")
    if research_coverage.report_count > 0:
        signals.append("research_coverage")
    return signals


def _snapshot_summary(
    score: float,
    fund_flow: AShareFundFlowInsight,
    dragon_tiger: AShareDragonTigerInsight,
    limit_sentiment: AShareLimitSentiment,
    risk_events: AShareRiskEventProfile,
) -> str:
    parts = [fund_flow.summary]
    if dragon_tiger.recent_records:
        parts.append(dragon_tiger.summary)
    if limit_sentiment.member_status != "none":
        parts.append(limit_sentiment.summary)
    if risk_events.warnings:
        parts.append(risk_events.summary)
    return f"增强分{score:.2f}；" + "；".join(parts)


def _cn_code(instrument_id: str) -> str | None:
    if not instrument_id.startswith("CN:"):
        return None
    code = instrument_id.split(":", 1)[1].split(".", 1)[0]
    return code if len(code) == 6 and code.isdigit() else None


def _limit_member_reason(row: dict | None) -> str | None:
    if not row:
        return None
    industry = str(row.get("hybk") or "")
    stat = row.get("zttj") or {}
    if isinstance(stat, dict):
        board = f'{stat.get("days", "?")}天{stat.get("ct", "?")}板'
        return f"{industry} {board}".strip()
    return industry or None


def _margin_change_pct(rows: list[dict]) -> float | None:
    if len(rows) < 2:
        return None
    latest = _float(rows[0].get("RZRQYE"))
    previous = _float(rows[-1].get("RZRQYE"))
    if previous == 0:
        return None
    return round((latest / previous - 1) * 100, 2)


def _date_from_text(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _float(value: object) -> float:
    if value in {None, "", "-"}:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _bounded(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _add_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
