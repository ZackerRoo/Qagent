from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from qagent.db import create_session_factory, initialize_database
from qagent.research.shadow_price_repair import (
    ExactPriceRequirement,
    repair_exact_daily_prices,
)
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.tables import (
    HistoricalInstrumentProfileRow,
    HistoricalTradabilityRow,
    MarketBarCacheRow,
)


def test_exact_date_repair_fills_internal_gap_hidden_by_99_37_percent_coverage(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-gap.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    start = date(2026, 1, 1)
    sessions = [start + timedelta(days=index) for index in range(159)]
    target = sessions[80]
    cached = pd.DataFrame(
        _bar("CN:000001", trade_date) for trade_date in sessions if trade_date != target
    )
    cache.save_daily_bars("fixture", cached)
    cache.record_coverage("fixture", "CN:000001", sessions[0], sessions[-1], 158)

    provider = RecordingProvider({("CN:000001", target): _bar("CN:000001", target)})
    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:000001", target, "adjusted_open")],
    )

    assert round(158 / 159, 4) == 0.9937
    assert result.repaired == 1
    assert result.missing == 0
    assert provider.calls == [(["CN:000001"], target, target)]


def test_exact_date_repair_batches_at_twenty_and_repairs_entry_and_exit(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-batches.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    entry = date(2026, 7, 2)
    exit_ = date(2026, 7, 8)
    instrument_ids = [f"CN:{index:06d}" for index in range(41)]
    rows = {
        (instrument_id, trade_date): _bar(instrument_id, trade_date)
        for instrument_id in instrument_ids
        for trade_date in (entry, exit_)
    }
    provider = RecordingProvider(rows)
    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[
            *(
                ExactPriceRequirement(instrument_id, entry, "adjusted_open")
                for instrument_id in instrument_ids
            ),
            *(
                ExactPriceRequirement(instrument_id, exit_, "adjusted_close")
                for instrument_id in instrument_ids
            ),
        ],
    )

    assert result.repaired == 82
    assert result.provider_batches == 6
    assert all(len(batch) <= 20 for batch, _, _ in provider.calls)
    assert len(cache.load_daily_bars("fixture", instrument_ids, entry, exit_)) == 82


def test_exact_date_no_row_classifies_suspension_not_listed_and_retryable_missing(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-classification.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    target = date(2026, 7, 2)
    with session_factory() as session:
        session.add(
            HistoricalTradabilityRow(
                provider_mode="fixture",
                instrument_id="CN:000001",
                trade_date=target,
                trading_status="suspended",
                source_provider="fixture",
                dataset_revision=1,
            )
        )
        session.add(
            HistoricalInstrumentProfileRow(
                provider_mode="fixture",
                instrument_id="CN:000002",
                snapshot_date=target,
                listing_date=target + timedelta(days=1),
                listing_status="pending",
                source_provider="fixture",
                dataset_revision=1,
            )
        )
        session.add(
            HistoricalInstrumentProfileRow(
                provider_mode="fixture",
                instrument_id="CN:000004",
                snapshot_date=target,
                listing_date=target - timedelta(days=100),
                listing_status="delisted",
                source_provider="fixture",
                dataset_revision=1,
            )
        )
        session.add(
            HistoricalInstrumentProfileRow(
                provider_mode="fixture",
                instrument_id="CN:000005",
                snapshot_date=target,
                listing_date=target - timedelta(days=100),
                delisting_date=target,
                listing_status="delisted",
                source_provider="fixture",
                dataset_revision=1,
            )
        )
        session.commit()
    provider = RecordingProvider({})
    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[
            ExactPriceRequirement("CN:000001", target, "adjusted_open"),
            ExactPriceRequirement("CN:000002", target, "adjusted_open"),
            ExactPriceRequirement("CN:000003", target, "adjusted_open"),
            ExactPriceRequirement("CN:000004", target, "adjusted_open"),
            ExactPriceRequirement("CN:000005", target, "adjusted_open"),
        ],
    )

    assert result.suspended == 1
    assert result.not_listed == 2
    assert result.missing == 2
    assert result.retryable == 2
    assert provider.calls == [(["CN:000003", "CN:000004"], target, target)]
    assert result.reasons == {"not_listed": 2, "provider_no_row": 2, "suspended": 1}


