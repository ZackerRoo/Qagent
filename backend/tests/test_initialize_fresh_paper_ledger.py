import importlib.util
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "initialize_fresh_paper_ledger.py"
SPEC = importlib.util.spec_from_file_location("initialize_fresh_paper_ledger", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def account_inputs(**changes):
    values = dict(label="A股研究模拟盘", initial_capital=Decimal("120000"),
                  allocation_per_trade_pct=Decimal("10"), max_positions=10,
                  transaction_cost_bps=Decimal("5"), slippage_bps=Decimal("5"),
                  take_profit_pct=Decimal("50"))
    values.update(changes)
    return MODULE.AccountInputs(**values)


def test_creates_one_explicit_paper_session_without_scheduler(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    result = MODULE.initialize(tmp_path, account_inputs())
    assert result["account"]["account_id"] == "default"
    assert result["account"]["session_id"].startswith("paper-session-")
    assert result["account"]["status"] == "active"
    assert Decimal(result["account"]["initial_capital"]) == Decimal("120000")
    assert result["account"]["max_positions"] == 10
    assert Decimal(result["account"]["transaction_cost_bps"]) == Decimal("5")
    assert Decimal(result["account"]["slippage_bps"]) == Decimal("5")
    assert Decimal(result["account"]["take_profit_pct"]) == Decimal("50")
    with sqlite3.connect(state / "qagent.db") as db:
        assert db.execute("select count(*) from paper_account_settings").fetchone() == (1,)
        assert db.execute("select count(*) from paper_trades").fetchone() == (0,)
        assert db.execute("select count(*) from automation_scheduler_state").fetchone() == (0,)
    assert not (state / ".single-writer-approved").exists()


def test_refuses_existing_database_without_modification(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    db = state / "qagent.db"
    db.write_bytes(b"existing ledger bytes")
    with pytest.raises(FileExistsError):
        MODULE.initialize(tmp_path, account_inputs())
    assert db.read_bytes() == b"existing ledger bytes"


def test_invalid_explicit_settings_do_not_create_database(tmp_path):
    (tmp_path / "state").mkdir()
    with pytest.raises(ValueError):
        MODULE.initialize(tmp_path, account_inputs(initial_capital=Decimal("NaN")))
    assert not (tmp_path / "state" / "qagent.db").exists()
