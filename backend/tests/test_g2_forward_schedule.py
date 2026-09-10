from datetime import datetime, timezone
import fcntl
import json

import pytest

from qagent.research import g2_forward_schedule as schedule
from qagent.research.g2_forward_source import atomic_archive, digest


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 11, 8, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "datetime", Clock)
    sources, frozen, output = [tmp_path / name for name in ("sources", "frozen", "output")]
    sources.mkdir()
    return sources, frozen, output


def source(directory, name, captured="2026-09-11T07:45:00+00:00"):
    path = directory / f"2026-09-11-{name}.json"
    path.write_text(json.dumps({"signal_date": "2026-09-11", "captured_at_utc": captured}))
    return path


def test_earliest_ready_sorted_by_capture_and_first_ready_retained(setup, monkeypatch):
    sources, frozen, output = setup
    first = source(sources, "z", "2026-09-11T07:40:00+00:00")
    second = source(sources, "b")
    source(sources, "a", "2026-09-11T07:50:00+00:00")
    called = []

    def collect(path, models, destination):
        assert models == frozen and destination == output
        called.append(path)
        status = "not_ready" if path == first else "ready"
        result = {"signal_date": "2026-09-11", "status": status}
        result["result_digest"] = digest(result)
        subdir = "diagnostics" if status == "not_ready" else "signals"
        atomic_archive(output / subdir / "2026-09-11.json", result)
        return result

    monkeypatch.setattr(schedule.forward, "collect", collect)
    assert schedule.run(*setup)[0] == 0
    assert called == [first, second]
    before = (output / "signals/2026-09-11.json").read_bytes()
    assert schedule.run(*setup)[1]["status"] == "already_collected"
    assert called == [first, second]
    assert (output / "signals/2026-09-11.json").read_bytes() == before
    assert len(list(sources.glob("*.json"))) == 3


def test_no_source_waits_without_signals(setup):
    code, report = schedule.run(*setup)
    assert code == 0 and report["status"] == "waiting_for_source"
    assert not (setup[2] / "signals").exists()
    assert len(list((setup[2] / "runs").glob("*.json"))) == 1


def test_unscheduled_does_not_call_collector(setup, monkeypatch):
    monkeypatch.setattr(schedule.forward, "trading_sessions_in_range", lambda *args: [])
    monkeypatch.setattr(schedule.forward, "collect", lambda *args: pytest.fail("not scheduled"))
    assert schedule.run(*setup)[1]["status"] == "skipped_unscheduled_day"


def test_corrupt_source_error_logged_and_retained(setup):
    path = source(setup[0], "bad")
    path.write_text("broken")
    code, report = schedule.run(*setup)
    assert code == 1 and len(report["errors"]) == 1
    assert path.read_text() == "broken"
    archived = json.loads(next((setup[2] / "runs").glob("*.json")).read_text())
    assert archived["errors"] == report["errors"]


def test_collector_exception_retained(setup, monkeypatch):
    source(setup[0], "bad")

    def fail(*args):
        raise ValueError("source digest mismatch")

    monkeypatch.setattr(schedule.forward, "collect", fail)
    code, report = schedule.run(*setup)
    assert code == 1 and "digest mismatch" in report["errors"][0]["error"]


def test_nonready_does_not_create_signal(setup, monkeypatch):
    source(setup[0], "future", "2026-09-12T08:00:00+00:00")
    monkeypatch.setattr(schedule.forward, "collect", lambda *args: {
        "status": "not_ready", "reasons": ["capture_not_after_freeze_or_future_timestamp"]})
    code, report = schedule.run(*setup)
    assert code == 2 and report["status"] == "not_ready"
    assert not (setup[2] / "signals").exists()


def test_overlap_skips(setup):
    setup[2].mkdir()
    with (setup[2] / ".schedule.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert schedule.run(*setup)[0] == 75


def test_corrupt_existing_signal_fails_closed(setup):
    target = setup[2] / "signals/2026-09-11.json"
    target.parent.mkdir(parents=True)
    target.write_text('{}')
    assert schedule.run(*setup)[0] == 1
