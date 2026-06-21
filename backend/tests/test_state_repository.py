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


def test_initialize_database_is_safe_for_parallel_calls(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'parallel' / 'qagent.db'}"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: initialize_database(database_url), range(8)))

    assert len(results) == 8
