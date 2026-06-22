from datetime import date
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from qagent.db import Base, create_db_engine, create_session_factory, initialize_database
from qagent.storage.repository import (
    AlertRuleCreate,
    PositionCreate,
    QagentRepository,
    WatchlistCreate,
)


def make_repo(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'qagent-test.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    return QagentRepository(session_factory)


def test_repository_adds_and_lists_watchlist_items(tmp_path):
    repo = make_repo(tmp_path)

    item = repo.upsert_watchlist_item(
        WatchlistCreate(
            instrument_id="CN:000001",
            thesis="A-share bank setup",
            status="watch",
            tags=["bank", "cn"],
        )
    )

    assert item.instrument_id == "CN:000001"
    assert repo.list_watchlist_items()[0].tags == ["bank", "cn"]


def test_repository_adds_and_lists_positions(tmp_path):
    repo = make_repo(tmp_path)

    position = repo.upsert_position(
        PositionCreate(
            instrument_id="US:TEST",
            shares=Decimal("10"),
            entry_price=Decimal("82.00"),
            entry_date=date(2026, 3, 31),
            strategy_tag="breakout",
            initial_stop=Decimal("78.72"),
            target_1=Decimal("88.56"),
            thesis="Fixture breakout card",
        )
    )

    assert position.instrument_id == "US:TEST"
    assert position.entry_price == Decimal("82.00")
    assert repo.list_positions()[0].strategy_tag == "breakout"


def test_create_db_engine_creates_sqlite_parent_directory(tmp_path):
    db_path = tmp_path / "nested" / "qagent.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    assert db_path.exists()


def test_repository_adds_and_lists_alert_rules(tmp_path):
    repo = make_repo(tmp_path)

    rule = repo.upsert_alert_rule(
        AlertRuleCreate(
            rule_id="entry-US-TEST",
            instrument_id="US:TEST",
            kind="entry_trigger",
            operator=">=",
            threshold=Decimal("82.00"),
        )
    )

    assert rule.rule_id == "entry-US-TEST"
    assert repo.list_alert_rules()[0].threshold == Decimal("82.00")


def test_repository_saves_scan_run_and_opportunity_snapshots(tmp_path):
    from qagent.jobs.daily_scan import run_daily_scan
    from qagent.providers.fixtures import FixtureMarketDataProvider

    repo = make_repo(tmp_path)
    result = run_daily_scan(["US:TEST"], FixtureMarketDataProvider())

    run = repo.save_scan_run(
        provider="fixture",
        mode="fixture",
        symbols=["US:TEST"],
        result=result,
    )

    runs = repo.list_scan_runs(limit=5)
    snapshots = repo.list_opportunity_snapshots(limit=5)
    assert runs[0].run_id == run.run_id
    assert runs[0].symbols == ["US:TEST"]
    assert runs[0].cards == 1
    assert runs[0].data_health["provider"] == "fixture"
    assert len(snapshots) == 1
    assert snapshots[0].run_id == run.run_id
    assert snapshots[0].instrument_id == "US:TEST"
    assert snapshots[0].primary_strategy_id
    assert snapshots[0].signal_date is not None
    assert snapshots[0].latest_close == Decimal("82.00")
    assert snapshots[0].card["instrument_id"] == "US:TEST"


def test_repository_filters_opportunity_snapshots_by_instrument(tmp_path):
    from qagent.jobs.daily_scan import run_daily_scan
    from qagent.providers.fixtures import FixtureMarketDataProvider

    repo = make_repo(tmp_path)
    result = run_daily_scan(["US:TEST", "CN:000001"], FixtureMarketDataProvider())
    repo.save_scan_run(
        provider="fixture",
        mode="fixture",
        symbols=["US:TEST", "CN:000001"],
        result=result,
    )

    snapshots = repo.list_opportunity_snapshots(instrument_id="CN:000001", limit=5)

    assert snapshots
    assert {snapshot.instrument_id for snapshot in snapshots} == {"CN:000001"}


def test_repository_saves_and_loads_brief_runs(tmp_path):
    from qagent.briefing.daily import build_daily_brief
    from qagent.jobs.daily_scan import run_daily_scan
    from qagent.providers.fixtures import FixtureMarketDataProvider

    repo = make_repo(tmp_path)
    scan = run_daily_scan(["US:TEST"], FixtureMarketDataProvider())
    brief = build_daily_brief(
        provider="fixture",
        symbols=["US:TEST"],
        scan_result=scan,
        limit=5,
        data_health={"brief_news": "skipped"},
    )

    saved = repo.save_brief_run(brief)
    runs = repo.list_brief_runs(limit=5)
    loaded = repo.get_brief_run(saved.brief_id)

    assert saved.brief_id.startswith("brief-")
    assert saved.provider == "fixture"
    assert saved.symbols == ["US:TEST"]
    assert saved.headline == brief.headline
    assert saved.opportunity_count == len(brief.top_opportunities)
    assert saved.payload["headline"] == brief.headline
    assert runs[0].brief_id == saved.brief_id
    assert loaded is not None
    assert loaded.payload["top_opportunities"][0]["instrument_id"] == "US:TEST"


def test_repository_queues_and_marks_brief_delivery(tmp_path):
    from qagent.briefing.daily import build_daily_brief
    from qagent.jobs.daily_scan import run_daily_scan
    from qagent.providers.fixtures import FixtureMarketDataProvider

    repo = make_repo(tmp_path)
    scan = run_daily_scan(["US:TEST"], FixtureMarketDataProvider())
    brief = build_daily_brief(
        provider="fixture",
        symbols=["US:TEST"],
        scan_result=scan,
        limit=5,
        data_health={"brief_news": "skipped"},
    )
    saved = repo.save_brief_run(brief)

    delivery = repo.enqueue_brief_delivery(
        brief_run=saved,
        channel="markdown",
        recipient="local",
        markdown="# Qagent Daily Brief\n",
    )
    queued = repo.list_delivery_outbox(status="queued", limit=5)
    sent = repo.mark_delivery_sent(delivery.delivery_id)

    assert delivery.delivery_id.startswith("delivery-")
    assert delivery.brief_id == saved.brief_id
    assert delivery.status == "queued"
    assert delivery.channel == "markdown"
    assert delivery.recipient == "local"
    assert delivery.subject == saved.headline
    assert delivery.markdown.startswith("# Qagent Daily Brief")
    assert queued[0].delivery_id == delivery.delivery_id
    assert sent is not None
    assert sent.status == "sent"
    assert sent.sent_at is not None
    assert repo.list_delivery_outbox(status="queued", limit=5) == []


def test_initialize_database_is_safe_for_parallel_calls(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'parallel' / 'qagent.db'}"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: initialize_database(database_url), range(8)))

    assert len(results) == 8
