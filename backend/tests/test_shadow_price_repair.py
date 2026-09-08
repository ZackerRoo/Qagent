from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from qagent.db import create_session_factory, initialize_database
from qagent.research.shadow_price_repair import (
    ExactPriceRepairBudget,
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


def test_exact_repair_budget_defers_unattempted_rows_but_keeps_not_listed_terminal(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'exact-budget.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    target = date(2026, 7, 2)
    instrument_ids = [f"CN:{index:06d}" for index in range(45)]
    not_listed = instrument_ids[0]
    with session_factory() as session:
        session.add(
            HistoricalInstrumentProfileRow(
                provider_mode="fixture",
                instrument_id=not_listed,
                snapshot_date=target,
                listing_date=target + timedelta(days=1),
                listing_status="pending",
                source_provider="fixture",
                dataset_revision=1,
            )
        )
        session.commit()
    rows = {
        (instrument_id, target): _bar(instrument_id, target)
        for instrument_id in instrument_ids[1:]
    }
    provider = RecordingProvider(rows)
    budget = ExactPriceRepairBudget.bounded(
        max_provider_batches=1,
        wall_clock_seconds=60,
    )

    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[
            ExactPriceRequirement(instrument_id, target, "adjusted_open")
            for instrument_id in instrument_ids
        ],
        work_budget=budget,
    )

    assert provider.calls == [(instrument_ids[1:21], target, target)]
    assert result.provider_batches == 1
    assert result.repaired == 20
    assert result.not_listed == 1
    assert result.deferred_by_budget == 24
    assert result.retryable == 24
    assert result.budget_exhausted_reason == "provider_batch_budget"
    assert result.reasons == {"not_listed": 1, "work_budget_exhausted": 24}


def test_exact_repair_expired_wall_clock_budget_makes_no_provider_call(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-deadline.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    target = date(2026, 7, 2)
    provider = RecordingProvider({("CN:000001", target): _bar("CN:000001", target)})

    result = repair_exact_daily_prices(
        MarketDataCacheRepository(session_factory),
        provider_mode="fixture",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:000001", target, "adjusted_open")],
        work_budget=ExactPriceRepairBudget.bounded(
            max_provider_batches=1,
            wall_clock_seconds=0,
        ),
    )

    assert provider.calls == []
    assert result.deferred_by_budget == 1
    assert result.budget_exhausted_reason == "wall_clock_deadline"
    assert result.data_health("factor_shadow")[
        "factor_shadow_exact_price_budget_exhausted"
    ] == "true"


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


@pytest.mark.parametrize(
    ("provider_name", "adjustment_type"),
    [
        ("fuyao_etf_unadjusted", "none"),
        ("fuyao_realtime", "snapshot_qfq_anchor"),
    ],
)
def test_exact_repair_rejects_unsafe_current_or_unadjusted_fuyao_prices(
    tmp_path,
    provider_name,
    adjustment_type,
):
    database_url = f"sqlite:///{tmp_path / 'unsafe-provenance.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    target = date(2026, 7, 2)
    row = _bar("CN:510300", target)
    row["provider"] = provider_name
    row["adjustment_type"] = adjustment_type
    provider = RecordingProvider({("CN:510300", target): row})

    result = repair_exact_daily_prices(
        MarketDataCacheRepository(session_factory),
        provider_mode="fixture",
        market_provider=provider,
        requirements=[ExactPriceRequirement("CN:510300", target, "adjusted_close")],
    )

    assert result.repaired == 0
    assert result.missing == 1
    assert result.rejected_unsafe_provenance == 1
    assert result.reasons == {"unsafe_adjustment_provenance": 1}


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


def test_exact_repair_rechecks_later_batch_after_concurrent_scan_fill(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-later-batch-recheck.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(session_factory)
    target = date(2026, 7, 2)
    instrument_ids = [f"CN:{index:06d}" for index in range(25)]
    first_batch = instrument_ids[:20]
    later_batch = instrument_ids[20:]

    class ConcurrentFillProvider(RecordingProvider):
        def get_historical_daily_bars(self, instrument_ids, start, end):
            frame = super().get_historical_daily_bars(instrument_ids, start, end)
            if len(self.calls) == 1:
                cache.save_daily_bars(
                    "fixture",
                    pd.DataFrame(_bar(instrument_id, target) for instrument_id in later_batch),
                )
            return frame

    provider = ConcurrentFillProvider(
        {(instrument_id, target): _bar(instrument_id, target) for instrument_id in first_batch}
    )
    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[
            ExactPriceRequirement(instrument_id, target, "adjusted_open")
            for instrument_id in instrument_ids
        ],
    )

    assert provider.calls == [(first_batch, target, target)]
    assert result.requested == 25
    assert result.provider_requested == 20
    assert result.provider_batches == 1
    assert result.skipped_after_recheck == 5
    assert result.cache_hits == 0
    assert result.repaired == 25
    assert result.unresolved == []
    assert result.repaired + len(result.unresolved) == result.requested - result.cache_hits


def test_exact_repair_rechecks_same_batch_and_requests_only_remaining_instruments(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-same-batch-recheck.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    target = date(2026, 7, 2)
    concurrently_filled = "CN:000001"
    provider_ids = ["CN:000002", "CN:000003"]

    class FillOnRecheckCache(MarketDataCacheRepository):
        def __init__(self):
            super().__init__(session_factory)
            self.loads = 0

        def load_daily_bars(self, provider_mode, instrument_ids, start, end):
            self.loads += 1
            if self.loads == 2:
                self.save_daily_bars(
                    provider_mode,
                    pd.DataFrame([_bar(concurrently_filled, target)]),
                )
            return super().load_daily_bars(provider_mode, instrument_ids, start, end)

    cache = FillOnRecheckCache()
    provider = RecordingProvider(
        {(instrument_id, target): _bar(instrument_id, target) for instrument_id in provider_ids}
    )
    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[
            ExactPriceRequirement(instrument_id, target, "adjusted_open")
            for instrument_id in [concurrently_filled, *provider_ids]
        ],
    )

    assert provider.calls == [(provider_ids, target, target)]
    assert result.requested == 3
    assert result.provider_requested == 2
    assert result.provider_batches == 1
    assert result.skipped_after_recheck == 1
    assert result.cache_hits == 0
    assert result.repaired == 3
    assert result.unresolved == []
    assert result.repaired + len(result.unresolved) == result.requested - result.cache_hits


def test_exact_repair_partial_recheck_for_same_instrument_requests_once(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'exact-multifield-recheck.db'}"
    initialize_database(database_url)
    session_factory = create_session_factory(database_url)
    target = date(2026, 7, 2)
    instrument_id = "CN:000001"

    class FillOneFieldOnRecheckCache(MarketDataCacheRepository):
        def __init__(self):
            super().__init__(session_factory)
            self.loads = 0

        def load_daily_bars(self, provider_mode, instrument_ids, start, end):
            self.loads += 1
            if self.loads == 2:
                with session_factory() as session:
                    session.add(
                        MarketBarCacheRow(
                            provider_mode=provider_mode,
                            instrument_id=instrument_id,
                            trade_date=target,
                            source_provider="concurrent_scan",
                            open=Decimal("10"),
                            high=Decimal("11"),
                            low=Decimal("9"),
                            close=Decimal("10"),
                            volume=Decimal("100"),
                            turnover=Decimal("1000"),
                            adjusted_open=None,
                            adjusted_high=None,
                            adjusted_low=None,
                            adjusted_close=Decimal("10"),
                            adjustment_factor=None,
                            adjustment_type=None,
                        )
                    )
                    session.commit()
            return super().load_daily_bars(provider_mode, instrument_ids, start, end)

    cache = FillOneFieldOnRecheckCache()
    provider = RecordingProvider({(instrument_id, target): _bar(instrument_id, target)})
    result = repair_exact_daily_prices(
        cache,
        provider_mode="fixture",
        market_provider=provider,
        requirements=[
            ExactPriceRequirement(instrument_id, target, "adjusted_open"),
            ExactPriceRequirement(instrument_id, target, "adjusted_close"),
        ],
    )

    assert provider.calls == [([instrument_id], target, target)]
    assert result.requested == 2
    assert result.cache_hits == 0
    assert result.skipped_after_recheck == 1
    assert result.provider_requested == 1
    assert result.provider_batches == 1
    assert result.repaired == 2
    assert result.unresolved == []
    assert result.repaired + len(result.unresolved) == result.requested - result.cache_hits
    bars = cache.load_daily_bars("fixture", [instrument_id], target, target)
    assert float(bars.iloc[0]["adjusted_open"]) == 10.0
    assert float(bars.iloc[0]["adjusted_close"]) == 10.0


@pytest.mark.parametrize("error_day", [date(2026, 7, 2), date(2026, 7, 3)])
def test_soft_provider_errors_stay_with_their_call_and_instrument(tmp_path, error_day):
    database_url = f"sqlite:///{tmp_path / 'soft-errors.db'}"
    initialize_database(database_url)
    cache = MarketDataCacheRepository(create_session_factory(database_url))

    class SoftFailureProvider(RecordingProvider):
        def get_historical_daily_bars(self, instrument_ids, start, end):
            self.last_errors = ["CN:000001: baostock login timed out"] if start == error_day else []
            return super().get_historical_daily_bars(instrument_ids, start, end)

    provider = SoftFailureProvider({})
    result = repair_exact_daily_prices(
        cache, provider_mode="fixture", market_provider=provider,
        requirements=[ExactPriceRequirement(instrument, day, "adjusted_open")
                      for instrument in ("CN:000001", "CN:000002")
                      for day in (date(2026, 7, 2), date(2026, 7, 3))],
    )
    assert result.reasons == {"provider_error": 1, "provider_no_row": 3}
    assert result.error_details == [f"{error_day}:CN:000001: baostock login timed out"]


def test_bounded_repair_eventually_attempts_every_gap_across_failing_cycles(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'fair-retry.db'}"
    initialize_database(database_url)
    cache = MarketDataCacheRepository(create_session_factory(database_url))
    requirements = [ExactPriceRequirement(f"CN:{i:06d}", date(2026, 7, 2), "adjusted_open")
                    for i in range(65)]
    provider = RecordingProvider({})
    for _ in range(2):
        result = repair_exact_daily_prices(
            cache, provider_mode="fixture", market_provider=provider,
            requirements=requirements, batch_size=5,
            work_budget=ExactPriceRepairBudget.bounded(max_provider_batches=8, wall_clock_seconds=60),
        )
        assert result.provider_batches == 8
        assert result.deferred_by_budget == 25
        assert result.repaired == 0
    assert {instrument for batch, _, _ in provider.calls for instrument in batch} == {
        requirement.instrument_id for requirement in requirements
    }
    assert provider.calls[8][0][0] == "CN:000040"
    # A new field/date protocol must not reuse an unrelated offset.
    changed = [ExactPriceRequirement(r.instrument_id, date(2026, 7, 3), r.field) for r in requirements]
    repair_exact_daily_prices(
        cache, provider_mode="fixture", market_provider=provider, requirements=changed, batch_size=5,
        work_budget=ExactPriceRepairBudget.bounded(max_provider_batches=1, wall_clock_seconds=60),
    )
    assert provider.calls[-1][0][0] == "CN:000000"


def test_cursor_claims_are_atomic_and_expire_old_scopes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from qagent.research.shadow_price_repair import _claim_repair_cursor
    from qagent.storage.tables import ExactPriceRepairCursorRow, utc_now

    database_url = f"sqlite:///{tmp_path / 'cursor.db'}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    cache = MarketDataCacheRepository(factory)
    with factory() as session:
        session.add(ExactPriceRepairCursorRow(scope_hash="stale", next_batch=5,
                                            updated_at=utc_now() - timedelta(days=31)))
        session.commit()
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: _claim_repair_cursor(cache, "shared", 20), range(8)))
    assert sorted(claims) == list(range(8))
    with factory() as session:
        assert session.get(ExactPriceRepairCursorRow, "stale") is None


