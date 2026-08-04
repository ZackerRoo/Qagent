from decimal import Decimal
from types import SimpleNamespace

from qagent.api.routes import (
    _paper_active_industry_counts,
    _paper_industry_capacity_filter,
    _paper_market_entry_gate_from_cache,
    _paper_merge_market_risk_gate,
    _paper_snapshot_industry,
    _paper_strategy_capacity_filter,
)


def test_cached_risk_off_market_throttles_research_paper_entries():
    health = _paper_market_entry_gate_from_cache(
        {
            "benchmark_trend": {
                "state": "risk_off",
                "entry_allowed": False,
                "reason": "3/4 个宽基指数低于 60 日均线。",
            }
        }
    )

    assert health["paper_market_entry_gate"] == "throttled"
    assert health["paper_market_entry_gate_state"] == "risk_off"
    assert health["paper_market_entry_gate_max_new_entries"] == "1"
    assert health["paper_market_entry_gate_position_size_multiplier"] == "0.3500"

    merged = _paper_merge_market_risk_gate(
        {
            "paper_risk_gate_action": "allow_new_entries",
            "paper_risk_gate_reason": "within limits",
            "paper_risk_gate_max_new_entries": "5",
            "paper_risk_gate_position_size_multiplier": "1.0000",
        },
        health,
    )
    assert merged["paper_risk_gate_action"] == "throttle_new_entries"
    assert merged["paper_risk_gate_max_new_entries"] == "1"
    assert merged["paper_risk_gate_position_size_multiplier"] == "0.3500"


def test_cached_extreme_market_still_blocks_research_paper_entries():
    health = _paper_market_entry_gate_from_cache(
        {
            "benchmark_trend": {
                "state": "extreme_risk",
                "entry_allowed": False,
                "hard_block": True,
                "reason": "market data or execution halted",
            }
        }
    )

    assert health["paper_market_entry_gate"] == "blocked"
    assert health["paper_market_entry_gate_max_new_entries"] == "0"
    assert health["paper_market_entry_gate_position_size_multiplier"] == "0.0000"


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


def test_paper_industry_capacity_blocks_third_name_and_missing_industry():
    trades = [
        SimpleNamespace(
            status="open",
            strategy_id="trend",
            instrument_id="CN:1",
            source_snapshot_id="old-1",
        ),
        SimpleNamespace(
            status="pending",
            strategy_id="quality",
            instrument_id="CN:2",
            source_snapshot_id="old-2",
            signal_date=None,
            trigger_price=None,
            latest_price=None,
            rank_score=0.8,
        ),
        SimpleNamespace(
            status="open",
            strategy_id="trend",
            instrument_id="CN:3",
            source_snapshot_id="old-3",
        ),
    ]
    contexts = {
        "old-1": SimpleNamespace(industry="银行"),
        "old-2": SimpleNamespace(industry="银行"),
        "old-3": SimpleNamespace(industry="unknown"),
    }
    paper_repo = SimpleNamespace(
        list_trades=lambda **_: trades,
        get_trade_source_context=lambda source_id: contexts[source_id],
        get_account_settings=lambda: SimpleNamespace(max_positions=10),
    )
    snapshots = [
        SimpleNamespace(
            instrument_id="CN:4",
            snapshot_id="new-bank",
            latest_close=Decimal("10.00"),
            trigger_price=Decimal("10.10"),
            card={"market_context": {"industry": "银行"}},
        ),
        SimpleNamespace(
            instrument_id="CN:5",
            snapshot_id="new-chip-1",
            latest_close=Decimal("10.00"),
            trigger_price=Decimal("10.10"),
            card={"market_context": {"industry": "半导体"}},
        ),
        SimpleNamespace(
            instrument_id="CN:6",
            snapshot_id="new-chip-2",
            latest_close=Decimal("10.00"),
            trigger_price=Decimal("10.10"),
            card={"market_context": {"industry": "半导体"}},
        ),
        SimpleNamespace(
            instrument_id="CN:7",
            snapshot_id="new-missing",
            latest_close=Decimal("10.00"),
            trigger_price=Decimal("10.10"),
            card={"market_context": {}},
        ),
    ]

    selected, health = _paper_industry_capacity_filter(
        paper_repo,
        snapshots,
        provider="free",
        max_per_industry=2,
    )

    assert [item.instrument_id for item in selected] == ["CN:5", "CN:6"]
    assert health["paper_industry_capacity_blocked"] == "1"
    assert health["paper_industry_capacity_missing"] == "1"
    assert health["paper_industry_capacity_active_unknown"] == "1"


