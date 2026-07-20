from datetime import date, timedelta

import pandas as pd

from qagent.jobs.daily_scan import _research_price_bars, run_daily_scan
from qagent.jobs import full_market
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.db import create_session_factory, initialize_database
from qagent.jobs.daily_scan import ScanItem
from qagent.market.sector_strength import build_sector_strength
from qagent.storage.repository import QagentRepository


def test_daily_scan_returns_cards_for_fixture_universe():
    result = run_daily_scan(
        instrument_ids=["US:TEST", "CN:000001"],
        provider=FixtureMarketDataProvider(),
    )
    assert len(result.cards) >= 1
    assert result.data_health["provider"] == "fixture"
    assert result.data_health["scanned"] == "2"
    assert result.data_health["cards"] == str(len(result.cards))
    assert len(result.items) == 2
    assert result.items[0].instrument_id in {"US:TEST", "CN:000001"}
    assert {item.status for item in result.items} == {"setup_ready"}
    assert result.strategy_health
    assert any(item.strategy_id == "breakout_volume_confirmation" for item in result.strategy_health)
    assert result.items[0].strategies_passed >= 1
    assert result.items[0].strategies_missing_data >= 1
    assert result.cards[0].rank_score >= result.cards[-1].rank_score
    assert result.cards[0].rank_reasons
    assert result.cards[0].probability_forecast is not None
    assert 0 <= result.cards[0].probability_forecast.win_probability_10d <= 1
    assert result.cards[0].probability_forecast.evidence
    assert result.data_health["probability_calibration_cards"] == str(len(result.cards))
    assert "strategy_auto_weighting_applied" in result.data_health
    assert result.data_health["a_share_data_readiness_score"]
    assert result.data_health["a_share_price_limit"] == "ready"
    assert result.data_health["a_share_liquidity"] in {"ready", "partial", "missing"}
    assert result.data_health["a_share_announcements"] in {"ready", "partial", "missing"}
    assert result.signal_monitor is not None
    assert result.signal_monitor.total == len(result.cards)
    assert result.signal_monitor.action_queue
    assert result.data_health["signal_monitor_total"] == str(len(result.cards))
    assert result.decision_quality_center is not None
    assert result.decision_quality_center.explanation_cards
    assert result.data_health["decision_quality_cards"] == str(len(result.cards))
    assert result.operational_readiness_center is not None
    assert len(result.operational_readiness_center.checks) == 6
    assert result.operational_readiness_center.user_questions
    assert result.data_health["operational_readiness_checks"] == "6"


def test_research_bars_use_adjusted_prices_without_changing_raw_frame():
    raw = pd.DataFrame(
        [
            {
                "trade_date": date(2026, 1, 5),
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "adjusted_open": 5.0,
                "adjusted_high": 5.25,
                "adjusted_low": 4.9,
                "adjusted_close": 5.1,
                "adjustment_factor": 0.5,
            },
            {
                "trade_date": date(2026, 1, 6),
                "open": 10.3,
                "high": 10.6,
                "low": 10.1,
                "close": 10.4,
                "adjusted_close": None,
                "adjustment_factor": None,
            },
        ]
    )

    research = _research_price_bars(raw)

    assert research["trade_date"].tolist() == [date(2026, 1, 5)]
    assert research.iloc[0]["close"] == 5.1
    assert research.iloc[0]["open"] == 5.0
    assert raw.iloc[0]["close"] == 10.2


def test_research_bars_derive_adjusted_ohlc_from_legacy_factor():
    raw = pd.DataFrame(
        [
            {
                "trade_date": date(2026, 1, 5),
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "adjusted_open": None,
                "adjusted_high": None,
                "adjusted_low": None,
                "adjusted_close": 5.1,
                "adjustment_factor": 0.5,
            }
        ]
    )

    research = _research_price_bars(raw)

    assert research.iloc[0]["open"] == 5.0
    assert research.iloc[0]["high"] == 5.25
    assert research.iloc[0]["low"] == 4.9
    assert research.iloc[0]["close"] == 5.1


