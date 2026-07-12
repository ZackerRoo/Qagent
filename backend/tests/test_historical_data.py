from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from qagent.data_management import run_historical_backfill
from qagent.db import Base, create_db_engine, create_session_factory
from qagent.historical_evidence.models import (
    HistoricalEvidenceBundle,
    HistoricalIndexMembership,
    HistoricalIndexSnapshot,
    HistoricalIndustrySnapshot,
    HistoricalInstrumentProfile,
    HistoricalTradabilityPoint,
)
from qagent.historical_evidence.providers import historical_snapshot_dates
from qagent.market.calendars import trading_sessions_in_range
from qagent.storage.market_cache import MarketDataCacheRepository
from qagent.storage.repository import QagentRepository
from qagent.strategy_data.models import FundamentalSnapshot
from qagent.strategy_data.providers import BaseStrategyDataProvider


def make_repositories(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'historical-data.db'}"
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(database_url)
    return QagentRepository(session_factory), MarketDataCacheRepository(session_factory)


class AdjustedHistoryProvider:
    name = "adjusted_fixture"
    last_errors: list[str] = []

    def __init__(self):
        self.calls = 0

    def get_daily_bars(self, instrument_ids, start, end):
        self.calls += 1
        sessions = trading_sessions_in_range(start, end)
        return pd.DataFrame(
            [
                {
                    "instrument_id": instrument_id,
                    "trade_date": trade_date,
                    "open": 10 + index,
                    "high": 10.5 + index,
                    "low": 9.5 + index,
                    "close": 10.2 + index,
                    "volume": 1_000_000,
                    "provider": "adjusted_fixture_qfq",
                    "adjusted_close": 10.2 + index,
                    "adjustment_factor": 1.0,
                    "adjustment_type": "qfq",
                }
                for instrument_id in instrument_ids
                for index, trade_date in enumerate(sessions)
            ]
        )


class FundamentalHistoryProvider(BaseStrategyDataProvider):
    name = "fundamental_fixture"

    def get_fundamentals(self, instrument_ids, start, end):
        return [
            FundamentalSnapshot(
                instrument_id=instrument_id,
                as_of_date=end,
                market_cap=Decimal("10000000000"),
                pe_ratio=Decimal("12.0"),
                return_on_equity_pct=Decimal("15.0"),
                provider=self.name,
            )
            for instrument_id in instrument_ids
        ]


class FailedHistoryProvider:
    name = "failed_fixture"
    last_errors = ["upstream disconnected"]

    def get_daily_bars(self, instrument_ids, start, end):
        return pd.DataFrame()


class FlakyHistoryProvider(AdjustedHistoryProvider):
    def get_daily_bars(self, instrument_ids, start, end):
        self.calls += 1
        if self.calls == 1:
            self.last_errors = ["temporary upstream disconnect"]
            return pd.DataFrame()
        self.calls -= 1
        self.last_errors = []
        return super().get_daily_bars(instrument_ids, start, end)


class PartialHistoryProvider(AdjustedHistoryProvider):
    def get_daily_bars(self, instrument_ids, start, end):
        return super().get_daily_bars(instrument_ids, start, end).head(2)


class PartialThenCompleteHistoryProvider(AdjustedHistoryProvider):
    def get_daily_bars(self, instrument_ids, start, end):
        frame = super().get_daily_bars(instrument_ids, start, end)
        return frame.head(2) if self.calls == 1 else frame


class UnexpectedFundamentalHistoryProvider(BaseStrategyDataProvider):
    name = "unexpected_fundamental_fixture"

    def get_fundamentals(self, instrument_ids, start, end):
        raise AssertionError("complete historical fundamentals must be reused")


class CompleteHistoricalEvidenceProvider:
    name = "evidence_fixture"
    last_errors: list[str] = []

    def get_evidence(self, instrument_ids, start, end):
        sessions = trading_sessions_in_range(start, end)
        snapshot_dates = historical_snapshot_dates(start, end)
        return HistoricalEvidenceBundle(
            tradability=[
                HistoricalTradabilityPoint(
                    instrument_id=instrument_id,
                    trade_date=trade_date,
                    trading_status=(
                        "suspended"
                        if instrument_id == "CN:000001" and index == 1
                        else "trading"
                    ),
                    is_st=False,
                    provider=self.name,
                )
                for instrument_id in instrument_ids
                for index, trade_date in enumerate(sessions)
            ],
            profiles=[
                HistoricalInstrumentProfile(
                    instrument_id=instrument_id,
                    name="历史样本",
                    snapshot_date=end,
                    listing_date=date(1991, 4, 3),
                    security_type="1",
                    listing_status="1",
                    provider=self.name,
                )
                for instrument_id in instrument_ids
            ],
            industries=[
                HistoricalIndustrySnapshot(
                    instrument_id=instrument_id,
                    snapshot_date=snapshot_date,
                    industry="银行",
                    classification="申万一级行业",
                    provider=self.name,
                )
                for instrument_id in instrument_ids
                for snapshot_date in snapshot_dates
            ],
            index_snapshots=[
                HistoricalIndexSnapshot(
                    index_id=index_id,
                    snapshot_date=snapshot_date,
                    status="ready",
                    member_count=1 if index_id == "CN:000300.IDX" else 0,
                    provider=self.name,
                )
                for index_id in [
                    "CN:000016.IDX",
                    "CN:000300.IDX",
                    "CN:000905.IDX",
                ]
                for snapshot_date in snapshot_dates
            ],
            index_memberships=[
                HistoricalIndexMembership(
                    index_id="CN:000300.IDX",
                    snapshot_date=snapshot_date,
                    instrument_id=instrument_id,
                    provider=self.name,
                )
                for instrument_id in instrument_ids
                for snapshot_date in snapshot_dates
            ],
        )


