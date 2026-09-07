from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pandas as pd
import pytest

from qagent.backtesting import portfolio as p
from test_matched_control import candidate, signal


def run(frame, **extra):
    requests = []
    def bars(*args, start, end):
        requests.append((start, end))
        return frame.loc[(frame.trade_date >= start) & (frame.trade_date <= end)].copy()
    kwargs = dict(signals=[signal()], instrument_ids=["US:A"],
                  provider=SimpleNamespace(name="fixture", get_daily_bars=bars),
                  start=date(2025, 1, 2), end=date(2025, 1, 7),
                  max_entry_wait_days=2)
    kwargs.update(extra)
    audit = []
    plain = p.run_signal_portfolio_backtest(**kwargs)
    observed = p.run_signal_portfolio_backtest(**kwargs, audit_sink=audit)
    assert plain == observed
    assert all(end == kwargs["end"] for _, end in requests)
    return audit


def frame():
    return pd.DataFrame([dict(instrument_id="US:A", trade_date=day,
                             open=8.0, high=8.5, low=7.0, close=8.0, volume=100000)
                         for day in (date(2025, 1, 3), date(2025, 1, 6))])


def test_candidate_reasons_and_missing_evidence():
    assert run(frame())[0]["reason"] == "not_triggered"
    assert run(frame().drop(columns="volume"))[0]["reason"] == "unknown"
    invalid = signal().model_copy(update={"trigger_price": None})
    assert run(frame(), signals=[invalid])[0]["reason"] == "invalid_plan"


def test_future_bar_cannot_complete_entry_window():
    original = frame()
    future = original.iloc[-1].copy()
    future["trade_date"] = date(2025, 2, 1)
    future["open"], future["high"] = 10, 12
    extended = pd.concat([original, future.to_frame().T], ignore_index=True)
    assert run(original, max_entry_wait_days=3) == run(extended, max_entry_wait_days=3)
    assert run(extended, max_entry_wait_days=3)[0]["reason"] == "insufficient_future_data"


@pytest.mark.parametrize("symbol,equity,risk,cash,capacity,expected", [
    ("CN:000001", "100000", "0.001", "100000", None, "risk_budget"),
    ("CN:000001", "100", "100", "100000", None, "minimum_order_quantity"),
    ("US:A", "100000", "1", "100000", "0", "executable_quantity_limit"),
    ("CN:000001", "100000", "1", "1", None, "cash_insufficient"),
])
def test_sizing_first_failure_and_raw_values(symbol, equity, risk, cash, capacity, expected):
    item = candidate(symbol)
    item.max_executable_shares = D(capacity) if capacity is not None else None
    kwargs = dict(equity=D(equity), risk_per_trade_pct=D(risk), cash=D(cash),
                  max_positions=5, transaction_cost_bps=D(5))
    detail = {}
    assert p._size_trade(item, **kwargs) == p._size_trade(item, **kwargs, audit_details=detail) is None
    assert detail["first_failure"] == expected
    assert D(detail["risk_budget"]) == D(equity) * D(risk) / 100
    assert "shares_before_cash" in detail


def test_successful_audit_preserves_trade_and_equity():
    bars = frame()
    bars.loc[0, ["open", "high", "low", "close"]] = [10, 10.5, 9.8, 10.2]
    bars.loc[1, ["open", "high", "low", "close"]] = [11, 11.2, 10.8, 11]
    assert run(bars)[0]["reason"] == "executed"


def test_optional_cash_evidence_failure_does_not_change_sizing(monkeypatch):
    monkeypatch.setattr(p, "_fit_shares_to_cash", lambda *args: D(0))
    def unavailable(*args):
        raise RuntimeError("fixture audit-only fee failure")
    monkeypatch.setattr(p, "_trade_cost_breakdown", unavailable)
    kwargs = dict(equity=D(100000), cash=D(1), risk_per_trade_pct=D(1),
                  max_positions=5, transaction_cost_bps=D(5))
    details = {}
    assert p._size_trade(candidate(), **kwargs) is None
    assert p._size_trade(candidate(), **kwargs, audit_details=details) is None
    assert details["first_failure"] == "unknown"
    assert details["detail"] == "cash_audit_unavailable:RuntimeError"
