#!/usr/bin/env python3
"""Collect one symbol/day as JSON, outside production and all persisted ledgers.

QAGENT_TUSHARE_RELAY_KEY must be present in the environment. Decimal values are
JSON strings so consumers can retain precision. Fundamentals are current only.
"""

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from qagent.config import Settings  # noqa: E402
from qagent.providers.tushare_relay import RelayError  # noqa: E402
from qagent.providers.tushare_relay_research import (  # noqa: E402
    build_tushare_relay_research_provider,
    _symbol,
)

SOURCE = "tushare_relay_promax"
SAFE_ERRORS = frozenset({
    "missing_config", "invalid_config", "research_disabled", "transport_error",
    "data_source_unavailable", "retry_deferred", "pending", "http_error",
    "upstream_pool_exhausted", "invalid_json", "upstream_error", "catalogue_schema",
    "forbidden_api", "invalid_params", "numeric_schema", "date_schema",
    "symbol_mismatch", "date_mismatch", "duplicate_row", "financial_no_data",
    "ambiguous_financial_revision", "unused_field_revision_difference",
    "identity_mismatch", "factor_schema", "missing_anchor", "missing_factor",
    "price_schema", "missing_anchor_price", "unsupported_instrument",
    "minute_invalid_number", "minute_invalid_time", "minute_future_session",
    "minute_symbol_mismatch", "minute_date_mismatch", "minute_future_bar",
    "minute_outside_window", "minute_duplicate_time", "minute_invalid_ohlc",
    "minute_ambiguous_volume",
})


def _warning(value: object) -> str:
    """Never emit arbitrary exceptions, remote messages, settings or credentials."""
    if isinstance(value, RelayError):
        kind = value.kind
    elif isinstance(value, str):
        kind = value.removeprefix("tushare_relay:").split(" (HTTP ", 1)[0]
    else:
        return "section_failed"
    return kind if kind in SAFE_ERRORS else "section_failed"


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("unsupported_report_value")


def _section_error(exc: Exception) -> dict:
    result = {"status": "error", "warnings": [_warning(exc)], "rows": []}
    if (isinstance(exc, RelayError) and type(exc.status_code) is int
            and 100 <= exc.status_code <= 599):
        result["http_status"] = exc.status_code
    return result


def collect(provider, symbol: str, requested_date: date, *, fundamentals: bool = False,
            minute_api: str | None = None, session: str = "full",
            now: datetime | None = None) -> dict:
    observed = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    today = observed.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if not re.fullmatch(r"CN:(?:[03648]\d{5}|92\d{4})", symbol):
        raise ValueError("unsupported_instrument")
    if requested_date > today:
        raise ValueError("future_date_not_supported")
    report = {
        "schema_version": 1, "source": SOURCE, "instrument_id": symbol,
        "requested_date": requested_date.isoformat(), "observed_at": observed.isoformat(),
        "research_only": True,
        "warnings": ["not_a_trusted_execution_price", "coverage_not_established"],
        "sections": {},
    }
    sections = report["sections"]
    try:
        table = provider.get_research_daily_bars(
            symbol, requested_date, requested_date, adjustment_anchor=requested_date,
        )
        sections["daily"] = {
            "status": "ok" if table.rows else "no_rows", "warnings": [],
            "adjustment_anchor": requested_date.isoformat(), "rows": list(table.rows),
        }
    except Exception as exc:
        sections["daily"] = _section_error(exc)
    sections["fundamentals"] = {"status": "not_requested", "warnings": [], "rows": []}
    if fundamentals:
        try:
            snapshots = provider.get_fundamentals([symbol], today - timedelta(days=10), today)
            warnings = [_warning(warning) for warning in provider.last_errors]
            sections["fundamentals"] = {
                "status": ("partial" if warnings else "ok") if snapshots else "no_rows",
                "warnings": ["current_observation_not_historical_pit", *warnings],
                "as_of_date": today.isoformat(),
                "rows": [snapshot.model_dump(mode="python") for snapshot in snapshots],
            }
        except Exception as exc:
            sections["fundamentals"] = _section_error(exc)
    if minute_api:
        try:
            from qagent.providers.tushare_relay_minutes import fetch_research_minutes
            start, end = {"full": ("09:30:00", "15:00:00"),
                          "morning": ("09:30:00", "11:30:00"),
                          "afternoon": ("13:00:00", "15:00:00")}[session]
            sample = fetch_research_minutes(
                provider.client, ts_code=_symbol(symbol),
                requested_date=requested_date.strftime("%Y%m%d"), api=minute_api,
                limit=300, start_time=start, end_time=end,
            )
            sections["minutes"] = {
                "status": "ok" if sample.rows else "no_rows",
                "warnings": ["realtime_freshness_not_established", "coverage_not_established"],
                "session": session, "sample": asdict(sample),
            }
        except Exception as exc:
            sections["minutes"] = _section_error(exc)
    active = [section for section in sections.values() if section["status"] != "not_requested"]
    report["status"] = "ok" if all(s["status"] == "ok" for s in active) else "incomplete"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="One A-share instrument, e.g. CN:000001")
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--fundamentals", action="store_true",
                        help="Today's snapshot independent of --date; never historical PIT")
    parser.add_argument("--minute-api", choices=("rt_min", "stk_mins", "a_share_mins"))
    parser.add_argument("--session", choices=("full", "morning", "afternoon"), default="full")
    args = parser.parse_args(argv)
    try:
        # Explicit in-process opt-in; no .env file, settings write or factory rewiring.
        settings = Settings(_env_file=None, tushare_relay_research_enabled=True)
        provider = build_tushare_relay_research_provider(settings)
        report = collect(provider, args.symbol, args.date, fundamentals=args.fundamentals,
                         minute_api=args.minute_api, session=args.session)
    except Exception as exc:
        report = {"schema_version": 1, "source": SOURCE, "status": "blocked",
                  "warnings": [_warning(exc)]}
    print(json.dumps(report, default=_json_value, ensure_ascii=False, allow_nan=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
