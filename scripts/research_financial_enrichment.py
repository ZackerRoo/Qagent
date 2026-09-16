#!/usr/bin/env python3
"""Current research snapshots: two stocks, six APIs in V2, twelve rows each.

Fixture schema: source (datahubco|promax), period (YYYYMMDD), trade_date
(YYYYMMDD), retrieved_at (timezone ISO), instruments: {symbol: {api:
{status: observed|no_rows|error, rows: [...], error: safe_code_if_error}}}.
analysis_version=2 enables balancesheet/fina_indicator; absent means legacy V1
with four APIs. New live collection uses V2; old fixtures retain V1 validation.
Raw table evidence is retained; no historical PIT, trading weight or ranking.
"""
import argparse
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from research_cashflow_quality import LIMIT, SAFE_ERRORS, day, digest, normalize, number
from rank_g2_consensus import publish

APIS = ("cashflow", "income", "daily_basic", "forecast")
APIS_V2 = APIS + ("balancesheet", "fina_indicator")
SYMBOL = re.compile(r"(?:[03]\d{5}\.SZ|6\d{5}\.SH|(?:[48]\d{5}|92\d{4})\.BJ)\Z")


def request_params(api, symbol, period, trade_date):
    params = {"ts_code": symbol, "limit": LIMIT}
    if api in ("cashflow", "income", "balancesheet"):
        params.update(period=period, report_type="1")
    elif api == "fina_indicator":
        params["period"] = period
    elif api == "daily_basic":
        params["trade_date"] = trade_date
    return params


def validate_header(payload):
    if payload["source"] not in ("datahubco", "promax"):
        raise ValueError("invalid_source")
    observed = datetime.fromisoformat(payload["retrieved_at"])
    if observed.tzinfo is None:
        raise ValueError("timezone_required")
    local = observed.astimezone(ZoneInfo("Asia/Shanghai"))
    today = local.date()
    period, trade = day(payload["period"]), day(payload["trade_date"])
    if period.strftime("%m%d") not in {"0331", "0630", "0930", "1231"} or max(period, trade) > today:
        raise ValueError("invalid_request_date")
    if trade == today and local.hour < 15:
        raise ValueError("current_session_not_closed")
    instruments = payload["instruments"]
    if not isinstance(instruments, dict) or not 1 <= len(instruments) <= 2:
        raise ValueError("instrument_budget")
    if any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in instruments):
        raise ValueError("invalid_symbol")
    return today


def safe_error(exc):
    kind = getattr(exc, "kind", None)
    return kind if isinstance(kind, str) and kind in SAFE_ERRORS else "section_failed"


def fetch(client, source, symbols, period, trade_date, *, observed=None, analysis_version=2):
    observed = observed or datetime.now(ZoneInfo("Asia/Shanghai"))
    if len(symbols) != len(set(symbols)):
        raise ValueError("duplicate_symbol")
    payload = {"source": source, "period": period, "trade_date": trade_date,
               "retrieved_at": observed.isoformat(), "instruments": {s: {} for s in symbols}}
    if type(analysis_version) is not int or analysis_version not in (1, 2):
        raise ValueError("unsupported_analysis_version")
    if analysis_version == 2:
        payload["analysis_version"] = 2
    validate_header(payload)
    for symbol, sections in payload["instruments"].items():
        for api in APIS_V2 if analysis_version == 2 else APIS:
            try:
                table = client.query(api, **request_params(api, symbol, period, trade_date))
                rows = list(table.rows)
                encoded = json.dumps(rows, allow_nan=False)
                key = getattr(client, "api_key", None)
                if isinstance(key, str) and key and key in encoded:
                    raise ValueError("unsafe_response")
                sections[api] = {"status": "observed" if rows else "no_rows", "rows": rows}
            except Exception as exc:
                sections[api] = {"status": "error", "rows": [], "error": safe_error(exc)}
    # Cutoff is start-of-collection for conservative same-day announcement availability.
    return payload


def numeric(value):
    parsed = number(value)
    return None if parsed is None else str(parsed)