class ErrorHistoricalEvidenceProvider:
    name = "evidence_error_fixture"

    def get_evidence(self, instrument_ids, start, end):
        return HistoricalEvidenceBundle(errors=["index snapshot unavailable"])


class ReferenceOnlyHistoricalEvidenceProvider:
    name = "reference_only_fixture"

    def get_evidence(self, instrument_ids, start, end):
        return HistoricalEvidenceBundle(
            profiles=[
                HistoricalInstrumentProfile(
                    instrument_id=instrument_id,
                    name="历史ETF样本",
                    snapshot_date=end,
                    listing_date=date(2012, 1, 1),
                    security_type="5",
                    listing_status="1",
                    provider=self.name,
                )
                for instrument_id in instrument_ids
            ]
        )


class InspectingHistoricalEvidenceProvider(CompleteHistoricalEvidenceProvider):
    def __init__(self, repo, job_id):
        self.repo = repo
        self.job_id = job_id

    def get_evidence(self, instrument_ids, start, end):
        job = self.repo.get_historical_backfill_job(self.job_id)
        assert job is not None
        assert job.data_health["backfill_phase"] == "historical_evidence"
        return super().get_evidence(instrument_ids, start, end)


class UnexpectedHistoricalEvidenceProvider:
    name = "unexpected_evidence_fixture"

    def get_evidence(self, instrument_ids, start, end):
        raise AssertionError("complete historical evidence must be reused")


def test_historical_backfill_is_idempotent_and_emits_coverage_manifest(tmp_path):
    repo, cache = make_repositories(tmp_path)
    repo.replace_tradable_instruments(
        [
            SimpleNamespace(
                instrument_id="CN:000001",
                symbol="000001",
                name="平安银行",
                label="平安银行 000001.SZ",
                asset_type="stock",
                exchange="SZ",
                source="fixture_catalog",
            )
        ]
    )
    provider = AdjustedHistoryProvider()

    first = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=provider,
        strategy_provider=FundamentalHistoryProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )
    second = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=provider,
        strategy_provider=FundamentalHistoryProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )

    item = first.manifest.instruments[0]
    assert first.job.status == "succeeded"
    assert first.job.rows_written == item.bar_rows
    assert item.bar_coverage_ratio >= 0.95
    assert item.adjustment_coverage_ratio == 1.0
    assert item.fundamental_rows == 1
    assert item.universe_snapshot_rows == 1
    assert item.status == "partial"
    assert "historical_universe_incomplete" in item.issues
    assert first.manifest.summary.ready_instruments == 0
    assert first.manifest.summary.universe_ready_instruments == 0
    assert second.job.status == "succeeded"
    assert second.job.rows_written == 0
    assert second.job.data_health["backfill_cache_reused"] == "1"
    assert provider.calls == 1


def test_historical_backfill_preserves_underlying_provider_errors(tmp_path):
    repo, cache = make_repositories(tmp_path)

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=SimpleNamespace(
            provider=FailedHistoryProvider(),
            last_errors=[],
        ),
        strategy_provider=None,
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )

    assert result.job.status == "succeeded_with_errors"
    assert result.job.failed_symbols == 1
    assert result.job.errors == ["CN:000001: upstream disconnected"]


def test_historical_backfill_retries_transient_provider_errors(tmp_path):
    repo, cache = make_repositories(tmp_path)
    provider = FlakyHistoryProvider()

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=provider,
        strategy_provider=None,
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )

    assert result.job.status == "succeeded"
    assert result.job.succeeded_symbols == 1
    assert provider.calls == 2


def test_historical_backfill_marks_nonempty_partial_price_span_as_failed(tmp_path):
    repo, cache = make_repositories(tmp_path)

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=PartialHistoryProvider(),
        strategy_provider=None,
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )

    assert result.job.status == "succeeded_with_errors"
    assert result.job.succeeded_symbols == 0
    assert result.job.failed_symbols == 1
    assert "price coverage incomplete" in result.job.errors[-1]


def test_historical_backfill_retries_nonempty_partial_price_result(tmp_path):
    repo, cache = make_repositories(tmp_path)
    provider = PartialThenCompleteHistoryProvider()

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=provider,
        strategy_provider=None,
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )

    assert provider.calls == 2
    assert result.job.status == "succeeded"
    assert result.job.succeeded_symbols == 1