def test_exact_repair_preserves_existing_fields_and_filters_extra_rows(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-merge.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    target = date(2026, 7, 2)
    with session_factory() as session:
        session.add(
            MarketBarCacheRow(
                provider_mode="fixture",
                instrument_id="CN:000001",
                trade_date=target,
                source_provider="trusted",
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("10"),
                volume=Decimal("100"),
                turnover=Decimal("1000"),
                adjusted_open=Decimal("10"),
                adjusted_high=Decimal("11"),
                adjusted_low=Decimal("9"),
                adjusted_close=None,
                adjustment_factor=None,
                adjustment_type=None,
            )
        )
        session.commit()
    repaired = _bar("CN:000001", target)
    repaired.update(
        {
            "open": Decimal("20"),
            "high": Decimal("22"),
            "low": Decimal("18"),
            "close": Decimal("20"),
            "adjusted_open": Decimal("20"),
            "adjusted_high": Decimal("22"),
            "adjusted_low": Decimal("9"),
            "adjusted_close": Decimal("10.5"),
            "provider": "lower_quality_repair",
        }
    )
    extra_date = target + timedelta(days=1)
    provider = RecordingProvider(
        {
            ("CN:000001", target): repaired,
            ("CN:999999", target): _bar("CN:999999", target),
            ("CN:000001", extra_date): _bar("CN:000001", extra_date),
        },
        extras=[_bar("CN:999999", target), _bar("CN:000001", extra_date)],
    )

    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:000001", target, "adjusted_close")],
    )

    assert result.repaired == 1
    bars = cache.load_daily_bars("fixture", ["CN:000001", "CN:999999"], target, extra_date)
    assert len(bars) == 1
    row = bars.iloc[0]
    assert float(row["open"]) == 10.0
    assert float(row["adjusted_open"]) == 10.0
    assert float(row["adjusted_close"]) == 10.5
    assert row["provider"] == "trusted"


def test_exact_repair_rejects_duplicate_requested_key(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-duplicate.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    target = date(2026, 7, 2)
    row = _bar("CN:000001", target)

    with pytest.raises(ValueError, match="duplicate instrument/date"):
        cache.merge_missing_daily_bars(
            "fixture",
            pd.DataFrame([row, row]),
            allowed_keys={("CN:000001", target)},
        )


def test_exact_repair_rejects_invalid_requested_row(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-invalid.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    target = date(2026, 7, 2)
    invalid = _bar("CN:000001", target)
    invalid["open"] = Decimal("-1")

    with pytest.raises(ValueError, match="invalid OHLC"):
        MarketDataCacheRepository(session_factory).merge_missing_daily_bars(
            "fixture",
            pd.DataFrame([invalid]),
            allowed_keys={("CN:000001", target)},
        )


def test_exact_repair_deduplicates_shared_requirements_before_provider_call(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-deduplicate.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    target = date(2026, 7, 2)
    provider = RecordingProvider({("CN:000001", target): _bar("CN:000001", target)})
    requirement = ExactPriceRequirement("CN:000001", target, "adjusted_open")

    result = repair_exact_daily_prices(
        MarketDataCacheRepository(session_factory),
        provider_mode="fixture",
        market_provider=provider,
        requirements=[requirement, requirement, requirement],
    )

    assert result.requested == 1
    assert result.provider_requested == 1
    assert provider.calls == [(["CN:000001"], target, target)]


class RecordingProvider:
    def __init__(
        self,
        rows: dict[tuple[str, date], dict[str, object]],
        *,
        extras: list[dict[str, object]] | None = None,
    ):
        self.rows = rows
        self.extras = extras or []
        self.calls: list[tuple[list[str], date, date]] = []
        self.last_errors: list[str] = []

    def get_historical_daily_bars(self, instrument_ids, start, end):
        self.calls.append((instrument_ids, start, end))
        return pd.DataFrame(
            [
                self.rows[(instrument_id, start)]
                for instrument_id in instrument_ids
                if (instrument_id, start) in self.rows
            ]
            + self.extras
        )


def _bar(instrument_id: str, trade_date: date) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "trade_date": trade_date,
        "open": Decimal("10"),
        "high": Decimal("11"),
        "low": Decimal("9"),
        "close": Decimal("10"),
        "volume": Decimal("100"),
        "turnover": Decimal("1000"),
        "provider": "fixture",
        "adjusted_open": Decimal("10"),
        "adjusted_high": Decimal("11"),
        "adjusted_low": Decimal("9"),
        "adjusted_close": Decimal("10"),
        "adjustment_factor": Decimal("1"),
        "adjustment_type": "forward",
    }
