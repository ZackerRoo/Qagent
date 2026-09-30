from datetime import datetime, timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from qagent.providers.tushare_relay import RelayError, RelayTable
from qagent.providers.tushare_relay_research import TushareRelayStrategyDataProvider


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "compare_current_financial_factors", SCRIPTS / "compare_current_financial_factors.py"
)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)
sys.path.pop(0)

TODAY = datetime.now(ZoneInfo("Asia/Shanghai")).date()
SYMBOLS = ("CN:000001", "CN:300750", "CN:600519")


class FakeClient:
    def __init__(self, *, missing_pe: str | None = None, wrong_source: bool = False,
                 fail_symbol: str | None = None):
        self.missing_pe = missing_pe
        self.wrong_source = wrong_source
        self.fail_symbol = fail_symbol

    def query(self, api: str, **params):
        symbol = params["ts_code"]
        if self.fail_symbol == symbol:
            raise RelayError("transport_error")
        if api == "daily_basic":
            rows = ({"ts_code": symbol, "trade_date": (TODAY - timedelta(days=1)).strftime("%Y%m%d"),
                     "pe_ttm": None if symbol == self.missing_pe else {"000001.SZ": 12,
                         "300750.SZ": 28, "600519.SH": 18}[symbol],
                     "total_mv": 1000000, "ps_ttm": 2},)
        else:
            rows = ({"ts_code": symbol,
                     "ann_date": (TODAY - timedelta(days=5)).strftime("%Y%m%d"),
                     "end_date": (TODAY - timedelta(days=90)).strftime("%Y%m%d"),
                     "roe": {"000001.SZ": 4, "300750.SZ": 22, "600519.SH": 26}[symbol],
                     "tr_yoy": 14, "netprofit_yoy": 21,
                     "grossprofit_margin": 30, "netprofit_margin": 12},)
        return RelayTable(api, tuple(rows[0]), rows, 20 if api == "daily_basic" else 100,
                          source="unknown" if self.wrong_source else "tushare_relay_promax")


def bars():
    rows = []
    for symbol in SYMBOLS:
        for offset in range(120):
            day = TODAY - timedelta(days=120 - offset)
            price = 10 + offset * 0.01 + (0.2 if symbol == SYMBOLS[1] else 0)
            rows.append({"instrument_id": symbol, "trade_date": day.isoformat(),
                         "open": price, "high": price * 1.01, "low": price * 0.99,
                         "close": price, "volume": 1000000, "provider": "fixture_bars"})
    return pd.DataFrame(rows)


def run(frame=None, client=None):
    provider = TushareRelayStrategyDataProvider(client or FakeClient())
    report = comparison.compare_current_financial_factors(
        bars() if frame is None else frame, provider, observation_day=TODAY,
        round_trip_cost_bps=Decimal("10"), top_k=2,
    )
    return report, provider


def test_same_cohort_existing_engine_and_auditable_dates():
    report, provider = run()
    assert report["status"] == "compared"
    assert report["cohort"]["eligible"] == sorted(SYMBOLS)
    assert report["cohort"]["excluded"] == []
    assert {row["instrument_id"] for row in report["baseline"]} == set(SYMBOLS)
    assert {row["instrument_id"] for row in report["enriched"]} == set(SYMBOLS)
    assert any(row["valuation_score"] != 0.5 for row in report["enriched"])
    assert all(row["valuation_score"] == 0.5 for row in report["baseline"])
    assert report["cost_assumption"]["round_trip_bps"] == "10"
    assert len(report["source_queries"]) == 6
    assert all(item["rows_digest"] for item in report["source_queries"])
    assert all(item["financial_period"] and item["financial_announcement_date"]
               and item["valuation_date"] for item in report["fundamentals"].values())
    assert report["result_digest"] == comparison._digest(
        {key: value for key, value in report.items() if key != "result_digest"}
    )
    assert provider.client.__class__ is FakeClient
    assert not report["decision_weight"] and not report["activation_allowed"]


def test_missing_financial_field_excluded_from_both_arms():
    report, _ = run(client=FakeClient(missing_pe="300750.SZ"))
    assert report["status"] == "compared"
    assert report["cohort"]["eligible"] == ["CN:000001", "CN:600519"]
    assert report["cohort"]["excluded"] == [{"instrument_id": "CN:300750",
                                                "reasons": ["positive_pe_unavailable"]}]
    assert {row["instrument_id"] for row in report["baseline"]} == set(report["cohort"]["eligible"])
    assert {row["instrument_id"] for row in report["enriched"]} == set(report["cohort"]["eligible"])


@pytest.mark.parametrize("client", [FakeClient(wrong_source=True),
                                     FakeClient(fail_symbol="300750.SZ")])
def test_source_or_transport_failure_blocks_comparison(client):
    report, _ = run(client=client)
    assert report["status"] == "blocked"
    assert report["baseline"] is None and report["enriched"] is None
    assert report["provider_errors"]
    assert report["source_queries"]


def test_reject_historical_observation_and_missing_bar_provenance():
    provider = TushareRelayStrategyDataProvider(FakeClient())
    with pytest.raises(ValueError, match="current_observation_only"):
        comparison.compare_current_financial_factors(
            bars(), provider, observation_day=TODAY - timedelta(days=1)
        )
    frame = bars().drop(columns="provider")
    with pytest.raises(ValueError, match="missing_bar_columns"):
        comparison.compare_current_financial_factors(frame, provider, observation_day=TODAY)


def test_reject_mixed_latest_bar_dates_before_query():
    frame = bars()
    frame = frame[~((frame["instrument_id"] == SYMBOLS[0])
                    & (frame["trade_date"] == (TODAY - timedelta(days=1)).isoformat()))]
    with pytest.raises(ValueError, match="mixed_latest_bar_dates"):
        run(frame=frame)


def test_cli_is_default_off_before_file_or_provider_access(monkeypatch, tmp_path):
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["compare_current_financial_factors.py",
                                  "--bars-csv", str(tmp_path / "missing.csv"),
                                  "--output", str(output)])
    with pytest.raises(SystemExit) as result:
        comparison.main()
    assert result.value.code == 2
    assert not output.exists()
