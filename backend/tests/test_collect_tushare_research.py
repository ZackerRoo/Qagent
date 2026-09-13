import importlib.util
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from qagent.providers.tushare_relay import RelayError, RelayTable
from qagent.providers.tushare_relay_research import RelayFundamentalSnapshot
from qagent.providers import tushare_relay_minutes

SPEC = importlib.util.spec_from_file_location(
    "collect_relay", Path(__file__).resolve().parents[2] / "scripts/collect_tushare_research.py",
)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
DAY = date(2026, 9, 13)
NOW = datetime(2026, 9, 13, 16, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def provider():
    result = Mock(last_errors=[])
    result.get_research_daily_bars.return_value = RelayTable(
        "research_daily_adjusted", ("trade_date", "raw_close"),
        ({"trade_date": DAY, "raw_close": Decimal("10.2300")},), 500,
    )
    result.get_fundamentals.return_value = [RelayFundamentalSnapshot(
        instrument_id="CN:000001", as_of_date=DAY, provider="relay", pe_ratio=Decimal("5.1"),
    )]
    return result


def test_one_day_explicit_anchor_and_precision(provider):
    report = collector.collect(provider, "CN:000001", DAY, now=NOW)
    provider.get_research_daily_bars.assert_called_once_with(
        "CN:000001", DAY, DAY, adjustment_anchor=DAY,
    )
    provider.get_fundamentals.assert_not_called()
    serialized = json.loads(json.dumps(report, default=collector._json_value))
    assert serialized["sections"]["daily"]["rows"][0] == {
        "trade_date": "2026-09-13", "raw_close": "10.2300",
    }
    assert report["status"] == "ok"


def test_historical_price_date_never_backdates_current_fundamentals(provider):
    report = collector.collect(provider, "CN:000001", date(2026, 9, 11),
                               fundamentals=True, now=NOW)
    provider.get_fundamentals.assert_called_once_with(
        ["CN:000001"], date(2026, 9, 3), DAY,
    )
    assert report["requested_date"] == "2026-09-11"
    assert report["sections"]["fundamentals"]["as_of_date"] == "2026-09-13"
    assert report["sections"]["fundamentals"]["rows"][0]["as_of_date"] == DAY
    assert "current_observation_not_historical_pit" in report["sections"]["fundamentals"]["warnings"]


def test_daily_failure_preserves_current_fundamentals(provider):
    provider.get_research_daily_bars.side_effect = RelayError("missing_anchor")
    report = collector.collect(provider, "CN:000001", DAY, fundamentals=True, now=NOW)
    assert report["sections"]["daily"]["warnings"] == ["missing_anchor"]
    assert report["sections"]["fundamentals"]["status"] == "ok"
    assert report["sections"]["fundamentals"]["as_of_date"] == DAY.isoformat()


def test_fundamental_failure_preserves_daily_and_redacts_exception(provider):
    provider.get_fundamentals.side_effect = RuntimeError("secret-key url?key=secret-key")
    report = collector.collect(provider, "CN:000001", DAY, fundamentals=True, now=NOW)
    assert report["sections"]["daily"]["status"] == "ok"
    assert report["sections"]["fundamentals"]["warnings"] == ["section_failed"]
    assert "secret-key" not in json.dumps(report, default=collector._json_value)


def test_partial_financial_warning_survives(provider):
    provider.last_errors = ["tushare_relay:financial_no_data"]
    report = collector.collect(provider, "CN:000001", DAY, fundamentals=True, now=NOW)
    assert report["sections"]["fundamentals"]["status"] == "partial"


@pytest.mark.parametrize("symbol,day", [("US:AAPL", DAY), ("CN:000001", date(2026, 9, 14))])
def test_invalid_request_makes_no_calls(provider, symbol, day):
    with pytest.raises(ValueError):
        collector.collect(provider, symbol, day, now=NOW)
    provider.get_research_daily_bars.assert_not_called()


def test_cli_env_only_opt_in_without_settings_write(monkeypatch, capsys, provider):
    monkeypatch.setenv("QAGENT_TUSHARE_RELAY_KEY", "private-test-key")
    factory = Mock(return_value=provider)
    monkeypatch.setattr(collector, "build_tushare_relay_research_provider", factory)
    assert collector.main(["--symbol", "CN:000001", "--date", "2026-09-11"]) == 0
    settings = factory.call_args.args[0]
    assert settings.tushare_relay_research_enabled is True
    assert settings.tushare_relay_key.get_secret_value() == "private-test-key"
    output = capsys.readouterr()
    assert "private-test-key" not in output.out + output.err


def test_configuration_failure_has_no_traceback(monkeypatch, capsys):
    monkeypatch.setattr(collector, "Settings", Mock(side_effect=ValueError("private-test-key")))
    assert collector.main(["--symbol", "CN:000001", "--date", "2026-09-11"]) == 1
    output = capsys.readouterr()
    assert "private-test-key" not in output.out + output.err
    assert json.loads(output.out)["status"] == "blocked"


def test_minute_failure_keeps_daily_without_fallback(monkeypatch, provider):
    fetch = Mock(side_effect=RelayError("pending", status_code=202))
    monkeypatch.setattr(tushare_relay_minutes, "fetch_research_minutes", fetch)
    report = collector.collect(provider, "CN:000001", DAY, now=NOW,
                               minute_api="stk_mins", session="morning")
    assert report["sections"]["daily"]["status"] == "ok"
    assert report["sections"]["minutes"]["warnings"] == ["pending"]
    assert report["sections"]["minutes"]["http_status"] == 202
    fetch.assert_called_once_with(
        provider.client, ts_code="000001.SZ", requested_date="20260913",
        api="stk_mins", limit=300, start_time="09:30:00", end_time="11:30:00",
    )


def test_minute_sample_serializes_provenance_and_limits(monkeypatch, provider):
    bar = tushare_relay_minutes.ResearchMinuteBar(
        "000001.SZ", NOW, 10.0, 11.0, 9.0, 10.5, 100.0, "vol",
    )
    sample = tushare_relay_minutes.ResearchMinuteSample(
        "rt_min", "000001.SZ", "1min", DAY, NOW, NOW, (bar,), 300, False,
        "unverified", "recent_endpoint_date_validates_response_only",
    )
    monkeypatch.setattr(tushare_relay_minutes, "fetch_research_minutes", Mock(return_value=sample))
    report = collector.collect(provider, "CN:000001", DAY, now=NOW, minute_api="rt_min")
    payload = json.loads(json.dumps(report, default=collector._json_value))
    actual = payload["sections"]["minutes"]["sample"]
    assert actual["requested_date"] == "2026-09-13"
    assert actual["rows"][0]["trade_time"] == NOW.isoformat()
    assert actual["frequency_verified"] is False
    assert actual["volume_unit"] == "upstream_unspecified"


def test_structured_http_status_preserved_without_parsing_remote_text(provider):
    provider.get_research_daily_bars.side_effect = RelayError("http_error", status_code=401)
    provider.get_fundamentals.side_effect = RelayError("http_error", status_code=503)
    report = collector.collect(provider, "CN:000001", DAY, now=NOW, fundamentals=True)
    assert report["sections"]["daily"]["http_status"] == 401
    assert report["sections"]["fundamentals"]["http_status"] == 503
    assert "http_status" not in collector._section_error(RuntimeError("HTTP 401 private-key"))
    assert "http_status" not in collector._section_error(RelayError("transport_error"))
    assert "http_status" not in collector._section_error(RelayError("http_error", status_code="private-key"))