def test_daily_scan_adds_benchmark_comparison_and_quick_brief():
    result = run_daily_scan(
        instrument_ids=["CN:000001"],
        provider=BenchmarkFixtureProvider(),
        mode="fixture",
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
    )

    card = result.cards[0]
    assert card.recommendation_brief is not None
    assert card.recommendation_brief.why
    assert card.recommendation_brief.buy_point
    assert card.recommendation_brief.stop_loss
    assert card.recommendation_brief.target
    assert card.recommendation_brief.history_odds
    assert card.recommendation_brief.current_verdict in {"适合买", "等待买点", "不适合"}
    assert card.benchmark_comparison is not None
    assert {item.name for item in card.benchmark_comparison.items} == {
        "沪深300",
        "中证500",
        "创业板指",
        "科创50",
    }
    assert card.benchmark_comparison.primary.excess_return_pct is not None
    assert result.data_health["benchmark_comparison_cards"] == str(len(result.cards))


def test_full_market_batch_job_caches_strategy_health_and_explanations(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'full-batch.db'}")
    initialize_database()
    repo = QagentRepository(create_session_factory())
    job = repo.create_full_market_scan_job(
        provider="fixture",
        symbols=["US:TEST", "CN:000001"],
        batch_size=1,
        include_etfs=True,
        sync_if_empty=False,
    )
    monkeypatch.setattr(
        full_market,
        "build_market_data_provider",
        lambda provider: FixtureMarketDataProvider(),
    )

    full_market.run_full_market_batch_scan_job(job.job_id, top_cards_limit=5)

    cached = repo.get_recent_scan_result_cache(
        cache_key=full_market.full_market_batch_cache_key("fixture", True),
        max_age=timedelta(minutes=60),
    )

    assert cached is not None
    assert cached.payload["factor_rankings"]
    assert len(cached.payload["factor_rankings"]) == len(cached.payload["cards"])
    assert cached.payload["data_health"]["factor_ranking_scope"] == "full_card_universe"
    assert cached.payload["data_health"]["factor_ranking_normalization"] == "global_second_pass"
    assert cached.payload["data_health"]["feature_set_version"]
    assert len(cached.payload["data_health"]["feature_universe_digest"]) == 64
    assert len(cached.payload["data_health"]["feature_input_digest"]) == 64
    assert cached.payload["feature_snapshot"]["feature_set_version"]
    assert cached.payload["feature_drift"]["status"] == "insufficient"
    assert cached.payload["feature_drift"]["auto_adjust_weights"] is False
    assert cached.payload["a_share_market_state"]["state"] in {
        "unknown",
        "stress",
        "weak",
        "mixed",
        "constructive",
        "strong",
    }
    assert cached.payload["data_health"]["a_share_market_state"]
    assert cached.payload["portfolio_plan"]["constraint_policy"]["market_state"] == (
        cached.payload["a_share_market_state"]["state"]
    )
    assert (
        cached.payload["data_health"]["dynamic_calibration_merge_policy"]
        == "preserve_batch_result_without_reapply"
    )
    assert cached.payload["strategy_health"]
    assert any(item["curve"] for item in cached.payload["strategy_health"])
    assert cached.payload["cards"][0]["confidence_explanation"]
    assert cached.payload["cards"][0]["execution_plan"]
    assert cached.payload["cards"][0]["probability_forecast"]
    assert cached.payload["signal_monitor"]["total"] == len(cached.payload["cards"])
    assert cached.payload["signal_monitor"]["action_queue"]
    assert cached.payload["decision_quality_center"]["explanation_cards"]
    assert cached.payload["data_health"]["decision_quality_cards"] == str(len(cached.payload["cards"]))
    assert cached.payload["operational_readiness_center"]["checks"]
    assert cached.payload["operational_readiness_center"]["user_questions"]
    assert cached.payload["data_health"]["operational_readiness_checks"] == "6"
    assert cached.payload["data_health"]["probability_calibration_cards"] == str(len(cached.payload["cards"]))
    assert cached.payload["sector_strength"]
    assert cached.payload["data_health"]["sector_strength"] == str(len(cached.payload["sector_strength"]))
    assert cached.payload["data_health"]["full_market_snapshot_items"] == str(
        len(cached.payload["cards"])
    )

    runs = repo.list_scan_runs(provider="fixture", limit=10)
    assert len(runs) == 1
    assert runs[0].mode == "full_market_batch"
    assert runs[0].cards == len(cached.payload["cards"])
    snapshots = repo.list_opportunity_snapshots(
        provider="fixture",
        limit=10,
        require_signal_date=True,
    )
    assert len(snapshots) == len(cached.payload["cards"])
    assert all(snapshot.latest_close is not None for snapshot in snapshots)