def test_cursor_failure_keeps_provider_budget_bounded(tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from qagent.research import shadow_price_repair

    database_url = f"sqlite:///{tmp_path / 'cursor-failure.db'}"
    initialize_database(database_url)
    cache = MarketDataCacheRepository(create_session_factory(database_url))
    def fail(*args):
        raise SQLAlchemyError("missing table")
    monkeypatch.setattr(shadow_price_repair, "_claim_repair_cursor", fail)
    provider = RecordingProvider({})
    result = repair_exact_daily_prices(
        cache, provider_mode="fixture", market_provider=provider,
        requirements=[ExactPriceRequirement(f"CN:{i:06d}", date(2026, 7, 2), "adjusted_open")
                      for i in range(3)], batch_size=1,
        work_budget=ExactPriceRepairBudget.bounded(max_provider_batches=1, wall_clock_seconds=60),
    )
    assert result.provider_batches == 1
    assert result.deferred_by_budget == 2
    assert result.error_details == ["research repair cursor unavailable"]


def test_failed_repair_writes_only_research_cursor_metadata(tmp_path):
    from sqlalchemy import event

    database_url = f"sqlite:///{tmp_path / 'write-boundary.db'}"
    initialize_database(database_url)
    factory = create_session_factory(database_url)
    mutations = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            mutations.append(statement)
    event.listen(factory.kw["bind"], "before_cursor_execute", observe)
    repair_exact_daily_prices(
        MarketDataCacheRepository(factory), provider_mode="fixture", market_provider=RecordingProvider({}),
        requirements=[ExactPriceRequirement("CN:000001", date(2026, 7, 2), "adjusted_open")],
        work_budget=ExactPriceRepairBudget.bounded(max_provider_batches=8, wall_clock_seconds=60),
    )
    assert mutations
    assert all("exact_price_repair_cursors" in statement for statement in mutations)


def test_error_details_redact_provider_credentials():
    from qagent.research.shadow_price_repair import _safe_error

    message = _safe_error("https://user:passwd@example.test/bars?api_key=hidden Bearer credential token=secret")
    for sensitive in ("passwd", "hidden", "credential", "=secret"):
        assert sensitive not in message
    assert "[redacted]" in message


def test_accumulated_previous_symbol_error_does_not_contaminate_next_batch(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'accumulated-errors.db'}"
    initialize_database(database_url)
    provider = RecordingProvider({})
    provider.last_errors = ["CN:000001: transport failed"]
    result = repair_exact_daily_prices(
        MarketDataCacheRepository(create_session_factory(database_url)),
        provider_mode="fixture", market_provider=provider, batch_size=1,
        requirements=[ExactPriceRequirement(i, date(2026, 7, 2), "adjusted_open")
                      for i in ("CN:000001", "CN:000002")],
    )
    assert result.reasons == {"provider_error": 1, "provider_no_row": 1}
    assert len(result.error_details) == 1


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
