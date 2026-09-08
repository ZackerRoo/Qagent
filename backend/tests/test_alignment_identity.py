import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from qagent.recommendations import alignment_identity as identity


def test_live_identity_capture_error_does_not_fail_scan(monkeypatch):
    monkeypatch.setattr(identity, "build_alignment_identity", lambda **kwargs: 1 / 0)
    job = NS(provider="fixture", batch_size=10, include_etfs=True)
    fact = identity.capture_live_identity(job, top_cards_limit=10, governance_context=None,
                                         feedback_center=NS(model_dump=lambda **kwargs: {}))
    assert fact["missing_components"] == ["identity_capture_error"]
    assert not identity.identity_is_valid(fact)


def test_incomplete_or_legacy_identity_never_compares():
    manifest = identity.build_alignment_identity(source="live", effective_config={"limit": 10},
                                                 missing_components=["calibration"])
    assert identity.compare_identities(manifest, manifest) == [
        "prospective_identity_incomplete:calibration", "historical_identity_incomplete:calibration"]
    assert identity.compare_identities({}, {})
    serialized = json.dumps(manifest)
    assert "api_key" not in serialized and "fuyao_base_url" not in serialized


def test_forward_runner_uses_identical_execution_config_and_no_scan(monkeypatch):
    from qagent.backtesting import forward_replay
    from test_forward_alignment import capture
    fact = capture(benchmark_entry_allowed=True)
    fact[identity.IDENTITY_KEY] = identity.build_alignment_identity(
        source="live_scan_cards_order", effective_config={"limit": 10})
    source = {"start_date": "2026-09-07", "end_date": "2026-09-07", "dataset_revision": 1,
              "provider_mode": "fixture", "cohorts": [{"run_id": "r", "fact": fact}]}
    calls = []
    def execute(**kwargs):
        calls.append(kwargs)
        return NS(model_dump=lambda **kwargs: {})
    monkeypatch.setattr(forward_replay, "run_signal_portfolio_backtest", execute)
    monkeypatch.setattr(forward_replay, "ReplayMarketDataProvider", lambda *a: NS(last_errors=[]))
    monkeypatch.setattr(forward_replay, "VersionedAshareExecutionResolver", lambda *a, **kw: None)
    repo = NS(provider_mode="fixture", current_revision=lambda: 1)
    result = forward_replay.run_forward_replay(source, repo)
    assert result["arms"]["top_5"]["signal_count"] == 5
    assert result["arms"]["top_10"]["signal_count"] == 10
    assert calls[0]["signals"] == calls[1]["signals"][:5]
    for field in ("initial_capital", "risk_per_trade_pct", "max_positions", "transaction_cost_bps",
                  "slippage_bps", "max_entry_wait_days", "max_holding_days", "execution_profile"):
        assert calls[0][field] == calls[1][field]
    source["cohorts"].append(source["cohorts"][0])
    with pytest.raises(ValueError, match="duplicate"):
        forward_replay.run_forward_replay(source, repo)
    from copy import deepcopy
    earlier = deepcopy(source["cohorts"][0])
    earlier["run_id"] = "earlier"
    earlier["fact"]["decision_date"] = "2026-09-04"
    earlier["fact"]["recorded_at"] = "2026-09-04T07:00:00+00:00"
    for item in earlier["fact"]["top10"]:
        item["signal_date"] = "2026-09-04"
    manifest = earlier["fact"][identity.IDENTITY_KEY]
    manifest["decision_inputs"] = {"feedback_digest": "prior-observations"}
    manifest["manifest_digest"] = identity.digest({k: v for k, v in manifest.items() if k != "manifest_digest"})
    source["cohorts"] = [earlier, source["cohorts"][0]]
    source["start_date"] = "2026-09-04"
    assert forward_replay.run_forward_replay(source, repo)["arms"]["top_5"]["signal_count"] == 10
    manifest["effective_config"]["limit"] = 5
    manifest["manifest_digest"] = identity.digest({k: v for k, v in manifest.items() if k != "manifest_digest"})
    with pytest.raises(ValueError, match="mixed cohort"):
        forward_replay.run_forward_replay(source, repo)


def test_forward_files_enforce_read_only_database_and_new_output(tmp_path, monkeypatch):
    from qagent.backtesting import forward_replay
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    source = tmp_path / "facts.json"
    source.write_text(json.dumps({"provider_mode": "fixture"}))
    database = tmp_path / "isolated.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel (value INTEGER)")
    original = database.read_bytes()
    def replay(source, repository):
        with repository.session_factory() as session:
            assert session.execute(text("SELECT count(*) FROM sentinel")).scalar() == 0
            with pytest.raises(OperationalError, match="readonly"):
                session.execute(text("INSERT INTO sentinel VALUES (1)"))
        return {"okay": True}
    monkeypatch.setattr(forward_replay, "run_forward_replay", replay)
    output = tmp_path / "report.json"
    assert forward_replay.run_files(source, database, output)["okay"]
    assert database.read_bytes() == original
    with pytest.raises(ValueError, match="new separate"):
        forward_replay.run_files(source, database, output)
    with pytest.raises(ValueError, match="isolated"):
        forward_replay.run_files(source, tmp_path / "qagent.db", tmp_path / "other.json")


def test_real_executor_keeps_unmatured_cohort_unknown_and_reads_only(tmp_path):
    from qagent.backtesting.forward_replay import run_forward_replay
    from test_walk_forward_selection import _replay_repository
    from test_forward_alignment import capture
    from sqlalchemy import event
    repository, day = _replay_repository(tmp_path)
    fact = capture(1, benchmark_entry_allowed=True)
    fact["decision_date"] = day.isoformat()
    fact["recorded_at"] = day.isoformat() + "T07:00:00+00:00"
    fact["top10"][0].update(signal_date=day.isoformat(), instrument_id="CN:000001")
    fact[identity.IDENTITY_KEY] = identity.build_alignment_identity(
        source="live_scan_cards_order", effective_config={"limit": 10})
    source = {"start_date": day.isoformat(), "end_date": day.isoformat(),
              "dataset_revision": repository.current_revision(), "provider_mode": repository.provider_mode,
              "cohorts": [{"run_id": "fixture", "fact": fact}]}
    statements = []
    engine = repository.session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql))
    result = run_forward_replay(source, repository)
    assert result["configs_identical"]
    for arm in result["arms"].values():
        assert arm["signal_count"] == 1
        assert arm["portfolio"]["trades"] == []
        assert arm["audit"] and all(item["reason"] != "executed" for item in arm["audit"])
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
