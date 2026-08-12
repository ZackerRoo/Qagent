from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError

from qagent import db
from qagent.db import create_session_factory, initialize_database
from qagent.research.factor_shadow_outcomes import factor_shadow_outcome_dates
from qagent.research.fuyao_market_sentiment import build_fuyao_market_sentiment
from qagent.research.fuyao_shadow_outcomes import (
    build_fuyao_shadow_evaluation,
    resolve_fuyao_shadow_outcomes,
)
from qagent.storage.fuyao_research import FuyaoResearchRepository
from qagent.storage.fuyao_shadow import FuyaoShadowRepository
from qagent.storage.market_cache import MarketDataCacheRepository


def test_fuyao_shadow_outcomes_wait_for_maturity_and_remain_immutable(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fuyao-shadow.db'}"
    engine = initialize_database(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE fuyao_shadow_outcomes DROP COLUMN round_trip_cost_bps")
        )
    db._initialized_urls.discard(database_url)
    engine = initialize_database(database_url)
    assert "round_trip_cost_bps" in {
        column["name"] for column in inspect(engine).get_columns("fuyao_shadow_outcomes")
    }
    session_factory = create_session_factory(database_url)
    signal_date = date(2026, 7, 1)
    sections = {
        "hot_stock_list": {
            "item": [
                {
                    "thscode": f"{index:06d}.SZ",
                    "name": f"测试{index}",
                    "rank": 7 - index,
                }
                for index in range(1, 7)
            ]
        }
    }
    sentiment = build_fuyao_market_sentiment(sections, trade_date=signal_date)
    snapshot = FuyaoResearchRepository(session_factory).append(
        research_type="market",
        identity={"period": "day", "trade_date": signal_date.isoformat()},
        payload={
            "provider": "fuyao",
            "research_type": "market",
            "classification": "research_only",
            "decision_weight_applied": False,
            "paper_order_side_effect": False,
            "sections": {"derived_sentiment": sentiment.model_dump(mode="json")},
            "summary": {},
            "errors": [],
        },
        source_request_id="fuyao-shadow-test",
        source_timestamp="2026-07-01T15:01:00+08:00",
    )
    entry_date, outcome_date = factor_shadow_outcome_dates(signal_date, 5)
    rows: list[dict[str, object]] = []
    for signal in sentiment.signals:
        instrument_number = int(signal.instrument_id.split(":", 1)[1])
        rows.extend(
            _price_rows(
                signal.instrument_id,
                entry_date,
                outcome_date,
                outcome_close=Decimal(100 + instrument_number),
            )
        )
    rows.extend(
        _price_rows(
            "CN:000300.IDX",
            entry_date,
            outcome_date,
            outcome_close=Decimal("101"),
        )
    )
    MarketDataCacheRepository(session_factory).save_daily_bars(
        "fixture",
        pd.DataFrame(rows),
    )

    pending = resolve_fuyao_shadow_outcomes(
        session_factory,
        provider_mode="fixture",
        as_of_date=entry_date,
        horizons=(5,),
    )
    resolved = resolve_fuyao_shadow_outcomes(
        session_factory,
        provider_mode="fixture",
        as_of_date=outcome_date,
        horizons=(5,),
    )
    evaluation = build_fuyao_shadow_evaluation(
        session_factory,
        as_of_date=outcome_date,
        horizons=(5,),
    )
    retried = resolve_fuyao_shadow_outcomes(
        session_factory,
        provider_mode="fixture",
        as_of_date=outcome_date,
        horizons=(5,),
    )

    assert pending.status == "collecting"
    assert pending.outcomes_inserted == 0
    assert pending.next_maturity_date == outcome_date
    assert resolved.status == "resolved"
    assert resolved.outcomes_inserted == 6
    assert resolved.unresolved_prices == 0
    assert retried.outcomes_inserted == 0
    assert retried.outcomes_existing == 6
    assert evaluation.status == "ready"
    assert evaluation.classification == "research_only"
    assert evaluation.decision_weight_applied is False
    assert evaluation.paper_order_side_effect is False
    horizon = evaluation.horizons[0]
    assert horizon.status == "ready"
    assert horizon.outcome_coverage == 1.0
    assert horizon.mean_rank_ic == pytest.approx(1.0)
    assert horizon.average_net_excess_return_pct == pytest.approx(2.3)
    assert horizon.top_quintile_net_excess_return_pct == pytest.approx(4.3)

    outcomes = FuyaoShadowRepository(session_factory).list_outcomes()
    assert outcomes[0].snapshot_id == snapshot.snapshot_id
    assert outcomes[0].round_trip_cost_bps == 20.0
    with pytest.raises(DatabaseError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE fuyao_shadow_outcomes "
                    "SET net_excess_return_pct = 999 "
                    "WHERE outcome_id = :outcome_id"
                ),
                {"outcome_id": outcomes[0].outcome_id},
            )


def _price_rows(
    instrument_id: str,
    entry_date: date,
    outcome_date: date,
    *,
    outcome_close: Decimal,
) -> list[dict[str, object]]:
    return [
        {
            "instrument_id": instrument_id,
            "trade_date": entry_date,
            "open": Decimal("100"),
            "high": Decimal("101"),
            "low": Decimal("99"),
            "close": Decimal("100"),
            "volume": Decimal("1000000"),
            "turnover": Decimal("100000000"),
            "provider": "fixture",
            "adjusted_open": Decimal("100"),
            "adjusted_high": Decimal("101"),
            "adjusted_low": Decimal("99"),
            "adjusted_close": Decimal("100"),
            "adjustment_factor": Decimal("1"),
            "adjustment_type": "forward",
        },
        {
            "instrument_id": instrument_id,
            "trade_date": outcome_date,
            "open": outcome_close,
            "high": outcome_close + Decimal("1"),
            "low": outcome_close - Decimal("1"),
            "close": outcome_close,
            "volume": Decimal("1000000"),
            "turnover": Decimal("100000000"),
            "provider": "fixture",
            "adjusted_open": outcome_close,
            "adjusted_high": outcome_close + Decimal("1"),
            "adjusted_low": outcome_close - Decimal("1"),
            "adjusted_close": outcome_close,
            "adjustment_factor": Decimal("1"),
            "adjustment_type": "forward",
        },
    ]