def valuation(rows, trade_date, *, include_total_mv=False):
    fields = ("pe", "pe_ttm", "pb", "turnover_rate", "volume_ratio")
    if any(row.get("trade_date") != trade_date for row in rows):
        raise ValueError("date_mismatch")
    # Decimal equality ignores harmless source precision (20 == 20.00).
    variants = {tuple(number(row.get(k)) for k in fields) for row in rows}
    if len(variants) != 1:
        raise ValueError("ambiguous_revision")
    values = {field: numeric(rows[0].get(field)) for field in fields}
    exclusions = {}
    for field in fields:
        if values[field] is None:
            exclusions[field] = "missing_or_nonfinite"
    for field in ("turnover_rate", "volume_ratio"):
        if values[field] is not None and Decimal(values[field]) < 0:
            values[field] = None
            exclusions[field] = "negative_value"
    if include_total_mv:
        market_caps = [number(row.get("total_mv")) for row in rows]
        positive = [value for value in market_caps if value is not None and value > 0]
        if len(positive) != len(rows):
            values["total_mv"] = None
            exclusions["total_mv"] = "missing_or_nonpositive"
        elif len(set(positive)) != 1:
            values["total_mv"] = None
            exclusions["total_mv"] = "ambiguous_revision"
        else:
            values["total_mv"] = str(positive[0])
    for pe_field, result_field in (("pe", "earnings_yield"), ("pe_ttm", "earnings_yield_ttm")):
        pe = number(values[pe_field])
        values[result_field] = str(Decimal(1) / pe) if pe is not None and pe > 0 else None
        if values[result_field] is None:
            exclusions[result_field] = "missing_or_nonpositive_pe"
    return {"trade_date": trade_date, "values": values, "exclusions": exclusions}


def forecasts(rows, today):
    fields = ("p_change_min", "p_change_max", "net_profit_min", "net_profit_max")
    grouped, excluded = {}, []
    for row in rows:
        period, ann = day(row.get("end_date")), day(row.get("ann_date"))
        if period.strftime("%m%d") not in {"0331", "0630", "0930", "1231"}:
            raise ValueError("invalid_period")
        if ann > today:
            excluded.append({"period": period.isoformat(), "reason": "future_announcement"})
            continue
        if row.get("f_ann_date") and day(row["f_ann_date"]) > today:
            excluded.append({"period": period.isoformat(), "reason": "future_announcement"})
            continue
        values = tuple(numeric(row.get(key)) for key in fields)
        grouped.setdefault(period.isoformat(), []).append((row.get("type"), values, ann.isoformat()))
    accepted = []
    for period, versions in sorted(grouped.items()):
        if len({(kind, tuple(number(value) for value in values)) for kind, values, _ in versions}) != 1:
            excluded.append({"period": period, "reason": "ambiguous_revision"})
            continue
        kind, values, _ = versions[0]
        result, reasons = dict(zip(fields, values)), {}
        for low, high in (("p_change_min", "p_change_max"), ("net_profit_min", "net_profit_max")):
            left, right = number(result[low]), number(result[high])
            if left is None or right is None or left > right:
                result[low] = result[high] = None
                reasons[low + "_to_" + high] = "missing_nonfinite_or_reversed_interval"
        accepted.append({"report_period": period, "type": kind, "ranges": result,
                         "announcement_dates": sorted({ann for _, _, ann in versions}),
                         "latest_announcement_date": max(ann for _, _, ann in versions),
                         "announcement_age_days": (today - datetime.fromisoformat(max(
                             ann for _, _, ann in versions)).date()).days,
                         "exclusions": reasons})
    return {"rows": accepted, "excluded": excluded}


