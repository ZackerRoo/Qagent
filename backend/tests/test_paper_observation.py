import hashlib
import json
import sqlite3
from datetime import timedelta

import pytest

from qagent.execution.paper_observation import main, observe_database
from qagent.execution.paper_replay_sqlite import open_read_only_sqlite
from qagent.storage.paper import encode_paper_replay_evidence

from test_paper_replay_evidence import OCCURRED_AT, _evidence


def _database(tmp_path, notes=()):
    path = tmp_path / "paper.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE paper_trades (trade_id TEXT)")
        db.execute("CREATE TABLE market_bar_cache (provider_mode TEXT)")
        db.execute("CREATE TABLE paper_trade_events "
                   "(event_id TEXT, trade_id TEXT, occurred_at TEXT, note TEXT)")
        db.executemany("INSERT INTO paper_trade_events VALUES (?, ?, ?, ?)", [
            (f"event-{i:03}", f"trade-{i:03}", OCCURRED_AT.isoformat(), note)
            for i, note in enumerate(notes)
        ])
    return path


def _note(**kwargs):
    return encode_paper_replay_evidence("fixture", _evidence(**kwargs))


def test_observation_is_deterministic_and_database_is_read_only(tmp_path, capsys):
    path = _database(tmp_path, [_note(evidence_version="v2")])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    first = observe_database(path)
    assert first == observe_database(path)
    assert first["samples"][0]["report"]["verdict"] == "matched"
    assert first["source_high_water"]["event_id"] == "event-000"
    assert main(["--db", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    db = open_read_only_sqlite(path)
    try:
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("CREATE TABLE forbidden (id INT)")
    finally:
        db.close()


def test_empty_observation_collects_and_missing_database_is_not_created(tmp_path, capsys):
    result = observe_database(_database(tmp_path))
    assert result["sample_count"] == 0
    assert result["source_high_water"] is None
    assert result["summary"]["gate"] == "collecting"
    path = tmp_path / "missing.db"
    assert main(["--db", str(path)]) == 1
    assert not path.exists()
    assert "FileNotFoundError" in capsys.readouterr().err


def test_v1_is_separate_and_duplicate_v2_does_not_inflate_matches(tmp_path):
    v2 = _note(evidence_version="v2")
    notes = [v2, v2, _note(evidence_version="v1", expected_price="10.01")]
    result = observe_database(_database(tmp_path, notes))
    assert result["source_event_count"] == 3
    assert result["sample_count"] == 2
    assert result["summary"]["buy"]["matched"] == 1
    assert result["summary"]["legacy_v1"]["unknown"] == 1
    assert result["summary"]["unknown_count"] == 0


def test_malformed_unknown_and_explained_are_visible_and_blocked(tmp_path, capsys):
    notes = [
        "[paper_replay_evidence:v2]{bad}",
        _note(evidence_version="v2", expected_price="10.01"),
        _note(evidence_version="v2", commission="6.00"),
        '[paper_replay_evidence_status:v2]{"status":"build_failed_trade_continued"}',
    ]
    path = _database(tmp_path, notes)
    result = observe_database(path)
    assert result["summary"]["gate"] == "blocked"
    assert result["summary"]["unknown_count"] == 3
    assert result["summary"]["audit_build_failures"] == 2
    assert result["samples"][0]["issue_code"] == "replay_evidence_corrupt"
    assert result["samples"][1]["report"]["differences"]
    assert result["summary"]["buy"]["explained_difference"] == 1
    assert main(["--db", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["summary"]["gate"] == "blocked"


def test_new_source_event_changes_digest_even_if_evidence_is_duplicate(tmp_path):
    note = _note(evidence_version="v2")
    path = _database(tmp_path, [note])
    first = observe_database(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO paper_trade_events VALUES (?, ?, ?, ?)",
                   ("new", "trade-0", (OCCURRED_AT + timedelta(days=1)).isoformat(), note))
    second = observe_database(path)
    assert second["samples"] == first["samples"]
    assert second["source_digest"] != first["source_digest"]
    assert second["observation_digest"] != first["observation_digest"]
    assert second["source_high_water"]["event_id"] == "new"


def test_arbitrary_sensitive_status_detail_is_not_emitted(tmp_path, capsys):
    secret = "fixture-sensitive-upstream-token"
    note = '[paper_replay_evidence_status:v2]' + json.dumps({
        "status": "build_failed_trade_continued", "reason": secret,
    })
    path = _database(tmp_path, [note])
    assert main(["--db", str(path)]) == 2
    output = capsys.readouterr().out
    assert secret not in output
    result = json.loads(output)
    assert "issue_detail" not in result["samples"][0]
    assert result["samples"][0]["issue_code"] == (
        "replay_evidence_status:build_failed_trade_continued")


def test_conflicting_same_phase_and_missing_tables_fail_closed(tmp_path):
    conflict = _note(evidence_version="v2") + "\n" + _note(
        evidence_version="v2", commission="6.00")
    path = _database(tmp_path, [conflict])
    result = observe_database(path)
    assert result["samples"][0]["issue_code"] == "replay_evidence_conflict"
    assert result["summary"]["gate"] == "blocked"
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE market_bar_cache")
    with pytest.raises(RuntimeError, match="missing required tables"):
        observe_database(path)