def test_full_market_batch_job_resumes_from_persisted_batch_checkpoints(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'checkpoint.db'}")
    initialize_database()
    repo = QagentRepository(create_session_factory())
    job = repo.create_full_market_scan_job(
        provider="fixture",
        symbols=["US:TEST", "CN:000001"],
        batch_size=1,
        include_etfs=True,
        sync_if_empty=False,
    )
    monkeypatch.setattr(
        full_market,
        "build_market_data_provider",
        lambda provider: FixtureMarketDataProvider(),
    )
    full_market.run_full_market_batch_scan_job(job.job_id, top_cards_limit=5)

    def fail_if_rescanned(*args, **kwargs):
        raise AssertionError("completed checkpoint batches must not be rescanned")

    monkeypatch.setattr(full_market, "run_daily_scan", fail_if_rescanned)
    repo.update_full_market_scan_job(job.job_id, status="running")

    full_market.run_full_market_batch_scan_job(job.job_id, top_cards_limit=5)

    restored = repo.get_full_market_scan_job(job.job_id)
    assert restored is not None
    assert restored.status == "succeeded"
    assert restored.scanned_symbols == 2
    assert restored.completed_batches == 2
    assert restored.data_health["full_market_restart_recovery"] == "batch_checkpoint_resume"
    assert restored.data_health["full_market_checkpoint_batches_restored"] == "2"


def test_full_market_batch_job_caches_rejected_items_with_remediation(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'full-batch-rejected.db'}")
    initialize_database()
    repo = QagentRepository(create_session_factory())
    job = repo.create_full_market_scan_job(
        provider="fixture",
        symbols=["US:TEST", "US:UNKNOWN"],
        batch_size=1,
        include_etfs=True,
        sync_if_empty=False,
    )
    monkeypatch.setattr(
        full_market,
        "build_market_data_provider",
        lambda provider: FixtureMarketDataProvider(),
    )

    full_market.run_full_market_batch_scan_job(job.job_id, top_cards_limit=5)

    cached = repo.get_recent_scan_result_cache(
        cache_key=full_market.full_market_batch_cache_key("fixture", True),
        max_age=timedelta(minutes=60),
    )

    assert cached is not None
    rejected = [item for item in cached.payload["items"] if item["status"] in {"no_data", "no_setup", "data_error"}]
    assert rejected
    assert rejected[0]["rejection_category"] in {"data_missing", "weak_signal", "execution_blocked", "scan_error"}
    assert rejected[0]["remediation"]
    assert cached.payload["data_health"]["full_market_rejected_items"] == str(len(rejected))


def test_full_market_batch_compares_same_version_feature_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("QAGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'feature-drift.db'}")
    initialize_database()
    repo = QagentRepository(create_session_factory())
    monkeypatch.setattr(
        full_market,
        "build_market_data_provider",
        lambda provider: FixtureMarketDataProvider(),
    )

    for _ in range(2):
        job = repo.create_full_market_scan_job(
            provider="fixture",
            symbols=["US:TEST", "CN:000001"],
            batch_size=1,
            include_etfs=True,
            sync_if_empty=False,
        )
        full_market.run_full_market_batch_scan_job(job.job_id, top_cards_limit=5)

    cached = repo.get_recent_scan_result_cache(
        cache_key=full_market.full_market_batch_cache_key("fixture", True),
        max_age=timedelta(minutes=60),
    )

    assert cached is not None
    drift = cached.payload["feature_drift"]
    assert drift["reference_version"] == drift["current_version"]
    assert drift["status"] in {"stable", "watch", "insufficient"}
    assert drift["auto_adjust_weights"] is False
    assert cached.payload["data_health"]["feature_drift_auto_weight_change"] == "false"


def test_daily_scan_promotes_pead_when_earnings_fixture_is_available():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
    )

    assert len(result.cards) == 1
    assert result.cards[0].primary_strategy_id == "pead_earnings_drift"
    assert result.cards[0].entry_plan.entry_type == "pead"
    assert result.data_health["strategy_data_provider"] == "fixture_strategy_data"


def test_daily_scan_respects_caller_date_window():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        start=date(2026, 1, 1),
        end=date(2026, 1, 30),
    )

    assert result.items[0].latest_trade_date == date(2026, 1, 30)
    assert result.items[0].latest_close == "57.35"


class EmptyProviderWithErrors:
    name = "empty"
    last_errors = ["US:MISS: source unavailable"]

    def get_daily_bars(self, instrument_ids, start, end):
        import pandas as pd

        return pd.DataFrame()

    def get_snapshot(self, instrument_ids):
        import pandas as pd

        return pd.DataFrame()