def test_paper_industry_capacity_allows_same_industry_pending_replacement():
    trades = [
        SimpleNamespace(
            status="pending",
            strategy_id="trend",
            instrument_id="CN:1",
            source_snapshot_id="old-1",
            signal_date=None,
            trigger_price=None,
            latest_price=None,
            rank_score=0.6,
        ),
        SimpleNamespace(
            status="open",
            strategy_id="quality",
            instrument_id="CN:2",
            source_snapshot_id="old-2",
        ),
        SimpleNamespace(
            status="open",
            strategy_id="trend",
            instrument_id="CN:3",
            source_snapshot_id="old-3",
        ),
    ]
    contexts = {
        "old-1": SimpleNamespace(industry="银行"),
        "old-2": SimpleNamespace(industry="银行"),
        "old-3": SimpleNamespace(industry="电力"),
    }
    paper_repo = SimpleNamespace(
        list_trades=lambda **_: trades,
        get_trade_source_context=lambda source_id: contexts[source_id],
        get_account_settings=lambda: SimpleNamespace(max_positions=3),
    )
    candidate = SimpleNamespace(
        instrument_id="CN:4",
        snapshot_id="new-bank",
        latest_close=Decimal("10.00"),
        trigger_price=Decimal("10.10"),
        card={"market_context": {"industry": "银行"}},
    )

    selected, health = _paper_industry_capacity_filter(
        paper_repo,
        [candidate],
        provider="free",
        max_per_industry=2,
    )

    assert selected == [candidate]
    assert health["paper_industry_capacity_mode"] == "replacement_only"


def test_paper_etf_exposure_group_reclassifies_old_snapshots_and_frozen_contexts():
    a50 = SimpleNamespace(
        instrument_id="CN:563080",
        card={
            "instrument_label": "中证A50ETF易方达",
            "asset_type": "etf",
            "market_context": {"industry": "指数ETF", "board": "ETF"},
        },
    )
    dividend = SimpleNamespace(
        instrument_id="CN:515080",
        card={
            "instrument_label": "中证红利ETF招商",
            "asset_type": "etf",
            "market_context": {"industry": "指数ETF", "board": "ETF"},
        },
    )
    unknown = SimpleNamespace(
        instrument_id="CN:159999",
        card={
            "instrument_label": "测试ETF",
            "asset_type": "etf",
            "market_context": {"industry": "指数ETF", "board": "ETF"},
        },
    )

    assert _paper_snapshot_industry(a50) == "宽基ETF:中证A50"
    assert _paper_snapshot_industry(dividend) == "策略ETF:红利"
    assert _paper_snapshot_industry(unknown) is None

    trades = [
        SimpleNamespace(
            status="open",
            instrument_id="CN:563080",
            source_snapshot_id="old-a50",
        )
    ]
    contexts = {
        "old-a50": SimpleNamespace(
            industry="指数ETF",
            card={
                "instrument_label": "中证A50ETF易方达",
                "asset_type": "etf",
                "market_context": {"industry": "指数ETF", "board": "ETF"},
            },
        )
    }
    repo = SimpleNamespace(
        get_trade_source_context=lambda source_id: contexts[source_id],
    )

    counts, by_instrument, unknown_count = _paper_active_industry_counts(repo, trades)

    assert counts == {"宽基ETF:中证A50": 1}
    assert by_instrument == {"CN:563080": "宽基ETF:中证A50"}
    assert unknown_count == 0
