"""Explicit research adapter outside the frozen strategy source tree.

Never a historical PIT or execution source; default strategy factories stay unchanged.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

from qagent.config import Settings, get_settings
from qagent.providers.datahubco import DatahubcoClient, DatahubcoError
from qagent.providers.tushare_relay import RelayError, RelayTable, TushareRelayClient
from qagent.strategy_data.models import FundamentalSnapshot
from qagent.strategy_data.providers import BaseStrategyDataProvider


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class RelayFundamentalSnapshot(FundamentalSnapshot):
    """Current observation with explicit dates for differently timed fields."""

    valuation_date: date | None = None
    financial_announcement_date: date | None = None
    financial_period: date | None = None


def _number(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise RelayError("numeric_schema") from None
    if not result.is_finite():
        raise RelayError("numeric_schema")
    return result


def _day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise RelayError("date_schema")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise RelayError("date_schema") from None


def _symbol(instrument_id: str) -> str:
    if not re.fullmatch(r"CN:\d{6}", instrument_id):
        raise RelayError("unsupported_instrument")
    code = instrument_id[3:]
    if code.startswith("6"):
        suffix = "SH"
    elif code.startswith(("0", "3")):
        suffix = "SZ"
    elif code.startswith(("4", "8", "92")):
        suffix = "BJ"
    else:
        raise RelayError("unsupported_instrument")
    return f"{code}.{suffix}"


class TushareRelayStrategyDataProvider(BaseStrategyDataProvider):
    name = "tushare_relay_promax_current_research"

    def __init__(self, client: TushareRelayClient):
        super().__init__()
        self.client = client

    def get_fundamentals(self, instrument_ids: list[str], start: date,
                         end: date) -> list[FundamentalSnapshot]:
        self.last_errors = []
        today = _today()
        if not start <= today <= end or end != today:
            self.last_errors = ["tushare_relay:historical_pit_not_supported"]
            return []
        if len(instrument_ids) > 20:
            raise RelayError("research_batch_limit")
        results = []
        for instrument_id in instrument_ids:
            if not instrument_id.startswith("CN:"):
                continue
            try:
                symbol = _symbol(instrument_id)
                # Limit the valuation window; do not relabel stale values as today's.
                from datetime import timedelta
                begin = max(start, today - timedelta(days=10))
                valuation = self.client.query(
                    "daily_basic", ts_code=symbol, start_date=begin.strftime("%Y%m%d"),
                    end_date=today.strftime("%Y%m%d"), limit=20,
                )
                valid = []
                seen = set()
                for row in valuation.rows:
                    if row.get("ts_code") != symbol:
                        raise RelayError("symbol_mismatch")
                    trading_day = _day(row.get("trade_date"))
                    if not begin <= trading_day <= today:
                        raise RelayError("date_mismatch")
                    if trading_day in seen:
                        raise RelayError("duplicate_row")
                    seen.add(trading_day)
                    valid.append((trading_day, row))
                values = {}
                if valid:
                    trading_day, row = max(valid, key=lambda item: item[0])
                    cap = _number(row.get("total_mv"))
                    values.update(
                        valuation_date=trading_day,
                        market_cap=cap * Decimal(10000) if cap is not None else None,
                        pe_ratio=_number(row.get("pe_ttm")),
                        price_to_sales=_number(row.get("ps_ttm")),
                    )
                try:
                    values.update(self._financial_values(symbol, today))
                except RelayError as exc:
                    # Independent financial failure must not discard valid valuation.
                    self.last_errors.append(str(exc))
                if values:
                    results.append(RelayFundamentalSnapshot(
                        instrument_id=instrument_id, as_of_date=today,
                        provider=self.name, **values,
                    ))
            except RelayError as exc:
                self.last_errors.append(str(exc))
        return results

    def _financial_values(self, symbol: str, today: date) -> dict:
        financials = self.client.query("fina_indicator", ts_code=symbol, limit=100)
        valid = []
        for row in financials.rows:
            if row.get("ts_code") != symbol:
                raise RelayError("symbol_mismatch")
            announced, period = _day(row.get("ann_date")), _day(row.get("end_date"))
            if period > announced or announced > today:
                raise RelayError("date_mismatch")
            valid.append((period, announced, row))
        if not valid:
            raise RelayError("financial_no_data")
        period, announced = max((item[0], item[1]) for item in valid)
        candidates = [row for p, a, row in valid if (p, a) == (period, announced)]
        row = candidates[0]
        consumed_fields = ("tr_yoy", "netprofit_yoy", "grossprofit_margin",
                           "netprofit_margin", "roe")
        normalized = tuple(_number(row.get(key)) for key in consumed_fields)
        if any(tuple(_number(candidate.get(key)) for key in consumed_fields) != normalized
               for candidate in candidates[1:]):
            raise RelayError("ambiguous_financial_revision")
        if any(candidate != row for candidate in candidates[1:]):
            self.last_errors.append("tushare_relay:unused_field_revision_difference")
        # Compare only normalized fields consumed by this adapter; differences in
        # unused fields do not select a revision or change the resulting snapshot.
        # Conflicts in older periods do not decide this current snapshot.
        # Current retrieval may contain restatements, so it is dated today.
        return dict(
            financial_announcement_date=announced, financial_period=period,
            revenue_growth_pct=normalized[0], earnings_growth_pct=normalized[1],
            gross_margin_pct=normalized[2], net_margin_pct=normalized[3],
            return_on_equity_pct=normalized[4],
        )

    def get_research_daily_bars(self, instrument_id: str, start: date, end: date,
                                *, adjustment_anchor: date) -> RelayTable:
        """One bounded, strictly paired page; research only, not trusted prices.

        Adjusted OHLC = raw OHLC * day_factor / explicit anchor_factor.
        Missing days are not synthesized; coverage must be checked by the caller.
        """
        if start > end or end > _today() or (end - start).days > 366:
            raise RelayError("research_window_limit")
        if not start <= adjustment_anchor <= end:
            raise RelayError("invalid_anchor")
        symbol = _symbol(instrument_id)
        params = dict(ts_code=symbol, start_date=start.strftime("%Y%m%d"),
                      end_date=end.strftime("%Y%m%d"), limit=500)
        prices = self.client.query("daily", **params)
        factors = self.client.query("adj_factor", **params)
        by_day = {}
        for row in factors.rows:
            day = _day(row.get("trade_date"))
            factor = _number(row.get("adj_factor"))
            if row.get("ts_code") != symbol or not start <= day <= end:
                raise RelayError("identity_mismatch")
            if day in by_day or factor is None or factor <= 0:
                raise RelayError("factor_schema")
            by_day[day] = factor
        anchor = by_day.get(adjustment_anchor)
        if anchor is None:
            raise RelayError("missing_anchor")
        records, seen = [], set()
        for row in prices.rows:
            day = _day(row.get("trade_date"))
            if row.get("ts_code") != symbol or not start <= day <= end or day in seen:
                raise RelayError("identity_mismatch")
            seen.add(day)
            factor = by_day.get(day)
            if factor is None:
                raise RelayError("missing_factor")
            ohlc = {k: _number(row.get(k)) for k in ("open", "high", "low", "close")}
            if any(value is None or value <= 0 for value in ohlc.values()):
                raise RelayError("price_schema")
            if not ohlc["low"] <= min(ohlc["open"], ohlc["close"]) <= max(
                ohlc["open"], ohlc["close"]
            ) <= ohlc["high"]:
                raise RelayError("price_schema")
            records.append({
                "instrument_id": instrument_id, "trade_date": day,
                **{f"raw_{k}": v for k, v in ohlc.items()},
                **{f"adjusted_{k}": v * factor / anchor for k, v in ohlc.items()},
                "adj_factor": factor, "adjustment_anchor": adjustment_anchor,
                "anchor_factor": anchor,
            })
        if adjustment_anchor not in seen:
            raise RelayError("missing_anchor_price")
        records.sort(key=lambda row: row["trade_date"])
        return RelayTable("research_daily_adjusted", tuple(records[0]), tuple(records), 500)


class DatahubcoStrategyDataProvider(BaseStrategyDataProvider):
    """Bounded current observation for the existing factor comparison only."""

    name = "datahubco_current_research"

    def __init__(self, client: DatahubcoClient, *, report_period: date,
                 valuation_trade_date: date):
        super().__init__()
        self.client = client
        self.report_period = report_period
        self.valuation_trade_date = valuation_trade_date

    @staticmethod
    def _date(value) -> date:
        try:
            return _day(value)
        except RelayError as exc:
            raise DatahubcoError(exc.kind) from None

    @staticmethod
    def _numeric(value) -> Decimal | None:
        try:
            number = _number(value)
        except RelayError as exc:
            raise DatahubcoError(exc.kind) from None
        if number is not None and abs(number) > Decimal("1e30"):
            raise DatahubcoError("numeric_schema")
        return number

    def get_fundamentals(self, instrument_ids: list[str], start: date,
                         end: date) -> list[FundamentalSnapshot]:
        self.last_errors = []
        today = _today()
        if not start <= today <= end or end != today:
            self.last_errors = ["datahubco:historical_pit_not_supported"]
            return []
        if not (self.report_period <= self.valuation_trade_date <= today):
            raise DatahubcoError("invalid_research_dates")
        if len(instrument_ids) > 20:
            raise DatahubcoError("research_batch_limit")
        results = []
        for instrument_id in instrument_ids:
            try:
                symbol = _symbol(instrument_id)
                valuation = self.client.query(
                    "daily_basic", ts_code=symbol,
                    trade_date=self.valuation_trade_date.strftime("%Y%m%d"), limit=20,
                )
                if not valuation.rows:
                    raise DatahubcoError("valuation_no_data")
                values = []
                for row in valuation.rows:
                    if row.get("ts_code") != symbol:
                        raise DatahubcoError("symbol_mismatch")
                    if self._date(row.get("trade_date")) != self.valuation_trade_date:
                        raise DatahubcoError("date_mismatch")
                    values.append(tuple(self._numeric(row.get(field)) for field in
                                        ("pe_ttm", "total_mv", "ps_ttm")))
                if any(item != values[0] for item in values[1:]):
                    raise DatahubcoError("ambiguous_valuation_revision")
                pe, cap, ps = values[0]
                financial = self.client.query(
                    "fina_indicator", ts_code=symbol,
                    period=self.report_period.strftime("%Y%m%d"), limit=100,
                )
                if not financial.rows:
                    raise DatahubcoError("financial_no_data")
                candidates = []
                consumed_fields = ("tr_yoy", "netprofit_yoy", "grossprofit_margin",
                                   "netprofit_margin", "roe")
                for row in financial.rows:
                    if row.get("ts_code") != symbol:
                        raise DatahubcoError("symbol_mismatch")
                    announced = self._date(row.get("ann_date"))
                    period = self._date(row.get("end_date"))
                    if (period != self.report_period
                            or not period <= announced <= self.valuation_trade_date):
                        raise DatahubcoError("date_mismatch")
                    final_announcement = row.get("f_ann_date")
                    if final_announcement not in (None, ""):
                        final_announcement = self._date(final_announcement)
                        if not period <= final_announcement <= self.valuation_trade_date:
                            raise DatahubcoError("date_mismatch")
                    available = max(announced, final_announcement or announced)
                    candidates.append((available, tuple(self._numeric(row.get(field))
                                                        for field in consumed_fields), row))
                announcement = max(item[0] for item in candidates)
                revisions = [item for item in candidates if item[0] == announcement]
                if any(item[1] != revisions[0][1] for item in revisions[1:]):
                    raise DatahubcoError("ambiguous_financial_revision")
                if any(item[2] != revisions[0][2] for item in revisions[1:]):
                    self.last_errors.append("datahubco:unused_field_revision_difference")
                revenue, earnings, gross, net, roe = revisions[0][1]
                results.append(RelayFundamentalSnapshot(
                    instrument_id=instrument_id, as_of_date=today, provider=self.name,
                    valuation_date=self.valuation_trade_date,
                    financial_announcement_date=announcement,
                    financial_period=self.report_period,
                    pe_ratio=pe, market_cap=cap * Decimal(10000) if cap is not None else None,
                    price_to_sales=ps, revenue_growth_pct=revenue,
                    earnings_growth_pct=earnings, gross_margin_pct=gross,
                    net_margin_pct=net, return_on_equity_pct=roe,
                ))
            except (RelayError, DatahubcoError) as exc:
                self.last_errors.append(str(exc))
        return results


def build_datahubco_research_provider(
    *, report_period: date, valuation_trade_date: date,
    settings: Settings | None = None,
) -> DatahubcoStrategyDataProvider:
    settings = settings or get_settings()
    if not settings.datahubco_enabled:
        raise DatahubcoError("research_disabled")
    if settings.datahubco_key is None:
        raise DatahubcoError("missing_config")
    return DatahubcoStrategyDataProvider(
        DatahubcoClient(settings.datahubco_key.get_secret_value(),
                        allow_insecure_http=settings.datahubco_allow_insecure_http),
        report_period=report_period, valuation_trade_date=valuation_trade_date,
    )


def build_tushare_relay_research_provider(
    settings: Settings | None = None,
) -> TushareRelayStrategyDataProvider:
    """Explicit research entrypoint; settings alone do not change default providers."""
    settings = settings or get_settings()
    if not settings.tushare_relay_research_enabled:
        raise RelayError("research_disabled")
    if settings.tushare_relay_key is None:
        raise RelayError("missing_config")
    return TushareRelayStrategyDataProvider(TushareRelayClient(
        api_key=settings.tushare_relay_key.get_secret_value(),
        timeout_seconds=settings.tushare_relay_timeout_seconds,
    ))