def test_daily_scan_surfaces_provider_errors():
    result = run_daily_scan(["US:MISS"], provider=EmptyProviderWithErrors(), mode="free")

    assert result.cards == []
    assert result.data_health["errors"] == "US:MISS: source unavailable"
    assert result.items[0].instrument_id == "US:MISS"
    assert result.items[0].status == "no_data"
    assert result.items[0].reason == "No daily bars returned by provider."
    assert result.items[0].rejection_category == "data_missing"
    assert result.items[0].remediation


def test_sector_strength_uses_full_scan_items_for_theme_breadth():
    items = [
        _scan_item("CN:688981", "watch"),
        _scan_item("CN:688008", "watch"),
        _scan_item("CN:000001", "watch"),
    ]
    bars_by_instrument = {
        item.instrument_id: _theme_bars(item.instrument_id, direction=1 if item.instrument_id != "CN:000001" else -1)
        for item in items
    }

    sectors = build_sector_strength([], bars_by_instrument, items=items)

    by_name = {item.industry: item for item in sectors}
    assert "半导体" in by_name
    assert "存储芯片" in by_name
    assert by_name["半导体"].category == "theme"
    assert by_name["半导体"].sample_count >= 2
    assert by_name["半导体"].score > by_name["银行"].score


class ProviderThatRaisesForOneSymbol:
    name = "partial_failure"
    last_errors: list[str] = []

    def __init__(self):
        self.fixture = FixtureMarketDataProvider()

    def get_daily_bars(self, instrument_ids, start, end):
        if instrument_ids == ["CN:BAD"]:
            raise ValueError("bad vendor payload")
        if instrument_ids == ["US:TEST"]:
            return self.fixture.get_daily_bars(instrument_ids, start, end)
        return pd.DataFrame()

    def get_snapshot(self, instrument_ids):
        return pd.DataFrame()


def test_daily_scan_continues_after_single_instrument_error():
    result = run_daily_scan(
        ["CN:BAD", "US:TEST"],
        provider=ProviderThatRaisesForOneSymbol(),
        mode="free",
    )

    by_id = {item.instrument_id: item for item in result.items}
    assert by_id["CN:BAD"].status == "data_error"
    assert "bad vendor payload" in by_id["CN:BAD"].reason
    assert by_id["CN:BAD"].rejection_category == "scan_error"
    assert by_id["US:TEST"].status == "setup_ready"
    assert len(result.cards) == 1
    assert result.data_health["scan_errors"] == "1"


class BenchmarkFixtureProvider(FixtureMarketDataProvider):
    def get_daily_bars(self, instrument_ids, start, end):
        benchmark_ids = {
            "CN:000300.IDX": 0.04,
            "CN:000905.IDX": 0.02,
            "CN:399006.IDX": -0.01,
            "CN:000688.IDX": 0.08,
        }
        if all(instrument_id in benchmark_ids for instrument_id in instrument_ids):
            frames = [
                _benchmark_bars(instrument_id, total_return)
                for instrument_id, total_return in benchmark_ids.items()
                if instrument_id in instrument_ids
            ]
            return pd.concat(frames, ignore_index=True)
        return super().get_daily_bars(instrument_ids, start, end)


def _scan_item(instrument_id: str, status: str) -> ScanItem:
    return ScanItem(
        instrument_id=instrument_id,
        instrument_label=None,
        status=status,
        reason="test",
        bars=80,
        signals=0,
        latest_close="10.00",
        latest_trade_date=date(2026, 3, 20),
    )


def _theme_bars(instrument_id: str, direction: int = 1) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(80):
        close = 10 + direction * index * 0.04
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close - 0.02,
                "high": close + 0.08,
                "low": close - 0.08,
                "close": close,
                "volume": 1_500_000 + index * 2_000,
                "provider": "fixture",
            }
        )
    return pd.DataFrame(rows)