def test_historical_backfill_reuses_complete_fundamental_history(tmp_path):
    repo, cache = make_repositories(tmp_path)
    repo.upsert_fundamental_snapshots(
        "free",
        [
            FundamentalSnapshot(
                instrument_id="CN:000001",
                as_of_date=date(2025, 12, 31),
                pe_ratio=Decimal("12"),
                provider="baostock_point_in_time",
            ),
            FundamentalSnapshot(
                instrument_id="CN:000001",
                as_of_date=date(2026, 6, 30),
                pe_ratio=Decimal("11"),
                provider="baostock_point_in_time",
            ),
        ],
    )

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=UnexpectedFundamentalHistoryProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 7, 9),
        universe_as_of=date(2026, 1, 1),
    )

    assert result.job.status == "succeeded"
    assert result.job.fundamental_rows_written == 0
    assert result.job.data_health["backfill_fundamental_cache_reused"] == "1"


def test_historical_backfill_resume_accepts_original_symbol_order(tmp_path):
    repo, cache = make_repositories(tmp_path)
    provider = AdjustedHistoryProvider()
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:600519", "CN:000001"],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=provider,
        strategy_provider=None,
        provider_mode="free",
        instrument_ids=job.symbols,
        start=job.start_date,
        end=job.end_date,
        job_id=job.job_id,
        universe_as_of=date(2026, 1, 9),
    )

    assert result.job.status == "succeeded"
    assert result.job.processed_symbols == 2


def test_historical_backfill_manifest_includes_tradability_and_reference_evidence(tmp_path):
    repo, cache = make_repositories(tmp_path)
    start = date(2026, 1, 1)
    end = date(2026, 1, 9)

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=FundamentalHistoryProvider(),
        historical_evidence_provider=CompleteHistoricalEvidenceProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=start,
        end=end,
        universe_as_of=start,
    )

    item = result.manifest.instruments[0]
    summary = result.manifest.summary
    assert item.tradability_coverage_ratio == 1.0
    assert item.suspended_rows == 1
    assert item.profile_rows == 1
    assert item.industry_rows == 1
    assert item.industries == ["银行"]
    assert item.benchmark_membership_rows == 1
    assert item.benchmark_ids == ["CN:000300.IDX"]
    assert item.universe_snapshot_rows > 0
    assert "historical_universe_incomplete" not in item.issues
    assert summary.tradability_ready_instruments == 1
    assert summary.profile_ready_instruments == 1
    assert summary.industry_ready_instruments == 1
    assert summary.universe_ready_instruments == 1
    assert summary.benchmark_coverage_ratio == 1.0
    assert result.job.data_health["historical_evidence_tradability"] == str(
        item.expected_sessions
    )


def test_historical_backfill_reports_evidence_errors_in_terminal_status(tmp_path):
    repo, cache = make_repositories(tmp_path)

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=None,
        historical_evidence_provider=ErrorHistoricalEvidenceProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 9),
    )

    assert result.job.status == "succeeded_with_errors"
    assert result.job.errors == ["index snapshot unavailable"]
    assert result.job.data_health["backfill_phase"] == "complete"


def test_historical_backfill_exposes_evidence_phase_while_running(tmp_path):
    repo, cache = make_repositories(tmp_path)
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:000001"],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=None,
        historical_evidence_provider=InspectingHistoricalEvidenceProvider(
            repo,
            job.job_id,
        ),
        provider_mode="free",
        instrument_ids=job.symbols,
        start=job.start_date,
        end=job.end_date,
        job_id=job.job_id,
        universe_as_of=date(2026, 1, 9),
    )

    assert result.job.data_health["backfill_phase"] == "complete"


def test_historical_backfill_infers_etf_tradability_from_cached_bars(tmp_path):
    repo, cache = make_repositories(tmp_path)

    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=None,
        historical_evidence_provider=ReferenceOnlyHistoricalEvidenceProvider(),
        provider_mode="free",
        instrument_ids=["CN:510300"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 9),
        universe_as_of=date(2026, 1, 1),
    )

    item = result.manifest.instruments[0]
    assert item.asset_type == "etf"
    assert item.tradability_rows == item.expected_sessions
    assert item.tradability_coverage_ratio == 1.0
    assert "tradability_coverage_below_95pct" not in item.issues


def test_historical_backfill_reuses_complete_historical_evidence(tmp_path):
    repo, cache = make_repositories(tmp_path)
    start = date(2026, 1, 1)
    end = date(2026, 1, 9)

    run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=None,
        historical_evidence_provider=CompleteHistoricalEvidenceProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=start,
        end=end,
        universe_as_of=start,
    )
    revision_before_reuse = repo.replay_evidence("free").current_revision()
    result = run_historical_backfill(
        repo=repo,
        cache=cache,
        provider=AdjustedHistoryProvider(),
        strategy_provider=None,
        historical_evidence_provider=UnexpectedHistoricalEvidenceProvider(),
        provider_mode="free",
        instrument_ids=["CN:000001"],
        start=start,
        end=end,
        universe_as_of=start,
    )

    assert result.job.status == "succeeded"
    assert result.job.data_health["historical_evidence_cache"] == "reused"
    assert repo.replay_evidence("free").current_revision() == revision_before_reuse
