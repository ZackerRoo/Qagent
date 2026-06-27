from datetime import date, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

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


def test_create_db_engine_configures_sqlite_for_local_concurrency(tmp_path):
    db_path = tmp_path / "qagent-concurrency.db"
    engine = create_db_engine(f"sqlite:///{db_path}")

    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout >= 30000


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


def test_repository_saves_and_loads_recent_scan_result_cache(tmp_path):
    repo = make_repo(tmp_path)
    payload = {
        "symbols": ["CN:000001"],
        "cards": [],
        "items": [],
        "strategy_health": [],
        "factor_rankings": [],
        "sector_strength": [],
        "portfolio_plan": {"profile": "balanced"},
        "data_health": {"provider": "free"},
    }

    saved = repo.save_scan_result_cache(
        cache_key="today_scan:free:30:true:true",
        provider="free",
        mode="today_scan",
        symbols=["CN:000001"],
        payload=payload,
    )
    loaded = repo.get_recent_scan_result_cache(
        cache_key="today_scan:free:30:true:true",
        max_age=timedelta(minutes=60),
    )

    assert saved.cache_id.startswith("scan-cache-")
    assert loaded is not None
    assert loaded.payload == payload
    assert loaded.symbols == ["CN:000001"]
    assert (
        repo.get_recent_scan_result_cache(
            cache_key="today_scan:free:80:true:true",
            max_age=timedelta(minutes=60),
        )
        is None
    )


def test_repository_tracks_full_market_batch_scan_jobs(tmp_path):
    repo = make_repo(tmp_path)

    job = repo.create_full_market_scan_job(
        provider="free",
        symbols=["CN:000001", "CN:000002", "CN:159001"],
        batch_size=2,
        include_etfs=True,
        sync_if_empty=True,
    )
    updated = repo.update_full_market_scan_job(
        job.job_id,
        status="running",
        scanned_symbols=2,
        completed_batches=1,
        cards=3,
        errors=1,
        message="Batch 1/2 complete",
        data_health={"market_cache_hits": "2"},
    )
    loaded = repo.get_full_market_scan_job(job.job_id)
    latest = repo.get_latest_full_market_scan_job(provider="free")

    assert job.job_id.startswith("full-scan-")
    assert job.status == "queued"
    assert job.total_symbols == 3
    assert job.total_batches == 2
    assert job.progress == 0
    assert updated is not None
    assert updated.progress == 66
    assert updated.cards == 3
    assert updated.errors == 1
    assert loaded is not None
    assert loaded.message == "Batch 1/2 complete"
    assert loaded.data_health["market_cache_hits"] == "2"
    assert latest is not None
    assert latest.job_id == job.job_id


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