def extended_financial(rows, api, period, today):
    """Consume only non-quarter profitability and same-period consolidated balances.

    fina_indicator does not define comp_type/report_type in its standard schema.
    Optional returned tags must agree; matching income/balance provide the scope gate.
    """
    fields = ("total_assets", "total_liab") if api == "balancesheet" else ("roe", "netprofit_margin")
    versions, dates, excluded = [], set(), []
    for row in rows:
        if row.get("end_date") != period:
            raise ValueError("period_mismatch")
        announcements = [day(row.get("ann_date"))]
        if row.get("f_ann_date") not in (None, ""):
            announcements.append(day(row["f_ann_date"]))
        if min(announcements) < day(period) or max(announcements) > today:
            excluded.append("unavailable_at_retrieval")
            continue
        if api == "balancesheet":
            compatible = all(str(row.get(key)) == "1" for key in ("report_type", "comp_type"))
        else:
            compatible = all(row.get(key) in (None, "") or str(row[key]) == "1"
                             for key in ("report_type", "comp_type"))
        if not compatible:
            excluded.append("unsupported_report_or_company_type")
            continue
        versions.append(tuple(numeric(row.get(key)) for key in fields))
        dates.update(d.isoformat() for d in announcements)
    if not versions:
        return {"values": {}, "exclusions": {"section": "no_compatible_rows"}, "excluded_rows": excluded}
    if len({tuple(number(value) for value in values) for values in versions}) != 1:
        return {"values": {}, "exclusions": {"section": "ambiguous_consumed_revision"}, "excluded_rows": excluded}
    values, reasons = dict(zip(fields, versions[0])), {}
    for key, value in values.items():
        if value is None:
            reasons[key] = "missing_or_nonfinite"
    if api == "balancesheet":
        assets, liabilities = number(values["total_assets"]), number(values["total_liab"])
        valid = assets is not None and assets > 0 and liabilities is not None and liabilities >= 0
        values["liabilities_to_assets"] = str(liabilities / assets) if valid else None
        if not valid:
            reasons["liabilities_to_assets"] = "missing_nonfinite_or_invalid_balance"
    return {"report_period": period, "values": values, "exclusions": reasons,
            "announcement_dates": sorted(dates), "excluded_rows": excluded,
            "equivalent_consumed_revisions": len(versions),
            "scope": "consolidated_company_type_1" if api == "balancesheet" else "requires_matching_consolidated_statements",
            "period_basis": "period_end_stock" if api == "balancesheet" else "source_nonquarter_nonannualized_metrics"}


