import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SPEC = importlib.util.spec_from_file_location(
    "repair_diagnostics",
    Path(__file__).resolve().parents[2] / "scripts/diagnose_exact_price_repair.py",
)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def stage(hour, trace="", **metrics):
    return {
        "stage_key": "factor_shadow", "updated_at": f"2026-09-08 {hour:02}:00:00",
        "exact_price": {"data_health": {
            "factor_shadow_exact_price_batch_trace": trace,
            **{"factor_shadow_exact_price_" + key: value for key, value in metrics.items()},
        }},
    }


def test_candidate_sums_are_not_unique_gaps_or_provider_repair_rates():
    report = diagnostic.summarize([
        stage(12, "def:0/2:2026-09-02:CN:000001,CN:000003", repaired="4",
              requested="100", aggregation="sum_per_candidate_resolution"),
        stage(10, "abc:0/3:2026-09-02:CN:000001,CN:000002", repaired="90"),
    ])["factor_shadow"]
    assert report["unique_pending_requirements"] is None
    assert report["trace_unique_instrument_dates_lower_bound"] == 3
    assert report["trace_distinct_scope_prefixes"] == 2
    assert report["reported_repaired_fields_per_elapsed_hour"] == 2
    newest = report["observations"][-1]
    assert newest["requested"] == 100
    assert newest["aggregation"] == "sum_per_candidate_resolution"
    assert newest["trace_repeated_instrument_dates_from_earlier_observations"] == 1


def test_truncated_and_invalid_trace_is_only_a_lower_bound():
    report = diagnostic.summarize([stage(
        10, "abc:0/4:2026-09-02:CN:000001,CN:00 | bad | "
            "abc:1/4:2026-09-02:CN:000001 | abc:4/4:2026-09-02:CN:000002",
        provider_requested="40", provider_batches="8",
    )])["factor_shadow"]
    assert report["trace_unique_instrument_dates_lower_bound"] == 1
    assert report["observations"][0]["trace_entries"] == 2
    assert report["observations"][0]["provider_requested"] == 40
    assert report["reported_repaired_fields_per_elapsed_hour"] is None


def test_missing_counters_and_no_row_structural_budget_reasons_are_not_recovery():
    report = diagnostic.summarize([
        stage(10, repaired="0", budget_exhausted_reason="none",
              reason_mix="confirmed_suspended=4,provider_no_row=15"),
        stage(11, repaired="0", deferred_by_budget="20",
              budget_exhausted_reason="wall_clock_deadline"),
        stage(12, repaired="invalid"),
    ])["factor_shadow"]
    assert report["reported_repaired_fields_per_elapsed_hour"] is None
    assert report["observations"][0]["requested"] is None
    assert report["observations"][0]["reason_mix"] == "confirmed_suspended=4,provider_no_row=15"
    assert report["budget_reason_counts"] == {
        "none": 1, "wall_clock_deadline": 1, "not_recorded": 1,
    }
    assert diagnostic.summarize([]) == {}


def test_database_diagnostic_is_bounded_and_read_only(tmp_path, monkeypatch):
    path = tmp_path / "qagent.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE automation_cycle_stages (cycle_slot, stage_key, status, "
                     "attempt_count, updated_at, output_json)")
        for hour in (10, 11):
            item = stage(hour, repaired="0")
            conn.execute("INSERT INTO automation_cycle_stages VALUES (?, ?, ?, ?, ?, ?)",
                         (str(hour), "factor_shadow", "deferred", 0, item["updated_at"],
                          json.dumps(item["exact_price"])))
    original_connect = sqlite3.connect
    opened = []

    class ReadOnlyConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            opened.append(sql)
            if sql.startswith("SELECT"):
                assert super().execute("PRAGMA query_only").fetchone() == (1,)
                with pytest.raises(sqlite3.OperationalError):
                    super().execute("DELETE FROM automation_cycle_stages")
            return super().execute(sql, *args)

    def connect(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro") and kwargs["uri"] is True
        return original_connect(database_uri, factory=ReadOnlyConnection, **kwargs)

    monkeypatch.setattr(diagnostic.sqlite3, "connect", connect)
    report = diagnostic.diagnose(path, limit=1)
    assert report["read_only"] is True and report["provider_calls"] == 0
    assert len(report["stages"]) == 1
    assert report["stages"][0]["cycle_slot"] == "11"
    assert "PRAGMA query_only=ON" in opened
    for limit in (0, 101):
        with pytest.raises(ValueError):
            diagnostic.diagnose(path, limit)


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        diagnostic.diagnose(path)
    assert not path.exists()
