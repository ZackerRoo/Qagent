import importlib.util
from datetime import date
from pathlib import Path
import sqlite3


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/diagnose_shadow_cached_labels.py"
spec = importlib.util.spec_from_file_location("cached_label_diagnostics", SCRIPT)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def test_quality_checks_keep_positive_but_unsafe_distinct():
    assert diagnostics.classify_price(None, "adjusted_open") == "missing"
    assert diagnostics.classify_price({"adjusted_open": 0}, "adjusted_open") == "nonpositive"
    assert diagnostics.classify_price({
        "adjusted_open": 10, "source_provider": "fuyao_realtime",
    }, "adjusted_open") == "unsafe"
    assert diagnostics.classify_price({
        "adjusted_open": 10, "source_provider": "fixture",
    }, "adjusted_open") == "ready_cached"


def test_snapshot_uses_canonical_mature_missing_labels_and_benchmark_quality():
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE factor_research_experiments (
                experiment_id TEXT, provider_mode TEXT, benchmark_id TEXT);
            CREATE TABLE factor_shadow_scores (
                experiment_id TEXT, scan_job_id TEXT, signal_date TEXT,
                instrument_id TEXT, created_at TEXT);
            CREATE TABLE factor_shadow_outcomes (
                experiment_id TEXT, scan_job_id TEXT, instrument_id TEXT,
                horizon_sessions INTEGER);
            CREATE TABLE market_bar_cache (
                provider_mode TEXT, instrument_id TEXT, trade_date TEXT,
                source_provider TEXT, adjusted_source_provider TEXT,
                adjustment_type TEXT, close REAL, adjusted_open REAL,
                adjusted_high REAL, adjusted_low REAL, adjusted_close REAL,
                adjustment_factor REAL);
            INSERT INTO factor_research_experiments VALUES ('e','fixture','index');
            INSERT INTO factor_shadow_scores VALUES ('e','first','2026-09-01','pending','1');
            INSERT INTO factor_shadow_scores VALUES ('e','first','2026-09-01','done','1');
            INSERT INTO factor_shadow_scores VALUES ('e','later','2026-09-01','duplicate','2');
            INSERT INTO factor_shadow_scores VALUES ('e','future','2026-09-10','immature','3');
            INSERT INTO factor_shadow_outcomes VALUES ('e','first','done',5);
        """)
        for instrument in ("pending", "index"):
            for day in ("2026-09-02", "2026-09-08"):
                connection.execute(
                    "INSERT INTO market_bar_cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("fixture", instrument, day, "fixture", None,
                     "qfq", 10, 10, 11, 9, 10, 1),
                )
        connection.commit()
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        report = diagnostics.diagnose(connection, experiment_id="e", as_of=date(2026, 9, 10))
        assert report["counts_with_benchmark"] == {"ready_cached": 1}
        assert len(report["batches"]) == 1
        assert report["batches"][0]["expected"] == 2
        assert report["batches"][0]["uncomputed"] == 1
