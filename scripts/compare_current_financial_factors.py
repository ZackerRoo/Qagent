#!/usr/bin/env python3
"""Opt-in, in-memory comparison of current ProMax inputs in the factor engine.

This is a current observation only. It cannot backfill a historical signal,
change the paper account, or provide a net-return estimate.
"""

import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

from qagent.factors.engine import build_factor_rankings
from qagent.providers.datahubco import DatahubcoError
from qagent.providers.tushare_relay import RelayError
from qagent.providers.tushare_relay_research import (
    DatahubcoStrategyDataProvider,
    TushareRelayStrategyDataProvider,
    _symbol,
    build_datahubco_research_provider,
    build_tushare_relay_research_provider,
)
from rank_g2_consensus import publish


PROTOCOL = "current-financial-factor-comparison-v1"
SOURCE = "tushare_relay_promax"
DATAHUBCO_SOURCE = "datahubco"
SAFE_WARNINGS = {"tushare_relay:unused_field_revision_difference",
                 "datahubco:unused_field_revision_difference"}
BAR_COLUMNS = ("instrument_id", "trade_date", "open", "high", "low", "close", "volume", "provider")


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=str).encode()).hexdigest()


def _day(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid_trade_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid_trade_date") from None


def _positive(value: object, *, zero_allowed: bool = False) -> float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_bar_number") from None
    if not number.is_finite() or number < 0 or (number == 0 and not zero_allowed):
        raise ValueError("invalid_bar_number")
    return float(number)


def _validated_bars(bars: pd.DataFrame, observation_day: date) -> tuple[pd.DataFrame, date, list[str], str]:
    if bars.empty or not set(BAR_COLUMNS).issubset(bars.columns):
        raise ValueError("missing_bar_columns")
    records = []
    for row in bars.loc[:, BAR_COLUMNS].to_dict("records"):
        instrument_id = row["instrument_id"]
        if not isinstance(instrument_id, str) or not re.fullmatch(r"CN:\d{6}", instrument_id):
            raise ValueError("invalid_instrument")
        provider = row["provider"]
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("missing_bar_provenance")
        trading_day = _day(row["trade_date"])
        if trading_day > observation_day:
            raise ValueError("future_bar")
        numbers = {key: _positive(row[key], zero_allowed=key == "volume")
                   for key in ("open", "high", "low", "close", "volume")}
        if not numbers["low"] <= min(numbers["open"], numbers["close"]) <= max(
            numbers["open"], numbers["close"]
        ) <= numbers["high"]:
            raise ValueError("invalid_bar_ohlc")
        records.append({"instrument_id": instrument_id, "trade_date": trading_day,
                        **numbers, "provider": provider})
    records.sort(key=lambda row: (row["instrument_id"], row["trade_date"]))
    normalized = pd.DataFrame.from_records(records)
    if normalized.duplicated(["instrument_id", "trade_date"]).any():
        raise ValueError("duplicate_bar")
    latest = normalized.groupby("instrument_id")["trade_date"].max()
    if len(latest) > 20:
        raise ValueError("research_batch_limit")
    if len(set(latest)) != 1:
        raise ValueError("mixed_latest_bar_dates")
    symbols = sorted(latest.index.tolist())
    canonical = [{**row, "trade_date": row["trade_date"].isoformat()}
                 for row in records]
    return normalized, latest.iloc[0], symbols, _digest(canonical)


class _RecordingClient:
    def __init__(self, client: object, source: str):
        self.client = client
        self.source = source
        self.queries: list[dict] = []

    def query(self, api: str, **params):
        if api not in {"daily_basic", "fina_indicator"}:
            raise RelayError("forbidden_api")
        evidence = {"api": api, "params": params}
        if self.source == DATAHUBCO_SOURCE:
            evidence["requested_source"] = self.source
        try:
            table = self.client.query(api, **params)
            if table.api != api or table.source != self.source:
                raise RelayError("source_provenance_mismatch")
            rows = [dict(row) for row in table.rows]
            evidence.update(source=table.source, row_limit=table.row_limit,
                            fields=list(table.fields), rows=rows,
                            rows_digest=_digest(rows), status="observed")
            if len(rows) >= table.row_limit:
                raise RelayError("page_coverage_unknown")
            return table
        except (RelayError, DatahubcoError) as exc:
            evidence.update(status="error", error=exc.kind)
            raise
        finally:
            self.queries.append(evidence)


def _snapshot_status(snapshot, observation_day: date, provider_name: str) -> list[str]:
    reasons = []
    if snapshot is None:
        return ["missing_snapshot"]
    if snapshot.provider != provider_name or snapshot.as_of_date != observation_day:
        reasons.append("invalid_snapshot_provenance")
    valuation_date = getattr(snapshot, "valuation_date", None)
    announcement_date = getattr(snapshot, "financial_announcement_date", None)
    financial_period = getattr(snapshot, "financial_period", None)
    if valuation_date is None or valuation_date > observation_day:
        reasons.append("valuation_date_unavailable")
    if (announcement_date is None or financial_period is None
            or not financial_period <= announcement_date <= observation_day):
        reasons.append("financial_dates_unavailable")
    if snapshot.pe_ratio is None or snapshot.pe_ratio <= 0:
        reasons.append("positive_pe_unavailable")
    if snapshot.market_cap is None or snapshot.market_cap <= 0:
        reasons.append("market_cap_unavailable")
    if snapshot.return_on_equity_pct is None:
        reasons.append("roe_unavailable")
    if snapshot.revenue_growth_pct is None and snapshot.earnings_growth_pct is None:
        reasons.append("growth_unavailable")
    return reasons


def compare_current_financial_factors(
    bars: pd.DataFrame,
    provider: TushareRelayStrategyDataProvider | DatahubcoStrategyDataProvider,
    *,
    observation_day: date,
    round_trip_cost_bps: Decimal = Decimal("10"),
    top_k: int = 5,
) -> dict:
    """Run both factor arms on one complete-case cohort, without persistence."""
    if observation_day != datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("current_observation_only")
    if not round_trip_cost_bps.is_finite() or not 0 <= round_trip_cost_bps <= 100:
        raise ValueError("invalid_cost")
    if type(top_k) is not int or top_k < 1 or top_k > 20:
        raise ValueError("invalid_top_k")
    normalized, latest_bar_date, symbols, bars_digest = _validated_bars(bars, observation_day)
    if (isinstance(provider, DatahubcoStrategyDataProvider)
            and provider.valuation_trade_date != latest_bar_date):
        raise ValueError("valuation_bar_date_mismatch")
    source = DATAHUBCO_SOURCE if isinstance(provider, DatahubcoStrategyDataProvider) else SOURCE
    recorder = _RecordingClient(provider.client, source)
    provider.client = recorder
    try:
        snapshots = provider.get_fundamentals(
            symbols, observation_day - timedelta(days=10), observation_day
        )
    finally:
        provider.client = recorder.client
    observed_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    by_id = {snapshot.instrument_id: snapshot for snapshot in snapshots}
    if len(by_id) != len(snapshots) or set(by_id) - set(symbols):
        raise ValueError("snapshot_identity_mismatch")
    excluded = [{"instrument_id": symbol, "reasons": _snapshot_status(
        by_id.get(symbol), observation_day, provider.name)}
                for symbol in symbols]
    excluded = [item for item in excluded if item["reasons"]]
    eligible = sorted(set(symbols) - {item["instrument_id"] for item in excluded})
    errors = [error for error in provider.last_errors if error not in SAFE_WARNINGS]
    errors.extend(query["error"] for query in recorder.queries if query["status"] == "error")
    query_pairs = {(query["api"], query["params"].get("ts_code"))
                   for query in recorder.queries if query["status"] == "observed"}
    expected_pairs = {(api, _symbol(symbol)) for api in ("daily_basic", "fina_indicator")
                      for symbol in symbols}
    if (len(recorder.queries) != 2 * len(symbols) or len(query_pairs) != 2 * len(symbols)
            or query_pairs != expected_pairs):
        errors.append("source_query_coverage_incomplete")
    if source == DATAHUBCO_SOURCE:
        expected_dates = {
            "daily_basic": {"trade_date": provider.valuation_trade_date.strftime("%Y%m%d")},
            "fina_indicator": {"period": provider.report_period.strftime("%Y%m%d")},
        }
        for query in recorder.queries:
            if any(query["params"].get(key) != value
                   for key, value in expected_dates[query["api"]].items()):
                errors.append("source_query_date_mismatch")
    report = {
        "protocol": PROTOCOL,
        "observation_day": observation_day.isoformat(),
        "observed_at": observed_at,
        "latest_bar_date": latest_bar_date.isoformat(),
        "bar_rows": len(normalized),
        "bar_providers": sorted(normalized["provider"].unique().tolist()),
        "bars_digest": bars_digest,
        "source": source,
        "source_queries": recorder.queries,
        "provider_warnings": [error for error in provider.last_errors if error in SAFE_WARNINGS],
        "provider_errors": sorted(set(errors)),
        "cost_assumption": {"round_trip_bps": str(round_trip_cost_bps),
                            "same_for_baseline_and_enriched": True},
        "cohort": {"input": symbols, "eligible": eligible, "excluded": excluded,
                   "same_symbols_and_bars_in_both_arms": True},
        "fundamentals": {symbol: by_id[symbol].model_dump(mode="json") for symbol in eligible},
        "baseline": None,
        "enriched": None,
        "comparison": None,
        "status": "blocked",
        "research_only": True,
        "decision_weight": False,
        "activation_allowed": False,
        "limitations": [
            "Current retrieval is not historical point-in-time evidence or a frozen G2 backfill.",
            "The factor engine has zero decision weight for its separate profitability and growth exposures.",
            "No forward returns, turnover, or net performance are computed; cost is a shared assumption only.",
            "Complete-case selection may bias this same-cohort behavioral comparison.",
        ],
        "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if source == DATAHUBCO_SOURCE:
        report["report_period"] = provider.report_period.isoformat()
        report["valuation_trade_date"] = provider.valuation_trade_date.isoformat()
    if not errors and len(eligible) >= 2:
        common_bars = normalized[normalized["instrument_id"].isin(eligible)]
        baseline = build_factor_rankings(common_bars)
        enriched = build_factor_rankings(common_bars, fundamentals=[by_id[s] for s in eligible])
        baseline_ids = [item.instrument_id for item in baseline]
        enriched_ids = [item.instrument_id for item in enriched]
        baseline_by_id = {item.instrument_id: item for item in baseline}
        enriched_by_id = {item.instrument_id: item for item in enriched}
        if set(baseline_ids) != set(eligible) or set(enriched_ids) != set(eligible):
            raise ValueError("ranking_cohort_mismatch")
        report["baseline"] = [item.model_dump(mode="json") for item in baseline]
        report["enriched"] = [item.model_dump(mode="json") for item in enriched]
        report["comparison"] = {
            "top_k": min(top_k, len(eligible)),
            "baseline_top": baseline_ids[:top_k],
            "enriched_top": enriched_ids[:top_k],
            "top_overlap": len(set(baseline_ids[:top_k]) & set(enriched_ids[:top_k])),
            "rank_changes": {symbol: baseline_by_id[symbol].factor_rank
                             - enriched_by_id[symbol].factor_rank for symbol in eligible},
        }
        report["status"] = "compared"
    report["result_digest"] = _digest(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-current-financial-research", action="store_true")
    parser.add_argument("--source", choices=("promax", "datahubco"), default="promax")
    parser.add_argument("--report-period", help="Datahubco report period, YYYYMMDD")
    parser.add_argument("--valuation-trade-date", help="Datahubco valuation date, YYYYMMDD")
    parser.add_argument("--bars-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round-trip-cost-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if not args.enable_current_financial_research:
        parser.error("explicit --enable-current-financial-research is required")
    if args.source == "datahubco" and (not args.report_period or not args.valuation_trade_date):
        parser.error("Datahubco requires --report-period and --valuation-trade-date")
    if args.source == "promax" and (args.report_period or args.valuation_trade_date):
        parser.error("Datahubco dates require --source datahubco")
    try:
        raw_bars = args.bars_csv.read_bytes()
        bars = pd.read_csv(args.bars_csv, dtype={"instrument_id": str, "trade_date": str,
                                                "provider": str})
        if args.source == "datahubco":
            def parse_explicit_day(value):
                if not re.fullmatch(r"\d{8}", value):
                    raise ValueError("invalid_explicit_date")
                try:
                    return datetime.strptime(value, "%Y%m%d").date()
                except ValueError:
                    raise ValueError("invalid_explicit_date") from None

            provider = build_datahubco_research_provider(
                report_period=parse_explicit_day(args.report_period),
                valuation_trade_date=parse_explicit_day(args.valuation_trade_date),
            )
        else:
            provider = build_tushare_relay_research_provider()
        report = compare_current_financial_factors(
            bars, provider,
            observation_day=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
            round_trip_cost_bps=args.round_trip_cost_bps,
            top_k=args.top_k,
        )
        report["bars_file_sha256"] = sha256(raw_bars).hexdigest()
        report["result_digest"] = _digest({key: value for key, value in report.items()
                                           if key != "result_digest"})
        publish(args.output, json.dumps(report, indent=2, allow_nan=False, default=str) + "\n")
        return 0 if report["status"] == "compared" else 1
    except (OSError, ValueError, RelayError, DatahubcoError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
