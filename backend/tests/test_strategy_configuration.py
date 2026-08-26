from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from qagent.recommendations.strategy_configuration import (
    build_paper_strategy_configuration,
    parse_paper_strategy_configuration,
)
from qagent.storage.paper import PaperAccountSettings


def test_paper_strategy_configuration_is_canonical_and_tamper_evident():
    account = PaperAccountSettings(
        account_id="default",
        session_id="paper-session-test",
        label="test",
        status="active",
        initial_capital=Decimal("100000"),
        allocation_per_trade_pct=Decimal("10"),
        max_positions=10,
        transaction_cost_bps=Decimal("5"),
        slippage_bps=Decimal("5"),
        take_profit_pct=Decimal("50"),
        started_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    configuration, digest = build_paper_strategy_configuration(
        provider="free",
        signal_date=date(2026, 8, 26),
        symbols=["CN:000001", "CN:000001", "CN:510300"],
        include_etfs=True,
        feature_set_version="factor-v3",
        recommendation_policy="final-policy-v1",
        calibration_merge_policy="fixed",
        quality_weights={"trend": 0.2, "momentum": 0.14},
        governance_source="fixture",
        governance_strategies={
            "trend": SimpleNamespace(
                strategy_version="v1",
                state="shadow",
                policy_version="p1",
                effective_weight=0.5,
                policy={"threshold": 0.6},
            )
        },
        account=account,
    )

    import json

    encoded = json.dumps(configuration, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert parse_paper_strategy_configuration(encoded, digest) == (configuration, digest)
    assert parse_paper_strategy_configuration(encoded, "0" * 64) is None
    assert configuration["universe"]["symbol_count"] == 2
    assert configuration["execution"]["max_positions"] == 10
