from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
import json
import sqlite3

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from qagent.backtesting import matched_control, portfolio
from qagent.backtesting.engine import BacktestSignal


def signal(symbol="US:A", score=10):
    return BacktestSignal(snapshot_id=symbol, instrument_id=symbol, signal_date=date(2025, 1, 2),
                          primary_strategy_id=None, status="ready", rank_score=D(score),
                          trigger_price=D(10), initial_stop=D(9), target_1=D(11), outcome_status="pending")


def candidate(symbol="US:A", score=10):
    return portfolio._TradeCandidate(signal(symbol, score), date(2025, 1, 3), date(2025, 1, 7),
                                     "time_exit", D(10), D(11), D(9), 2)


def simulate(items, audit=None, **extra):
    return portfolio._simulate_portfolio(items, start=date(2025, 1, 2), initial_capital=D(100000),
        risk_per_trade_pct=D(1), transaction_cost_bps=D(5), fee_multiplier=D(1),
        audit_sink=audit, **extra)


def test_audit_preserves_simulation_and_first_constraint():
    items = [candidate(), candidate(score=9), candidate("US:B", 8)]
    before = deepcopy(items)
    audit = []
    assert simulate(items, max_positions=2) == simulate(items, audit, max_positions=2)
    assert [row["reason"] for row in audit] == ["executed", "already_held", "executed"]
    assert items == before
    limited = []
    simulate(items, limited, max_positions=1)
    assert [row["reason"] for row in limited] == ["executed", "position_limit", "position_limit"]


def test_size_zero_is_not_mislabeled_as_cash():
    item = candidate()
    item.max_executable_shares = D(0)
    audit = []
    simulate([item], audit, max_positions=10)
    assert [row["reason"] for row in audit] == ["size_zero"]


def test_explicit_cash_check_is_audited(monkeypatch):
    original = portfolio._size_trade
    def oversized(item, **kwargs):
        trade = original(item, **kwargs)
        return trade.model_copy(update={"shares": D(100000)})
    monkeypatch.setattr(portfolio, "_size_trade", oversized)
    audit = []
    simulate([candidate()], audit, max_positions=10)
    assert [row["reason"] for row in audit] == ["cash_insufficient"]


def test_missing_candidate_has_data_reason_and_default_result_unchanged():
    provider = SimpleNamespace(name="empty", get_daily_bars=lambda *args, **kwargs: pd.DataFrame())
    kwargs = dict(signals=[signal()], instrument_ids=["US:A"], provider=provider,
                  start=date(2025, 1, 2), end=date(2025, 1, 7))
    audit = []
    assert portfolio.run_signal_portfolio_backtest(**kwargs) == portfolio.run_signal_portfolio_backtest(**kwargs, audit_sink=audit)
    assert [row["reason"] for row in audit] == ["insufficient_future_data"]


def source():
    selections = [dict(instrument_id=f"US:{i}", status="ready", primary_strategy_id=None,
                       rank_score=str(10-i), trigger_price="10", initial_stop="9", target_1="11") for i in range(10)]
    snapshot = dict(decision_date="2025-01-02", historical_universe_size=10, eligible_size=10,
                    suspended_count=0, st_excluded_count=0, missing_tradability_count=0,
                    top_5=selections[:5], top_10=selections)
    return {"run_id": "saved", "payload": {"dataset_revision": 3, "provider_mode": "free",
            "start_date": "2025-01-02", "end_date": "2025-01-07", "snapshots": [snapshot]}}


def test_both_arms_share_config_and_source_is_unchanged(monkeypatch):
    original_source = source()
    before = deepcopy(original_source)
    provider = SimpleNamespace(name="empty", last_errors=[], get_daily_bars=lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(matched_control, "ReplayMarketDataProvider", lambda *a: provider)
    monkeypatch.setattr(matched_control, "VersionedAshareExecutionResolver", lambda *a, **k: None)
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return portfolio.run_signal_portfolio_backtest(**kwargs)
    monkeypatch.setattr(matched_control, "run_signal_portfolio_backtest", run)
    repository = SimpleNamespace(current_revision=lambda: 3, provider_mode="free")
    report = matched_control.run_matched_control(original_source, repository)
    assert original_source == before
    assert report["configs_identical"]
    assert [len(call["signals"]) for call in calls] == [5, 10]
    assert calls[0]["max_positions"] == calls[1]["max_positions"] == 10
    assert {key: value for key, value in calls[0].items() if key not in {"signals", "audit_sink"}} == {
        key: value for key, value in calls[1].items() if key not in {"signals", "audit_sink"}}
    assert sum(report["arms"]["top_10"]["audit_reason_counts"].values()) == 10
    limitations = " ".join(report["limitations"])
    assert "Candidate resolution distinguishes" in limitations
    assert "first_failure and raw sizing evidence" in limitations
    assert "missing execution evidence remains unknown" in limitations
    assert "Non-candidates are unknown:" not in limitations
    assert "cash_insufficient records only the explicit outlay check" not in limitations


def test_rejects_revision_mismatch_and_production_path(tmp_path):
    with pytest.raises(ValueError, match="revision"):
        matched_control.run_matched_control(source(), SimpleNamespace(current_revision=lambda: 4))
    with pytest.raises(ValueError, match="production"):
        matched_control.run_files(tmp_path / "input.json", tmp_path / "qagent.db", tmp_path / "out.json")


def test_file_runner_cannot_write_database_and_preserves_inputs(tmp_path, monkeypatch):
    database = tmp_path / "isolated-copy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("create table sentinel(value integer)")
        connection.execute("insert into sentinel values (7)")
    source_file = tmp_path / "source.json"
    source_file.write_text(json.dumps(source()))
    before = (source_file.read_bytes(), database.read_bytes())
    def inspect(_source, repository):
        with repository.session_factory() as session:
            assert session.execute(text("PRAGMA query_only")).scalar() == 1
            with pytest.raises(OperationalError, match="readonly"):
                session.execute(text("insert into sentinel values (8)"))
        return {}
    monkeypatch.setattr(matched_control, "run_matched_control", inspect)
    output = tmp_path / "output.json"
    matched_control.run_files(source_file, database, output)
    assert (source_file.read_bytes(), database.read_bytes()) == before
    assert json.loads(output.read_text())["database_access"].startswith("sqlite mode=ro")
