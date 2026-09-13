from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from threading import Event
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from qagent.storage.paper import PaperTradingRepository
from qagent.storage.paper_writer import paper_account_writer, run_paper_update_slot
from qagent.paper_trading.engine import seed_paper_trades_from_snapshots, update_paper_trades
from test_state_repository import make_repo


def _hold_writer(database, ready, release):
    factory = sessionmaker(bind=create_engine("sqlite:///" + database))
    with paper_account_writer(factory):
        ready.set()
        release.wait(10)


def test_writer_is_reentrant_and_serializes_different_repositories(tmp_path):
    repo = make_repo(tmp_path)
    second = sessionmaker(bind=create_engine(str(repo.session_factory.kw["bind"].url)))
    started, entered = Event(), Event()

    def worker():
        started.set()
        with paper_account_writer(second):
            entered.set()

    with ThreadPoolExecutor(1) as executor:
        with paper_account_writer(repo.session_factory):
            with paper_account_writer(second):
                pending = executor.submit(worker)
                assert started.wait(2)
                assert not entered.wait(.05)
        pending.result(2)
    assert entered.is_set()


def test_process_death_releases_writer(tmp_path):
    repo = make_repo(tmp_path)
    context = get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_writer, args=(
        repo.session_factory.kw["bind"].url.database, ready, release,
    ))
    process.start()
    try:
        assert ready.wait(8)
        entered = Event()
        with ThreadPoolExecutor(1) as executor:
            def enter():
                with paper_account_writer(repo.session_factory):
                    entered.set()
            future = executor.submit(enter)
            assert not entered.wait(.1)
            process.terminate()
            process.join(5)
            future.result(5)
        assert entered.is_set()
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)


def test_failure_releases_writer_and_slot_retries_then_replays(tmp_path):
    repo = make_repo(tmp_path)
    attempts = []

    def callback():
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("partial failure")
        return {"updated": 1}

    with pytest.raises(ValueError):
        run_paper_update_slot(repo.session_factory, "slot", callback)
    assert not run_paper_update_slot(repo.session_factory, "slot", callback)["replayed"]
    assert run_paper_update_slot(repo.session_factory, "slot", callback)["replayed"]
    assert len(attempts) == 2


@pytest.mark.parametrize("operation", ["update", "seed", "settings"])
def test_account_operations_wait_before_reading_account(tmp_path, operation):
    repo = PaperTradingRepository(make_repo(tmp_path).session_factory)
    reached = Event()
    original = repo.get_account_settings

    def read():
        reached.set()
        return original()

    repo.get_account_settings = read
    if operation == "update":
        def run():
            return update_paper_trades(repo, provider=SimpleNamespace(name="fixture"))
    elif operation == "seed":
        def run():
            return seed_paper_trades_from_snapshots(repo, [], "fixture")
    else:
        def run():
            settings = original()
            repo.start_account_session(**{
                key: getattr(settings, key) for key in (
                    "label", "initial_capital", "allocation_per_trade_pct", "max_positions",
                    "transaction_cost_bps", "slippage_bps", "take_profit_pct",
                )
            })
            reached.set()
    with ThreadPoolExecutor(1) as executor:
        with paper_account_writer(repo.session_factory):
            future = executor.submit(run)
            assert not reached.wait(.05)
        future.result(2)
    assert reached.is_set()


def test_tick_checks_expiry_and_control_after_writer_wait(tmp_path, monkeypatch):
    from qagent.api import routes

    repo = PaperTradingRepository(make_repo(tmp_path).session_factory)
    due = datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc)
    state = SimpleNamespace(enabled=True, settings=SimpleNamespace(update_paper=True, provider="free"))
    monkeypatch.setattr(routes, "_paper_repo", lambda: repo)
    monkeypatch.setattr(routes, "_automation_scheduler", SimpleNamespace(state=lambda: state))
    monkeypatch.setattr(routes, "current_slot", lambda now: None)
    monkeypatch.setattr(routes, "update_paper_trades", lambda *a, **kw: pytest.fail("expired mutation"))
    tick = SimpleNamespace(slot_id="expired", due_at=due)
    with pytest.raises(RuntimeError, match="expired"):
        routes._run_independent_paper_update(tick)
    state.enabled = False
    with pytest.raises(RuntimeError, match="disabled"):
        routes._run_independent_paper_update(tick)


def test_tick_refreshes_provider_and_replays_completed_slot(tmp_path, monkeypatch):
    from qagent.api import routes

    repo = PaperTradingRepository(make_repo(tmp_path).session_factory)
    due = datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc)
    state = SimpleNamespace(enabled=True, settings=SimpleNamespace(update_paper=True, provider="free"))
    monkeypatch.setattr(routes, "_paper_repo", lambda: repo)
    monkeypatch.setattr(routes, "_automation_scheduler", SimpleNamespace(state=lambda: state))
    monkeypatch.setattr(routes, "current_slot", lambda now: due)
    monkeypatch.setattr(routes, "build_market_data_provider", lambda mode: mode)
    calls = []

    def update(*args, **kwargs):
        calls.append(kwargs["provider_mode"])
        from qagent.paper_trading.engine import PaperUpdateResult, summarize_paper_trades
        return PaperUpdateResult(summary=summarize_paper_trades([]), trades=[], data_health={
            "paper_price_requested": "0", "paper_price_resolved": "0",
        })

    monkeypatch.setattr(routes, "update_paper_trades", update)
    tick = SimpleNamespace(slot_id="slot", due_at=due)
    assert not routes._run_independent_paper_update(tick)["replayed"]
    assert routes._run_independent_paper_update(tick)["replayed"]
    assert calls == ["free"]


def test_tick_lifecycle_honors_master_and_update_switch(monkeypatch):
    from qagent.api import routes

    state = SimpleNamespace(enabled=True, settings=SimpleNamespace(update_paper=True))
    config = SimpleNamespace(paper_update_scheduler_enabled=False)
    calls = []
    monkeypatch.setattr(routes, "get_settings", lambda: config)
    monkeypatch.setattr(routes, "_automation_scheduler", SimpleNamespace(state=lambda: state))
    monkeypatch.setattr(routes, "_paper_update_scheduler", SimpleNamespace(
        start=lambda: calls.append("start"), stop=lambda: calls.append("stop"),
    ))
    routes._sync_paper_update_scheduler()
    config.paper_update_scheduler_enabled = True
    routes._sync_paper_update_scheduler()
    state.settings.update_paper = False
    routes._sync_paper_update_scheduler()
    state.settings.update_paper = True
    state.enabled = False
    routes._sync_paper_update_scheduler()
    assert calls == ["stop", "start", "stop", "stop"]


def test_legacy_update_retained_outside_independent_session(monkeypatch):
    from qagent.api import routes

    state = SimpleNamespace(enabled=True, settings=SimpleNamespace(update_paper=True))
    config = SimpleNamespace(paper_update_scheduler_enabled=True)
    monkeypatch.setattr(routes, "get_settings", lambda: config)
    monkeypatch.setattr(routes, "_automation_scheduler", SimpleNamespace(state=lambda: state))
    monkeypatch.setattr(routes, "current_slot", lambda now: now)
    assert routes._independent_paper_update_covers_now()
    monkeypatch.setattr(routes, "current_slot", lambda now: None)
    assert not routes._independent_paper_update_covers_now()
    monkeypatch.setattr(routes, "current_slot", lambda now: now)
    config.paper_update_scheduler_enabled = False
    assert not routes._independent_paper_update_covers_now()
