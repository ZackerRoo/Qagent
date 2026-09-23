from datetime import datetime, timedelta
from threading import Event, Thread
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from qagent.config import Settings
from qagent.jobs.paper_update_scheduler import (
    PaperUpdateScheduler,
    PaperUpdateSlotExpiredWhileWaitingForWriter,
    current_slot,
)

SH = ZoneInfo("Asia/Shanghai")


def at(hour, minute=0, second=0, day=14):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=SH)


def weekdays(day):
    return day.weekday() < 5


@pytest.mark.parametrize("now, expected", [
    (at(9, 29), None), (at(9, 30), at(9, 30)), (at(10, 9), at(10)),
    (at(11, 29), at(11, 20)), (at(11, 30, 59), at(11, 30)),
    (at(11, 31), None), (at(12), None), (at(13), at(13)),
    (at(15, 0, 59), at(15)), (at(15, 1), None), (at(10, day=13), None),
])
def test_shanghai_session_slots(now, expected):
    assert current_slot(now, weekdays) == expected


def test_real_calendar_excludes_exchange_holiday_and_weekend():
    assert current_slot(datetime(2025, 10, 1, 10, tzinfo=SH)) is None
    assert current_slot(at(10, day=13)) is None


def test_slot_identity_stable_on_restart_with_durable_runner():
    now = [at(10, 1)]
    completed = set()
    effects = []

    def durable_runner(tick):
        if tick.slot_id not in completed:
            effects.append(tick)
            completed.add(tick.slot_id)

    first = PaperUpdateScheduler(durable_runner, clock=lambda: now[0], is_session=weekdays)
    assert first.run_due()
    assert not first.run_due()
    now[0] = at(10, 3)
    restarted = PaperUpdateScheduler(durable_runner, clock=lambda: now[0], is_session=weekdays)
    assert restarted.run_due()
    assert len(effects) == 1
    assert first.state().slot_id == restarted.state().slot_id


def test_errors_retry_same_slot_then_skip_expired_without_fake_asof():
    now = [at(10, 1)]
    ticks = []

    def fail(tick):
        ticks.append(tick)
        raise RuntimeError("provider failed")

    scheduler = PaperUpdateScheduler(fail, clock=lambda: now[0], is_session=weekdays)
    assert not scheduler.run_due()
    assert scheduler.state().last_error == "RuntimeError"
    assert not scheduler.run_due()
    assert len(ticks) == 1
    now[0] += timedelta(seconds=31)
    scheduler.run_due()
    assert ticks[0].slot_id == ticks[1].slot_id
    now[0] = at(10, 31)
    scheduler.run_due()
    assert ticks[-1].due_at == at(10, 30)
    assert ticks[-1].started_at == at(10, 31)
    assert scheduler.state().skipped_slots == 3
    assert scheduler.state().lateness_seconds == 60


def test_blocked_callback_no_overlap_and_bounded_shutdown():
    entered, release = Event(), Event()
    ticks = []

    def block(tick):
        ticks.append(tick)
        entered.set()
        release.wait(2)

    scheduler = PaperUpdateScheduler(block, clock=lambda: at(10), is_session=weekdays)
    try:
        scheduler.start()
        assert entered.wait(1)
        assert not scheduler.run_due()
        assert not scheduler.stop(timeout=0.01)
        with pytest.raises(RuntimeError, match="still stopping"):
            scheduler.start()
        assert len(ticks) == 1
        assert scheduler.state().status == "stopping"
    finally:
        release.set()
        assert scheduler.stop(timeout=1)
    scheduler.start()
    assert scheduler.state().enabled
    assert scheduler.stop(timeout=1)


def test_research_blocked_across_tick_does_not_block_independent_worker():
    research_started, release, paper_called = Event(), Event(), Event()
    now = [at(9, 29, 59)]

    def research():
        research_started.set()
        release.wait(2)

    thread = Thread(target=research)
    thread.start()
    scheduler = PaperUpdateScheduler(
        lambda tick: paper_called.set(), clock=lambda: now[0],
        is_session=weekdays, poll_seconds=0.01,
    )
    try:
        assert research_started.wait(1)
        scheduler.start()
        now[0] = at(9, 30)
        assert paper_called.wait(1)
        assert thread.is_alive()
    finally:
        release.set()
        thread.join(1)
        scheduler.stop()


def test_slow_callback_anchors_next_tick_and_does_not_replay_missed_slots():
    now = [at(10)]
    ticks = []

    def runner(tick):
        ticks.append(tick)
        now[0] = at(10, 25)

    scheduler = PaperUpdateScheduler(runner, clock=lambda: now[0], is_session=weekdays)
    assert scheduler.run_due()
    assert scheduler.run_due()
    assert [tick.due_at for tick in ticks] == [at(10), at(10, 20)]
    assert scheduler.state().skipped_slots == 1


def test_state_read_is_passive_and_calendar_failure_fails_closed():
    ticks = []

    def broken_calendar(day):
        raise ValueError("calendar unavailable")

    scheduler = PaperUpdateScheduler(ticks.append, clock=lambda: at(10), is_session=broken_calendar)
    assert scheduler.state().status == "stopped"
    assert not scheduler.run_due()
    assert ticks == []
    assert scheduler.state().last_error == "ValueError"


def test_upstream_secrets_are_not_exposed_in_scheduler_diagnostics():
    def runner(tick):
        raise RuntimeError("https://upstream/?token=private-token")

    scheduler = PaperUpdateScheduler(runner, clock=lambda: at(10), is_session=weekdays)
    assert not scheduler.run_due()
    assert scheduler.state().last_error == "RuntimeError"
    assert scheduler.state().error_code == "runner_failed"
    assert scheduler.state().error_reason == "Paper update failed; the scheduler will retry the current slot."
    assert "private-token" not in repr(scheduler.state())


def test_slot_expired_while_waiting_for_writer_has_safe_failure_details():
    now = [at(10)]

    def expired(tick):
        error = PaperUpdateSlotExpiredWhileWaitingForWriter()
        error.writer_wait_seconds = 0.25
        raise error

    scheduler = PaperUpdateScheduler(expired, clock=lambda: now[0], is_session=weekdays)
    assert not scheduler.run_due()
    state = scheduler.state()
    assert state.error_code == "slot_expired_while_waiting_for_writer"
    assert state.error_reason == "Paper update slot expired while waiting for the account writer."
    assert state.writer_wait_seconds == 0.25


def test_opt_in_and_fixed_interval(monkeypatch):
    settings = Settings(_env_file=None)
    assert not settings.paper_update_scheduler_enabled
    assert settings.paper_update_interval_seconds == 600
    with pytest.raises(ValidationError):
        Settings(_env_file=None, paper_update_interval_seconds=60)
    monkeypatch.setenv("QAGENT_PAPER_UPDATE_INTERVAL_SECONDS", "600")
    assert Settings(_env_file=None).paper_update_interval_seconds == 600
