from types import SimpleNamespace

from qagent.api.routes import (
    _paper_market_entry_gate_from_cache,
    _paper_strategy_capacity_filter,
)


def test_cached_risk_off_market_blocks_new_paper_entries():
    health = _paper_market_entry_gate_from_cache(
        {
            "benchmark_trend": {
                "state": "risk_off",
                "entry_allowed": False,
                "reason": "3/4 个宽基指数低于 60 日均线。",
            }
        }
    )

    assert health["paper_market_entry_gate"] == "blocked"
    assert health["paper_market_entry_gate_state"] == "risk_off"


def test_paper_strategy_capacity_counts_open_and_pending_positions():
    paper_repo = SimpleNamespace(
        list_trades=lambda **_: [
            SimpleNamespace(
                status="open",
                strategy_id="trend",
                instrument_id="CN:1",
                source_snapshot_id="old-1",
            ),
            SimpleNamespace(
                status="pending",
                strategy_id="trend",
                instrument_id="CN:2",
                source_snapshot_id="old-2",
            ),
            SimpleNamespace(
                status="stopped",
                strategy_id="quality",
                instrument_id="CN:3",
                source_snapshot_id="old-3",
            ),
        ],
        get_account_settings=lambda: SimpleNamespace(max_positions=5),
    )
    snapshots = [
        SimpleNamespace(
            primary_strategy_id="trend",
            instrument_id="CN:4",
            snapshot_id="new-1",
        ),
        SimpleNamespace(
            primary_strategy_id="quality",
            instrument_id="CN:5",
            snapshot_id="new-2",
        ),
        SimpleNamespace(
            primary_strategy_id="quality",
            instrument_id="CN:6",
            snapshot_id="new-3",
        ),
        SimpleNamespace(
            primary_strategy_id="quality",
            instrument_id="CN:7",
            snapshot_id="new-4",
        ),
    ]

    selected, health = _paper_strategy_capacity_filter(
        paper_repo,
        snapshots,
        provider="free",
        max_per_strategy=2,
    )

    assert [item.primary_strategy_id for item in selected] == ["quality", "quality"]
    assert health["paper_strategy_capacity_blocked"] == "2"
