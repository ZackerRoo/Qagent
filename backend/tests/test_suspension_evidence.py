from datetime import date
import json

import pytest
import pandas as pd
from sqlalchemy import event

from qagent.db import create_session_factory, initialize_database
from qagent.research import suspension_evidence
from qagent.research.shadow_price_repair import (
    ExactPriceRequirement, repair_exact_daily_prices,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.tables import HistoricalTradabilityRow


def test_bundle_is_exact_finite_reviewed_pairs():
    evidence = suspension_evidence.load_suspension_evidence()
    assert len(evidence) == 24
    assert {
        ("CN:002731", date(2026, 9, 2)), ("CN:002731", date(2026, 9, 7)),
        ("CN:600929", date(2026, 9, 4)), ("CN:688432", date(2026, 9, 7)),
    } <= set(evidence)
    assert ("CN:002731", date(2026, 9, 8)) not in evidence
    assert ("CN:002743", date(2026, 9, 1)) not in evidence
    assert ("CN:600825", date(2026, 9, 1)) not in evidence


@pytest.mark.parametrize("kind", ["missing", "json", "future", "duplicate", "source", "usage"])
def test_invalid_bundle_fails_closed(tmp_path, kind):
    path = tmp_path / "evidence.json"
    bundle = json.loads(suspension_evidence.DEFAULT_EVIDENCE_PATH.read_text())
    if kind == "missing":
        assert suspension_evidence.load_suspension_evidence(path) == {}
        return
    if kind == "future":
        bundle["entries"][0]["confirmed_dates"] = ["2026-09-09"]
    if kind == "duplicate":
        bundle["entries"].append(bundle["entries"][0])
    if kind == "source":
        bundle["entries"][0]["sources"] = []
    if kind == "usage":
        bundle["usage"] = "trading"
    path.write_text("{" if kind == "json" else json.dumps(bundle))
    assert suspension_evidence.load_suspension_evidence(path) == {}


class EmptyProvider:
    def __init__(self):
        self.calls = []

    def get_historical_daily_bars(self, instruments, start, end):
        self.calls.append((instruments, start, end))
        raise RuntimeError("provider unavailable")


def test_real_market_evidence_does_not_suppress_fixture_requests(tmp_path):
    url = f"sqlite:///{tmp_path / 'fixture.db'}"
    initialize_database(url)
    provider = EmptyProvider()
    result = repair_exact_daily_prices(
        MarketDataCacheRepository(create_session_factory(url)), provider_mode="fixture",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:002731", date(2026, 9, 2), "adjusted_open")],
    )
    assert result.suspended == 0
    assert result.retryable == 1
    assert len(provider.calls) == 1


def test_confirmed_pairs_no_provider_no_writes_and_keep_denominator(tmp_path):
    url = f"sqlite:///{tmp_path / 'research.db'}"
    initialize_database(url)
    factory = create_session_factory(url)
    cache = MarketDataCacheRepository(factory)
    statements = []
    with factory() as session:
        engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", lambda c, u, s, p, x, m: statements.append(s))
    provider = EmptyProvider()
    requirements = [ExactPriceRequirement(symbol, day, "adjusted_open")
                    for symbol, day in suspension_evidence.load_suspension_evidence()]
    result = repair_exact_daily_prices(cache, provider_mode="real", market_provider=provider,
                                       requirements=requirements)
    assert result.requested == result.suspended == len(result.unresolved) == 24
    assert result.repaired == result.errors == result.retryable == 0
    assert result.reasons == {"confirmed_suspended": 24}
    assert provider.calls == []
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)


@pytest.mark.parametrize("status", ["trading", "unknown"])
def test_contrary_metadata_stays_retryable(tmp_path, status):
    url = f"sqlite:///{tmp_path / 'conflict.db'}"
    initialize_database(url)
    factory = create_session_factory(url)
    with factory() as session:
        session.add(HistoricalTradabilityRow(provider_mode="real", instrument_id="CN:002731",
                    trade_date=date(2026, 9, 2), trading_status=status,
                    source_provider="fixture", dataset_revision=1))
        session.commit()
    provider = EmptyProvider()
    result = repair_exact_daily_prices(MarketDataCacheRepository(factory), provider_mode="real",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:002731", date(2026, 9, 2), "adjusted_open")])
    assert result.suspended == 0
    assert result.retryable == 1
    assert len(provider.calls) == 1


def test_missing_runtime_bundle_keeps_retry_and_no_future_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(suspension_evidence, "DEFAULT_EVIDENCE_PATH", tmp_path / "absent.json")
    url = f"sqlite:///{tmp_path / 'missing.db'}"
    initialize_database(url)
    provider = EmptyProvider()
    result = repair_exact_daily_prices(
        MarketDataCacheRepository(create_session_factory(url)), provider_mode="real",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:002731", date(2026, 9, 2), "adjusted_open"),
                      ExactPriceRequirement("CN:002731", date(2026, 9, 8), "adjusted_open")])
    assert result.suspended == 0
    assert result.retryable == 2


def test_cached_price_wins_and_future_date_still_retries(tmp_path):
    url = f"sqlite:///{tmp_path / 'cached.db'}"
    initialize_database(url)
    cache = MarketDataCacheRepository(create_session_factory(url))
    cache.save_daily_bars("real", pd.DataFrame([{
        "instrument_id": "CN:002731", "trade_date": date(2026, 9, 2),
        "open": 10, "high": 11, "low": 9, "close": 10,
        "volume": 100, "adjusted_open": 10, "provider": "fixture",
        "adjusted_high": 11, "adjusted_low": 9, "adjusted_close": 10,
        "adjustment_factor": 1, "adjustment_type": "forward", "turnover": 1000,
    }]))
    provider = EmptyProvider()
    result = repair_exact_daily_prices(cache, provider_mode="real", market_provider=provider,
        requirements=[ExactPriceRequirement("CN:002731", date(2026, 9, 2), "adjusted_open"),
                      ExactPriceRequirement("CN:002731", date(2026, 9, 8), "adjusted_open")])
    assert result.cache_hits == 1
    assert result.suspended == 0
    assert result.requested == 2
    assert result.retryable == 1
    assert provider.calls == [(["CN:002731"], date(2026, 9, 8), date(2026, 9, 8))]