def analyze(payload):
    today = validate_header(payload)
    version = payload.get("analysis_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported_analysis_version")
    matched_control_evidence = payload.get("matched_control_evidence_version")
    if matched_control_evidence not in (None, 1):
        raise ValueError("unsupported_matched_control_evidence_version")
    report = {"protocol": f"financial-enrichment-current-v{version}", "source": payload["source"],
              "retrieved_at": payload["retrieved_at"], "period": payload["period"],
              "trade_date": payload["trade_date"], "decision_weight": False, "activation_allowed": False,
              "temporal_semantics": "current_observation_not_historical_pit",
              "status_semantics": "observed means identity-valid rows exist, not all metrics available; inspect exclusions",
              "metric_definitions": {
                  "cashflow_to_netprofit": "n_cashflow_act / positive n_income; consolidated cumulative company type 1",
                  "cashflow_to_total_revenue": "n_cashflow_act / positive total_revenue; same report period",
                  "earnings_yield": "1 / positive pe, not a return forecast",
                  "earnings_yield_ttm": "1 / positive pe_ttm, not a return forecast",
                  "turnover_rate": "source daily turnover percent", "volume_ratio": "source daily volume ratio",
                  "forecast": "issuer forecast range, not analyst consensus or earnings surprise; net_profit units ten thousand CNY",
              }, "limitations": ["Bounded pages; full coverage and source independence unverified.",
                                   "Forecast announcement age is descriptive; an old announcement is not a new catalyst.",
                                   "No weights, ranking, historical backfill or performance claim."],
              "raw_evidence": payload, "input_digest": digest(payload), "instruments": {}}
    for symbol, evidence in sorted(payload["instruments"].items()):
        sections, financial = {}, {}
        for api in APIS_V2 if version == 2 else APIS:
            section = {"request": request_params(api, symbol, payload["period"], payload["trade_date"])}
            sections[api] = section
            try:
                item = evidence[api]
                rows = item["rows"]
                if not isinstance(rows, list) or len(rows) > LIMIT:
                    raise ValueError("invalid_page")
                section["coverage"] = {"received_rows": len(rows), "page_limit_reached": len(rows) == LIMIT}
                if item["status"] == "error":
                    if rows:
                        raise ValueError("error_with_rows")
                    section.update(status="error", error=item.get("error") if item.get("error") in SAFE_ERRORS else "section_failed")
                    continue
                if item["status"] not in ("observed", "no_rows") or (item["status"] == "no_rows" and rows):
                    raise ValueError("invalid_status")
                if not rows:
                    section["status"] = "no_rows"
                    continue
                if any(not isinstance(r, dict) or r.get("ts_code") != symbol for r in rows):
                    raise ValueError("symbol_mismatch")
                if api in ("cashflow", "income"):
                    if any(r.get("end_date") != payload["period"] for r in rows):
                        raise ValueError("period_mismatch")
                    normalized, coverage = normalize(rows, symbol, api, today)
                    financial[api] = normalized.get(day(payload["period"]).isoformat())
                    section.update(coverage=coverage, status="observed" if financial[api] else "no_usable_rows")
                    if financial[api]:
                        section["derived"] = {
                            "report_period": payload["period"],
                            "announcement_dates": financial[api]["announcement_dates"],
                            "values": {k: None if v is None else str(v) for k, v in financial[api]["values"].items()},
                        }
                elif api == "daily_basic":
                    section.update(status="observed", derived=valuation(
                        rows, payload["trade_date"], include_total_mv=matched_control_evidence == 1))
                elif api in ("balancesheet", "fina_indicator"):
                    derived = extended_financial(rows, api, payload["period"], today)
                    section.update(status="observed" if derived["values"] else "no_usable_rows", derived=derived)
                else:
                    derived = forecasts(rows, today)
                    section.update(status="observed" if derived["rows"] else "no_usable_rows", derived=derived)
            except Exception:
                section.update(status="invalid_data", error="validation_failed")
        ratios = {}
        cash, inc = financial.get("cashflow"), financial.get("income")
        for denominator, name in (("n_income", "cashflow_to_netprofit"), ("total_revenue", "cashflow_to_total_revenue")):
            numerator = cash["values"]["n_cashflow_act"] if cash else None
            denom = inc["values"][denominator] if inc else None
            okay = numerator is not None and denom is not None and denom > 0
            ratios[name] = {"value": str(numerator / denom) if okay else None,
                            "reason": None if okay else "missing_or_nonpositive_denominator_or_missing_cashflow"}
        report["instruments"][symbol] = {"sections": sections, "financial_ratios": ratios}
        if version == 2:
            indicator = sections["fina_indicator"]
            if indicator["status"] == "observed":
                confirmed = financial.get("income") is not None and sections["balancesheet"]["status"] == "observed"
                indicator["derived"]["scope_confirmed_by_matching_statements"] = confirmed
                if not confirmed:
                    indicator["status"] = "no_usable_rows"
                    indicator["derived"]["exclusions"]["scope"] = "matching_consolidated_statements_required"
    if version == 2:
        report["research_only"] = True
        report["metric_definitions"].update({
            "roe": "fina_indicator.roe source percent, not q_roe/roe_yearly; same report period",
            "netprofit_margin": "fina_indicator.netprofit_margin source sales net profit percent, not quarterly",
            "liabilities_to_assets": "balancesheet.total_liab / positive total_assets; nonnegative liabilities, dimensionless",
        })
        report["limitations"].append("Indicator schema lacks scope tags; same-period type-1 income/balance corroborate scope, not cross-source authenticity.")
    if matched_control_evidence == 1:
        report["metric_definitions"]["total_mv"] = (
            "positive daily_basic.total_mv source value; provider source unit retained without conversion; "
            "current observation, not historical PIT"
        )
    report["status"] = "observed" if all(s["status"] == "observed" for i in report["instruments"].values()
                                          for s in i["sections"].values()) else "incomplete"
    root = Path(__file__).resolve().parents[1]
    report["implementation_sha256"] = {name: sha256((root / name).read_bytes()).hexdigest() for name in (
        "scripts/research_financial_enrichment.py", "scripts/research_cashflow_quality.py",
        "backend/qagent/providers/datahubco.py", "backend/qagent/providers/tushare_relay.py")}
    report["result_digest"] = digest(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", type=Path)
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--source", required=True, choices=("datahubco", "promax"))
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--period")
    parser.add_argument("--trade-date")
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.fixture:
            payload = json.loads(args.fixture.read_text())
            if payload["source"] != args.source:
                raise ValueError("source_mismatch")
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
            if args.source == "datahubco":
                if not args.allow_insecure_http:
                    raise ValueError("http_opt_in_required")
                from qagent.providers.datahubco import DatahubcoClient
                client = DatahubcoClient(os.environ.get("QAGENT_DATAHUBCO_KEY", ""), allow_insecure_http=True)
            else:
                from qagent.providers.tushare_relay import TushareRelayClient
                client = TushareRelayClient(os.environ.get("QAGENT_TUSHARE_RELAY_KEY", ""), retries=0)
            payload = fetch(client, args.source, args.symbol, args.period, args.trade_date)
        report = analyze(payload)
        report["input_mode"] = "offline_fixture" if args.fixture else "live"
        report.pop("result_digest")
        report["result_digest"] = digest(report)
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            publish(args.output, encoded)
        else:
            print(encoded, end="")
        return 0 if report["status"] == "observed" else 1
    except Exception:
        print(json.dumps({"status": "error", "error": "financial_enrichment_failed",
                          "decision_weight": False, "activation_allowed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
