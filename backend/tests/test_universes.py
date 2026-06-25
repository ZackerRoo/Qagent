from qagent.market.universes import (
    UniverseCreate,
    builtin_universes,
    merge_universes,
)
from qagent.storage.repository import QagentRepository

from test_state_repository import make_repo


def test_builtin_universes_include_fixture_and_free_starters():
    universes = builtin_universes()
    by_id = {universe.universe_id: universe for universe in universes}

    assert "fixture_dev" in by_id
    assert by_id["fixture_dev"].symbols == ["US:TEST", "CN:000001"]
    assert "free_default" in by_id
    assert by_id["free_default"].market_scope == "CN"
    assert by_id["free_default"].symbols == ["CN:ALL"]
    assert "US:NVDA" not in by_id["free_default"].symbols
    assert len(by_id["cn_liquid_starter"].symbols) >= 20
    assert all(symbol.startswith("CN:") for symbol in by_id["cn_liquid_starter"].symbols)
    assert "CN:600519" in by_id["cn_liquid_starter"].symbols
    assert all(universe.source == "builtin_starter" for universe in universes)


def test_repository_saves_and_lists_custom_universes(tmp_path):
    repo = make_repo(tmp_path)

    saved = repo.upsert_universe(
        UniverseCreate(
            universe_id="custom_ai_watch",
            name="AI Watch",
            description="Custom AI names",
            market_scope="mixed",
            tags=["ai", "custom"],
            symbols=["US:NVDA", "CN:000001"],
        )
    )
    loaded = repo.get_universe("custom_ai_watch")
    listed = repo.list_custom_universes()

    assert saved.universe_id == "custom_ai_watch"
    assert loaded is not None
    assert loaded.symbols == ["US:NVDA", "CN:000001"]
    assert listed[0].tags == ["ai", "custom"]
    assert listed[0].source == "custom"


def test_merge_universes_prefers_custom_over_builtin_with_same_id(tmp_path):
    repo: QagentRepository = make_repo(tmp_path)
    repo.upsert_universe(
        UniverseCreate(
            universe_id="free_default",
            name="My Free Pool",
            description="Override starter",
            market_scope="mixed",
            tags=["override"],
            symbols=["US:AAPL"],
        )
    )

    merged = merge_universes(repo.list_custom_universes())
    by_id = {universe.universe_id: universe for universe in merged}

    assert by_id["free_default"].name == "My Free Pool"
    assert by_id["free_default"].symbols == ["US:AAPL"]
    assert by_id["fixture_dev"].source == "builtin_starter"
