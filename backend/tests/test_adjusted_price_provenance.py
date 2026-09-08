from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import inspect, text

from qagent import db
from qagent.db import create_db_engine, create_session_factory, initialize_database
from qagent.research.shadow_price_repair import _unsafe_exact_row
from qagent.research.factor_shadow_outcomes import _adjusted_price as factor_price
from qagent.research.fuyao_shadow_outcomes import _adjusted_price as fuyao_price
from qagent.storage.market_cache import MarketDataCacheRepository


def _bar():
    return dict(
        instrument_id="CN:002743", trade_date=date(2026, 9, 1),
        open=10, high=11, low=9, close=10, volume=100, turnover=1000,
        provider="fuyao_realtime", adjusted_open=10, adjusted_high=11,
        adjusted_low=9, adjusted_close=10, adjustment_factor=1,
        adjustment_type="qfq",
    )


@pytest.fixture
def cache(tmp_path):
    url = f"sqlite:///{tmp_path / 'provenance.db'}"
    initialize_database(url)
    return MarketDataCacheRepository(create_session_factory(url))


def _load(cache):
    return cache.load_daily_bars("live", ["CN:002743"], date(2026, 9, 1), date(2026, 9, 1)).iloc[0]


def _merge(cache, row):
    cache.merge_missing_daily_bars("live", pd.DataFrame([row]), allowed_keys={("CN:002743", date(2026, 9, 1))})


@pytest.mark.parametrize("provider,kind", [("fuyao_stock_paired", "qfq"), ("tickflow_free_paired_shanghai", "forward")])
@pytest.mark.parametrize("missing", [False, True])
def test_fresh_paired_evidence_certifies_adjustments_preserving_raw_origin(cache, provider, kind, missing):
    original = _bar()
    if missing:
        for column in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "adjustment_factor", "adjustment_type"):
            original[column] = None
    cache.save_daily_bars("live", pd.DataFrame([original]))
    before = _load(cache)
    assert _unsafe_exact_row(before, "adjusted_open")
    _merge(cache, {**_bar(), "provider": provider, "adjustment_type": kind})
    after = _load(cache)
    assert after["provider"] == "fuyao_realtime"
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        assert after[column] == before[column]
    assert after["adjusted_source_provider"] == provider
    assert not _unsafe_exact_row(after, "adjusted_open")
    cache.save_daily_bars("live", pd.DataFrame([_bar()]))
    assert _load(cache)["adjusted_source_provider"] is None
    assert _unsafe_exact_row(_load(cache), "adjusted_open")


@pytest.mark.parametrize("change", [
    {"adjusted_open": 10.1}, {"open": 10.1}, {"close": 10.1},
    {"adjustment_factor": 2}, {"provider": "unknown"},
    {"adjustment_type": None}, {"adjusted_open": None},
])
def test_mismatched_or_unknown_pair_cannot_certify_retained_values(cache, change):
    cache.save_daily_bars("live", pd.DataFrame([_bar()]))
    before = _load(cache)
    _merge(cache, {**_bar(), "provider": "fuyao_stock_paired", **change})
    after = _load(cache)
    assert after["adjusted_source_provider"] is None
    assert _unsafe_exact_row(after, "adjusted_open")
    for column in ("open", "high", "low", "close", "volume", "turnover", "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "adjustment_factor"):
        assert after[column] == before[column]


def test_snapshot_anchor_is_never_relabelled_by_matching_history(cache):
    cache.save_daily_bars("live", pd.DataFrame([{**_bar(), "adjustment_type": "snapshot_qfq_anchor"}]))
    _merge(cache, {**_bar(), "provider": "fuyao_stock_paired"})
    after = _load(cache)
    assert after["adjustment_type"] == "snapshot_qfq_anchor"
    assert after["adjusted_source_provider"] is None
    assert _unsafe_exact_row(after, "adjusted_open")


def test_matching_uses_database_price_precision(cache):
    cache.save_daily_bars("live", pd.DataFrame([_bar()]))
    _merge(cache, {**_bar(), "provider": "fuyao_stock_paired", "open": Decimal("10.0000001"), "adjusted_open": Decimal("10.0000001")})
    assert not _unsafe_exact_row(_load(cache), "adjusted_open")


@pytest.mark.parametrize("change", [
    {"adjustment_factor": 2}, {"adjusted_high": 8},
    {"adjustment_type": None}, {"adjustment_factor": float("nan")},
    {"adjusted_source_provider": "unknown"},
])
def test_checker_rejects_corrupted_certificate_metadata(change):
    row = pd.Series({**_bar(), "adjusted_source_provider": "fuyao_stock_paired", **change})
    assert _unsafe_exact_row(row, "adjusted_open")


def test_existing_database_migration_adds_nullable_provenance_without_certifying_history(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    initialize_database(url)
    cache = MarketDataCacheRepository(create_session_factory(url))
    cache.save_daily_bars("live", pd.DataFrame([_bar()]))
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE market_bar_cache DROP COLUMN adjusted_source_provider"))
    # Simulate the first initialization after deploying a new process.
    db._initialized_urls.discard(url)
    initialize_database(url)
    db._initialized_urls.discard(url)
    initialize_database(url)
    column = next(item for item in inspect(engine).get_columns("market_bar_cache") if item["name"] == "adjusted_source_provider")
    assert column["nullable"]
    after = _load(cache)
    assert after["provider"] == "fuyao_realtime"
    assert after["close"] == 10
    assert after["adjusted_close"] == 10
    assert after["adjusted_source_provider"] is None
    assert _unsafe_exact_row(after, "adjusted_open")


@pytest.mark.parametrize("consumer", [factor_price, fuyao_price])
@pytest.mark.parametrize("column", ["adjusted_open", "adjusted_close"])
@pytest.mark.parametrize("metadata,expected", [
    ({}, None),
    ({"adjustment_type": "snapshot_qfq_anchor"}, None),
    ({"adjusted_source_provider": "fuyao_stock_paired"}, 10),
    ({"provider": "fixture"}, 10),
    ({"provider": "fuyao_etf_unadjusted"}, None),
    ({"provider": "fixture", "adjusted_open": float("inf"), "adjusted_close": float("inf")}, None),
])
def test_outcome_consumers_enforce_exact_price_provenance(consumer, column, metadata, expected):
    bars = pd.DataFrame([{**_bar(), **metadata}])
    assert consumer(bars, "CN:002743", date(2026, 9, 1), column) == expected