class StrategyDataProviderWithRecords:
    name = "strategy_records"
    last_errors = ["fmp: rate limited"]

    def get_earnings_events(self, instrument_ids, start, end):
        return []

    def get_filings(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import FilingEvent

        return [
            FilingEvent(
                instrument_id=instrument_ids[0],
                form="10-Q",
                filing_date=date(2026, 1, 31),
                accession_number="0001",
                provider="sec_edgar",
            )
        ]

    def get_announcements(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import AnnouncementEvent

        return [
            AnnouncementEvent(
                instrument_id=instrument_ids[0],
                title="2025年度报告",
                published_at=date(2026, 1, 31),
                provider="cninfo",
            )
        ]

    def get_fundamentals(self, instrument_ids, start, end):
        return []

    def get_analyst_insights(self, instrument_ids, start, end):
        return []


def test_daily_scan_surfaces_strategy_data_counts_and_errors():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        strategy_data_provider=StrategyDataProviderWithRecords(),
    )

    assert result.data_health["strategy_filings"] == "1"
    assert result.data_health["strategy_announcements"] == "1"
    assert result.data_health["strategy_data_errors"] == "fmp: rate limited"


def _benchmark_bars(instrument_id: str, total_return: float) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(70):
        close = 100 * (1 + total_return * index / 69)
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": start + timedelta(days=index),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 5_000_000,
                "provider": "benchmark_fixture",
            }
        )
    return pd.DataFrame(rows)

class StrategyDataProviderWithOwnershipFilings:
    name = "ownership_records"
    last_errors: list[str] = []

    def get_earnings_events(self, instrument_ids, start, end):
        return []

    def get_filings(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import FilingEvent

        return [
            FilingEvent(
                instrument_id=instrument_ids[0],
                form="4",
                filing_date=date(2026, 3, 20),
                accession_number="0001",
                provider="sec_edgar",
            ),
            FilingEvent(
                instrument_id=instrument_ids[0],
                form="13F-HR",
                filing_date=date(2026, 3, 21),
                accession_number="0002",
                provider="sec_edgar",
            ),
        ]

    def get_announcements(self, instrument_ids, start, end):
        return []

    def get_fundamentals(self, instrument_ids, start, end):
        return []

    def get_analyst_insights(self, instrument_ids, start, end):
        return []


def test_daily_scan_passes_ownership_filings_into_strategy_stack():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        strategy_data_provider=StrategyDataProviderWithOwnershipFilings(),
    )

    strategies = {item.strategy_id: item for item in result.cards[0].strategy_evaluations}
    assert strategies["insider_institutional_confirmation"].status == "passed"
    assert result.data_health["strategy_filings"] == "2"


class StrategyDataProviderWithFreeFundamentals:
    name = "free_fundamental_records"
    last_errors: list[str] = []

    def get_earnings_events(self, instrument_ids, start, end):
        return []

    def get_filings(self, instrument_ids, start, end):
        return []

    def get_announcements(self, instrument_ids, start, end):
        return []

    def get_fundamentals(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import FundamentalSnapshot

        return [
            FundamentalSnapshot(
                instrument_id=instrument_ids[0],
                as_of_date=date(2026, 3, 31),
                revenue_growth_pct=32.5,
                earnings_growth_pct=41.2,
                gross_margin_pct=68,
                operating_margin_pct=24.5,
                net_margin_pct=18,
                return_on_equity_pct=29,
                market_cap=8_500_000_000,
                pe_ratio=34,
                forward_pe=28,
                peg_ratio=0.95,
                price_to_sales=7.5,
                provider="fixture_strategy_data",
            )
        ]

    def get_analyst_insights(self, instrument_ids, start, end):
        from datetime import date

        from qagent.strategy_data.models import AnalystInsight

        return [
            AnalystInsight(
                instrument_id=instrument_ids[0],
                as_of_date=date(2026, 3, 31),
                revision_date=date(2026, 3, 31),
                current_eps_estimate=1.35,
                prior_eps_estimate=1.1,
                current_revenue_estimate=152_000_000,
                prior_revenue_estimate=140_000_000,
                target_price=64,
                prior_target_price=54,
                current_price=50,
                strong_buy_count=6,
                buy_count=14,
                hold_count=4,
                sell_count=1,
                provider="fixture_strategy_data",
            )
        ]


def test_daily_scan_passes_free_fundamentals_into_strategy_stack():
    result = run_daily_scan(
        instrument_ids=["US:TEST"],
        provider=FixtureMarketDataProvider(),
        strategy_data_provider=StrategyDataProviderWithFreeFundamentals(),
    )

    assert result.data_health["strategy_fundamentals"] == "1"
    assert result.data_health["strategy_analyst_insights"] == "1"
    card = result.cards[0]
    strategies = {item.strategy_id: item for item in card.strategy_evaluations}
    assert strategies["tam_adj_peg_growth"].status == "passed"
    assert strategies["bayesian_intrinsic_growth"].status == "passed"
    assert strategies["analyst_revision_momentum"].status == "passed"
