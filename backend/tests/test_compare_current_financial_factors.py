from datetime import datetime, timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from qagent.providers.tushare_relay import RelayError, RelayTable
from qagent.providers.tushare_relay_research import (
    DatahubcoStrategyDataProvider,
    TushareRelayStrategyDataProvider,
)


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
    assert report["source"] == "tushare_relay_promax"
    assert "report_period" not in report and "valuation_trade_date" not in report
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


class FakeDatahubcoClient:
    def __init__(self, *, edit=None, full_page=False, wrong_source=False):
        self.edit = edit
        self.full_page = full_page
        self.wrong_source = wrong_source
        self.calls = []

    def query(self, api: str, **params):
        self.calls.append((api, params))
        symbol = params["ts_code"]
        if api == "daily_basic":
            rows = [{"ts_code": symbol, "trade_date": params["trade_date"],
                     "pe_ttm": 12, "total_mv": 1000000, "ps_ttm": 2}]
        else:
            rows = [{"ts_code": symbol, "ann_date": (TODAY - timedelta(days=5)).strftime("%Y%m%d"),
                     "end_date": params["period"], "roe": 10, "tr_yoy": 14,
                     "netprofit_yoy": 21, "grossprofit_margin": 30,
                     "netprofit_margin": 12}]
        if self.edit:
            self.edit(api, rows, symbol)
        limit = len(rows) if self.full_page else params["limit"]
        return RelayTable(api, tuple(rows[0]) if rows else (), tuple(rows), limit,
                          source="unknown" if self.wrong_source else "datahubco")


def run_datahubco(client=None):
    client = client or FakeDatahubcoClient()
    period = TODAY - timedelta(days=90)
    provider = DatahubcoStrategyDataProvider(
        client, report_period=period, valuation_trade_date=TODAY - timedelta(days=1)
    )
    report = comparison.compare_current_financial_factors(
        bars(), provider, observation_day=TODAY, top_k=2,
    )
    return report, client


def test_datahubco_same_cohort_explicit_dates_and_provenance():
    report, client = run_datahubco()
    assert report["status"] == "compared"
    assert report["source"] == "datahubco"
    assert report["report_period"] == (TODAY - timedelta(days=90)).isoformat()
    assert report["valuation_trade_date"] == (TODAY - timedelta(days=1)).isoformat()
    assert report["cohort"]["eligible"] == sorted(SYMBOLS)
    assert {row["instrument_id"] for row in report["baseline"]} == set(SYMBOLS)
    assert {row["instrument_id"] for row in report["enriched"]} == set(SYMBOLS)
    assert len(client.calls) == 6
    assert all("period" in params for api, params in client.calls if api == "fina_indicator")
    assert all(item["source"] == "datahubco" and item["rows_digest"]
               for item in report["source_queries"])


@pytest.mark.parametrize("edit", [
    lambda api, rows, symbol: rows[0].update(ts_code="999999.SZ") if api == "daily_basic" else None,
    lambda api, rows, symbol: rows[0].update(trade_date="20200101") if api == "daily_basic" else None,
    lambda api, rows, symbol: rows[0].update(end_date="20200101") if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(ann_date="20990101") if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(ann_date=TODAY.strftime("%Y%m%d"))
    if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(f_ann_date=TODAY.strftime("%Y%m%d"))
    if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(f_ann_date="20200101")
    if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(f_ann_date="not-a-date")
    if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(roe="NaN") if api == "fina_indicator" else None,
    lambda api, rows, symbol: rows[0].update(total_mv="1e999") if api == "daily_basic" else None,
    lambda api, rows, symbol: rows.append({**rows[0], "roe": 11}) if api == "fina_indicator" else None,
])
def test_datahubco_malicious_rows_block_comparison(edit):
    report, _ = run_datahubco(FakeDatahubcoClient(edit=edit))
    assert report["status"] == "blocked"
    assert report["baseline"] is None and report["enriched"] is None
    assert report["provider_errors"]


def test_datahubco_empty_or_page_full_fails_closed():
    def empty_financial(api, rows, symbol):
        if api == "fina_indicator" and symbol == "300750.SZ":
            rows.clear()

    for client in (FakeDatahubcoClient(edit=empty_financial),
                   FakeDatahubcoClient(full_page=True),
                   FakeDatahubcoClient(wrong_source=True)):
        report, _ = run_datahubco(client)
        assert report["status"] == "blocked"
        assert report["baseline"] is None
        assert report["provider_errors"]


def test_datahubco_snapshot_uses_later_actual_announcement_within_valuation_cutoff():
    actual = TODAY - timedelta(days=2)

    def later_actual(api, rows, symbol):
        if api == "fina_indicator":
            rows[0]["f_ann_date"] = actual.strftime("%Y%m%d")

    report, _ = run_datahubco(FakeDatahubcoClient(edit=later_actual))
    assert report["status"] == "compared"
    assert all(item["financial_announcement_date"] == actual.isoformat()
               for item in report["fundamentals"].values())


def test_datahubco_valuation_day_must_match_bars_before_query():
    client = FakeDatahubcoClient()
    provider = DatahubcoStrategyDataProvider(
        client, report_period=TODAY - timedelta(days=90), valuation_trade_date=TODAY
    )
    with pytest.raises(ValueError, match="valuation_bar_date_mismatch"):
        comparison.compare_current_financial_factors(bars(), provider, observation_day=TODAY)
    assert not client.calls


def test_datahubco_cli_requires_explicit_period_and_valuation_day(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["compare_current_financial_factors.py",
                                     "--enable-current-financial-research",
                                     "--source", "datahubco",
                                     "--bars-csv", str(tmp_path / "missing.csv"),
                                     "--output", str(tmp_path / "report.json")])
    with pytest.raises(SystemExit) as result:
        comparison.main()
    assert result.value.code == 2
    assert not (tmp_path / "report.json").exists()
