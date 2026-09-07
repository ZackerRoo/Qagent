import json
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from qagent.recommendations.forward_alignment import (
    KEY, capture_recommendation_order, build_forward_alignment_report, frozen_selection_signals,
    _valid_saved_fact,
    compare_historical_identity,
)
from qagent.storage.tables import Base, ScanRunRow


def capture(n=12, **kwargs):
    cards = [NS(card_id=str(i), instrument_id=f"CN:{i:06}", primary_strategy_id=f"s{i // 2}",
                status=NS(value="entry_watch"), rank_score=0.8,
                entry_plan=NS(trigger_price=10, no_chase_above=11),
                exit_plan=NS(initial_stop=9, target_1=12)) for i in range(n)]
    items = {c.instrument_id: NS(latest_trade_date=date(2026, 9, 7)) for c in cards}
    return capture_recommendation_order(cards, items,
        recorded_at=datetime(2026, 9, 7, 7, tzinfo=timezone.utc), data_health={},
        source_complete=True, **kwargs)


def test_capture_preserves_order_and_frozen_plan_without_reranking():
    fact = capture()
    assert fact["blockers"] == []
    assert [i["original_rank"] for i in fact["top10"]] == list(range(1, 11))
    signals = frozen_selection_signals(fact, run_id="run", size=5)
    assert len(signals) == 5
    assert signals[0].trigger_price == 10
    assert signals[0].no_chase_above == 11


def test_complete_small_universe_is_valid_and_market_gate_can_close():
    assert capture(3)["blockers"] == []
    assert len(capture(3)["top10"]) == 3
    assert capture(benchmark_entry_allowed=False)["top10"] == []


@pytest.mark.parametrize("field,value", [("decision_date", []), ("decision_date", "broken"),
    ("model_identity", []), ("blockers", [{}]), ("source_complete", "true"), ("top10", {})])
def test_malformed_facts_fail_closed(field, value):
    fact = capture()
    fact[field] = value
    assert not _valid_saved_fact(fact)


def test_historical_identity_requires_matching_frozen_fields_and_gate():
    fact = capture(benchmark_entry_allowed=True)
    fact["model_identity"] = {"feature_set_version": "features-1",
        "recommendation_policy_entrypoint": "policy-1", "ranking_model_version": "rank-1"}
    reference = {key: fact[key] for key in ("model_identity", "selection_implementation_digest", "ranking_implementation_digest")}
    assert compare_historical_identity(fact, reference) == {"comparable": True, "differences": []}
    changed = {**reference, "ranking_implementation_digest": "different"}
    assert compare_historical_identity(fact, changed)["differences"] == ["ranking_implementation_digest"]
    fact["model_identity"] = {**fact["model_identity"], "ranking_model_version": None}
    assert "model_identity:ranking_model_version" in compare_historical_identity(fact, reference)["differences"]
    assert compare_historical_identity(fact, None)["comparable"] is False


def test_capture_failure_does_not_interrupt_scan_or_mutate_input(tmp_path, monkeypatch):
    from qagent.storage.repository import QagentRepository
    from qagent.recommendations import forward_alignment
    engine = create_engine(f"sqlite:///{tmp_path / 'save.db'}")
    Base.metadata.create_all(engine)
    repo = QagentRepository(sessionmaker(engine))
    result = NS(items=[], cards=[], data_health={"original": "kept"})
    monkeypatch.setattr(forward_alignment, "capture_recommendation_order", lambda *a, **k: 1 / 0)
    run = repo.save_scan_run(provider="fixture", mode="fixture", symbols=[], result=result)
    assert result.data_health == {"original": "kept"}
    assert result.cards == []
    assert json.loads(run.data_health[KEY])["blockers"] == ["capture_error"]


def test_report_canonical_legacy_exclusion_and_no_writes(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory() as session:
        for run_id, hour, fact in [("legacy", 6, None), ("first", 7, capture()), ("later", 8, capture())]:
            session.add(ScanRunRow(run_id=run_id, provider="free", mode="full_market_batch",
                symbols="[]", scanned=12, cards=12, data_health=json.dumps({KEY: json.dumps(fact)} if fact else {}),
                created_at=datetime(2026, 9, 7, hour, tzinfo=timezone.utc)))
        session.commit()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql))
    report = build_forward_alignment_report(factory, provider="free", start=date(2026, 9, 7), end=date(2026, 9, 7))
    assert report["legacy_runs_excluded"] == 1
    assert len(report["cohorts"]) == 1
    assert report["cohorts"][0]["run_id"] == "first"
    assert report["cohorts"][0]["metrics"]["top5"]["5"]["mean_return_pct"] is None
    assert report["historical_comparison"]["comparable"] is False
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


def test_api_rejects_future_and_does_not_initialize_database(monkeypatch):
    from qagent.api import routes
    from datetime import timedelta
    from fastapi import HTTPException
    monkeypatch.setattr(routes, "initialize_database", lambda: pytest.fail("must not initialize"))
    with pytest.raises(HTTPException) as error:
        routes.recommendation_forward_alignment(end=date.today() + timedelta(days=1))
    assert error.value.status_code == 422


@pytest.mark.parametrize("missing", [False, True])
def test_maturity_uses_exchange_sessions_not_available_bar_count(tmp_path, monkeypatch, missing):
    from qagent.market.calendars import trading_sessions_in_range
    from qagent.storage.market_cache import MarketDataCacheRepository
    engine = create_engine(f"sqlite:///{tmp_path / 'mature.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    fact = capture(1)
    fact["decision_date"] = "2026-07-07"
    fact["top10"][0]["signal_date"] = "2026-07-07"
    days = trading_sessions_in_range(date(2026, 7, 7), date(2026, 8, 10))
    bars = pd.DataFrame([{"instrument_id": "CN:000000", "trade_date": day,
        "close": 100 + i} for i, day in enumerate(days) if not (missing and i == 3)])
    monkeypatch.setattr(MarketDataCacheRepository, "load_daily_bars", lambda *a, **k: bars)
    with factory() as session:
        session.add(ScanRunRow(run_id="mature", provider="free", mode="full_market_batch",
            symbols="[]", scanned=1, cards=1, data_health=json.dumps({KEY: json.dumps(fact)}),
            created_at=datetime(2026, 7, 7, 7, tzinfo=timezone.utc)))
        session.commit()
    report = build_forward_alignment_report(factory, provider="free", start=date(2026, 7, 7), end=date(2026, 8, 10))
    metric = report["cohorts"][0]["metrics"]["top5"]["5"]
    assert metric["mature_count"] == (0 if missing else 1)
    assert metric["mean_return_pct"] == (None if missing else 5.0)
