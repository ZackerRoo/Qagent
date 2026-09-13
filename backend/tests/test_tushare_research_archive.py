import fcntl
import importlib.util
import json
from datetime import date
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "relay_archive", Path(__file__).resolve().parents[2] / "scripts/archive_tushare_research.py")
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)
DAY = date(2026, 9, 11)


def report():
    return {"status": "ok", "instrument_id": "CN:000001", "requested_date": str(DAY),
            "research_only": True, "sections": {
                "daily": {"status": "ok", "rows": [{"close": "10.01"}]},
                "fundamentals": {"status": "ok", "as_of_date": "2026-09-13"},
                "minutes": {"status": "ok", "sample": {
                    "latest_time": "2026-09-11T15:00:00+08:00",
                    "realtime_freshness": "not_current_session"}}}}


def test_success_atomic_metadata(monkeypatch, tmp_path):
    mock = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(report()), ""))
    monkeypatch.setattr(wrapper.subprocess, "run", mock)
    code, path = wrapper.archive(tmp_path, ["CN:000001"], DAY)
    saved = json.loads(path.read_text())
    assert code == 0
    assert saved["runs"][0]["report"] == report()
    assert mock.call_args.kwargs["timeout"] == 180
    assert "--fundamentals" in mock.call_args.args[0]
    assert not list(tmp_path.glob(".pending-*"))
    assert path.stat().st_mode & 0o777 == 0o400


def test_partial_failure_preserved_and_secret_redacted(monkeypatch, tmp_path):
    payload = report()
    payload["status"] = "incomplete"
    payload["sections"]["daily"] = {"status": "error", "warnings": ["http_error"]}
    payload["warning"] = "private-test-key https://secret.example/?token=other"
    monkeypatch.setenv("QAGENT_TUSHARE_RELAY_KEY", "private-test-key")
    monkeypatch.setattr(wrapper.subprocess, "run", Mock(return_value=
                        subprocess.CompletedProcess([], 1, json.dumps(payload), "private-test-key")))
    code, path = wrapper.archive(tmp_path, ["CN:000001"], DAY)
    saved = json.loads(path.read_text())
    assert code == 1 and saved["status"] == "incomplete"
    assert saved["runs"][0]["report"]["sections"]["minutes"] == report()["sections"]["minutes"]
    assert "private-test-key" not in path.read_text()
    assert "secret.example" not in path.read_text()


def test_timeout_archived_and_next_symbol_continues(monkeypatch, tmp_path):
    mock = Mock(side_effect=[subprocess.TimeoutExpired(["private-key"], 1,
                            output="private-key", stderr="private-key"),
                            subprocess.CompletedProcess([], 1, '{"status":"blocked"}', "")])
    monkeypatch.setattr(wrapper.subprocess, "run", mock)
    code, path = wrapper.archive(tmp_path, ["CN:000001", "CN:600519"], DAY, timeout=1)
    saved = json.loads(path.read_text())
    assert code == 1 and len(saved["runs"]) == 2
    assert saved["runs"][0]["error"] == "collector_timeout"
    assert "private-key" not in path.read_text()


def test_lock_prevents_child(monkeypatch, tmp_path):
    mock = Mock()
    monkeypatch.setattr(wrapper.subprocess, "run", mock)
    with (tmp_path / ".research.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert wrapper.archive(tmp_path, ["CN:000001"], DAY) == (75, None)
    mock.assert_not_called()
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("stdout", ["private-key", "[]", '{"status":"ok"}',
                                   '{"value":NaN}'])
def test_invalid_or_false_success_report_fails(monkeypatch, tmp_path, stdout):
    monkeypatch.setattr(wrapper.subprocess, "run", Mock(return_value=
                        subprocess.CompletedProcess([], 0, stdout, "private-key")))
    code, path = wrapper.archive(tmp_path, ["CN:000001"], DAY)
    assert code == 1 and "private-key" not in path.read_text()


@pytest.mark.parametrize("kwargs", [{"limit": 6}, {"limit": 0}, {"timeout": float("nan")},
                                   {"timeout": 241}, {"minute_api": "order"}])
def test_invalid_bounds(tmp_path, kwargs):
    with pytest.raises(ValueError):
        wrapper.archive(tmp_path, ["CN:000001"], DAY, **kwargs)


def test_cli_failure_never_prints_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(wrapper.subprocess, "run", Mock(side_effect=OSError("private-key")))
    assert wrapper.main(["--output-dir", str(tmp_path), "--symbols", "CN:000001"]) == 1
    output = capsys.readouterr()
    assert '"status": "incomplete"' in output.out
    assert "private-key" not in output.out + output.err
