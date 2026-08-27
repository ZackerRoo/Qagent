from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from qagent.historical_evidence import providers as historical_providers
from qagent.config import Settings
from qagent.db import create_session_factory, initialize_database
from qagent.jobs import historical_data as historical_job
from qagent.storage.repository import QagentRepository


class FakeResult:
    def __init__(self, fields, rows, error_code="0", error_msg="success"):
        self.fields = fields
        self.rows = rows
        self.error_code = error_code
        self.error_msg = error_msg
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class FakeBaoStockFundamentalClient:
    profit_fields = [
        "code",
        "pubDate",
        "statDate",
        "roeAvg",
        "npMargin",
        "gpMargin",
        "netProfit",
        "epsTTM",
        "MBRevenue",
        "totalShare",
        "liqaShare",
    ]
    growth_fields = [
        "code",
        "pubDate",
        "statDate",
        "YOYEquity",
        "YOYAsset",
        "YOYNI",
        "YOYEPSBasic",
        "YOYPNI",
    ]

    def __init__(self):
        self.financial_calls = []

    def login(self):
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout(self):
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_profit_data(self, code, year=None, quarter=None):
        self.financial_calls.append(("profit", code, int(year), int(quarter)))
        rows = {
            (2024, 4): [
                [
                    code,
                    "2025-04-01",
                    "2024-12-31",
                    "0.25",
                    "0.45",
                    "0.80",
                    "80",
                    "8",
                    "100",
                    "10",
                    "10",
                ]
            ],
            (2025, 4): [
                [
                    code,
                    "2026-04-01",
                    "2025-12-31",
                    "0.30",
                    "0.50",
                    "0.90",
                    "92",
                    "9.2",
                    "120",
                    "10",
                    "10",
                ]
            ],
        }.get((int(year), int(quarter)), [])
        return FakeResult(self.profit_fields, rows)

    def query_growth_data(self, code, year=None, quarter=None):
        self.financial_calls.append(("growth", code, int(year), int(quarter)))
        rows = {
            (2024, 4): [[code, "2025-04-01", "2024-12-31", "", "", "0", "", ""]],
            (2025, 4): [[code, "2026-04-01", "2025-12-31", "", "", "0.15", "", ""]],
        }.get((int(year), int(quarter)), [])
        return FakeResult(self.growth_fields, rows)

    def query_history_k_data_plus(self, code, fields, **kwargs):
        assert fields == "date,close,peTTM,psTTM"
        return FakeResult(
            ["date", "close", "peTTM", "psTTM"],
            [
                ["2025-03-31", "10", "12", "2"],
                ["2026-03-31", "20", "10", "3"],
                ["2026-04-02", "30", "9", "4"],
            ],
        )


def test_baostock_historical_fundamentals_are_point_in_time_and_skip_etfs():
    client = FakeBaoStockFundamentalClient()
    provider_class = historical_providers.BaoStockHistoricalFundamentalProvider
    provider = provider_class(client=client, request_timeout_seconds=1)

    snapshots = provider.get_fundamentals(
        ["CN:600519", "CN:510300"],
        date(2025, 1, 1),
        date(2026, 7, 9),
    )

    latest = max(snapshots, key=lambda item: item.as_of_date)
    earliest = min(snapshots, key=lambda item: item.as_of_date)
    assert earliest.earnings_growth_pct == Decimal("0")
    assert latest.instrument_id == "CN:600519"
    assert latest.as_of_date == date(2026, 4, 1)
    assert latest.revenue_growth_pct == Decimal("20.0")
    assert latest.earnings_growth_pct == Decimal("15.00")
    assert latest.gross_margin_pct == Decimal("90.00")
    assert latest.net_margin_pct == Decimal("50.00")
    assert latest.return_on_equity_pct == Decimal("30.00")
    assert latest.market_cap == Decimal("200")
    assert latest.pe_ratio == Decimal("10")
    assert latest.price_to_sales == Decimal("3")
    assert latest.provider == "baostock_point_in_time"
    assert all(call[1] == "sh.600519" for call in client.financial_calls)


class FakeFinancialSummaryClient:
    def __init__(self):
        self.calls = []

    def stock_financial_abstract_ths(self, symbol, indicator):
        self.calls.append((symbol, indicator))
        return pd.DataFrame(
            [
                {
                    "报告期": "2023-03-31",
                    "净利润": "10亿",
                    "营业总收入": "100亿",
                },
                {
                    "报告期": "2023-12-31",
                    "净利润": "50亿",
                    "营业总收入": "500亿",
                },
                {
                    "报告期": "2024-03-31",
                    "净利润": "20亿",
                    "净利润同比增长率": "100%",
                    "营业总收入": "120亿",
                    "营业总收入同比增长率": "20%",
                    "销售毛利率": "45%",
                    "销售净利率": "16.67%",
                    "净资产收益率": "8%",
                },
                {
                    "报告期": "2024-12-31",
                    "净利润": "80亿",
                    "净利润同比增长率": "60%",
                    "营业总收入": "600亿",
                    "营业总收入同比增长率": "20%",
                    "销售毛利率": "48%",
                    "销售净利率": "13.33%",
                    "净资产收益率": "22%",
                },
            ]
        )


class FakeShareStructureClient:
    def __init__(self):
        self.calls = []

    def stock_zh_a_gbjg_em(self, symbol):
        self.calls.append(symbol)
        return pd.DataFrame(
            [
                {"变更日期": "2020-01-01", "总股本": "100000000"},
                {"变更日期": "2025-05-01", "总股本": "200000000"},
            ]
        )


def test_fast_historical_fundamentals_join_cached_prices_without_lookahead():
    financial_client = FakeFinancialSummaryClient()
    share_client = FakeShareStructureClient()
    provider = historical_providers.BaoStockHistoricalFundamentalProvider(
        client=FakeBaoStockFundamentalClient(),
        request_timeout_seconds=1,
        financial_client=financial_client,
        share_structure_client=share_client,
    )
    bars = pd.DataFrame(
        [
            {
                "instrument_id": "CN:600519",
                "trade_date": date(2024, 4, 29),
                "close": Decimal("12"),
            },
            {
                "instrument_id": "CN:600519",
                "trade_date": date(2024, 5, 6),
                "close": Decimal("99"),
            },
            {
                "instrument_id": "CN:600519",
                "trade_date": date(2025, 4, 30),
                "close": Decimal("16"),
            },
        ]
    )

    snapshots = provider.get_fundamentals_from_cached_bars(
        ["CN:600519", "CN:510300"],
        date(2024, 1, 1),
        date(2025, 12, 31),
        bars,
    )

    q1 = next(item for item in snapshots if item.as_of_date == date(2024, 4, 30))
    annual = next(item for item in snapshots if item.as_of_date == date(2025, 4, 30))
    assert q1.market_cap == Decimal("1200000000")
    assert q1.pe_ratio == Decimal("0.2")
    assert q1.price_to_sales == Decimal("0.02307692307692307692307692308")
    assert q1.earnings_growth_pct == Decimal("100")
    assert q1.provider == "akshare_ths_em_conservative_pit"
    assert annual.market_cap == Decimal("1600000000")
    assert annual.pe_ratio == Decimal("0.2")
    assert financial_client.calls == [("600519", "按报告期")]
    assert share_client.calls == ["600519.SH"]


def test_historical_job_uses_dedicated_point_in_time_fundamental_provider(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'historical-fundamental-job.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:600519"],
        date(2025, 1, 1),
        date(2026, 7, 9),
    )
    point_in_time_provider = object()
    captured = {}

    monkeypatch.setattr(
        historical_job,
        "build_historical_fundamental_provider",
        lambda _mode: point_in_time_provider,
        raising=False,
    )
    monkeypatch.setattr(
        historical_job,
        "build_market_data_provider",
        lambda _mode: object(),
    )
    monkeypatch.setattr(
        historical_job,
        "build_historical_evidence_provider",
        lambda _mode: None,
    )

    def fake_run_historical_backfill(**kwargs):
        captured.update(kwargs)
        return "completed"

    monkeypatch.setattr(
        historical_job,
        "run_historical_backfill",
        fake_run_historical_backfill,
    )

    result = historical_job.run_historical_backfill_job(job.job_id)

    assert result == "completed"
    assert captured["strategy_provider"] is point_in_time_provider


def test_historical_job_adds_fuyao_current_snapshot_only_when_range_contains_today(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'fuyao-current-overlay.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    today = date.today()
    job = repo.create_historical_backfill_job(
        "free",
        ["CN:600519"],
        today,
        today,
    )

    class HistoricalProvider:
        name = "historical"
        last_errors = []

        def get_fundamentals(self, instrument_ids, start, end):
            return []

    historical = HistoricalProvider()
    current = SimpleNamespace(name="fuyao_current", last_errors=[])
    captured = {}
    monkeypatch.setattr(
        historical_job,
        "build_historical_fundamental_provider",
        lambda _mode: historical,
    )
    monkeypatch.setattr(
        historical_job,
        "get_settings",
        lambda: Settings(fuyao_api_key="fuyao-key"),
    )

    def fake_strategy_builder(_mode, _settings=None, **kwargs):
        assert kwargs == {"include_fuyao_current_snapshot": True}
        return current

    monkeypatch.setattr(historical_job, "build_strategy_data_provider", fake_strategy_builder)
    monkeypatch.setattr(historical_job, "build_market_data_provider", lambda _mode: object())
    monkeypatch.setattr(
        historical_job,
        "build_historical_evidence_provider",
        lambda _mode: None,
    )
    monkeypatch.setattr(
        historical_job,
        "run_historical_backfill",
        lambda **kwargs: captured.update(kwargs) or "completed",
    )

    assert historical_job.run_historical_backfill_job(job.job_id) == "completed"
    overlay = captured["strategy_provider"]
    assert overlay.historical is historical
    assert overlay.current is current
    assert "known_at_retrieval_date" in overlay.current_snapshot_temporal_semantics


def test_historical_job_uses_fuyao_current_provider_without_dedicated_history(
    tmp_path,
    monkeypatch,
):
    database_url = f"sqlite:///{tmp_path / 'fuyao-current-direct.db'}"
    monkeypatch.setenv("QAGENT_DATABASE_URL", database_url)
    initialize_database(database_url)
    repo = QagentRepository(create_session_factory(database_url))
    today = date.today()
    job = repo.create_historical_backfill_job(
        "development",
        ["CN:600519"],
        today,
        today,
    )
    current = SimpleNamespace(name="fuyao_current", last_errors=[])
    captured = {}
    monkeypatch.setattr(
        historical_job,
        "build_historical_fundamental_provider",
        lambda _mode: None,
    )
    monkeypatch.setattr(
        historical_job,
        "get_settings",
        lambda: Settings(fuyao_api_key="fuyao-key"),
    )

    def fake_strategy_builder(_mode, _settings=None, **kwargs):
        assert kwargs == {"include_fuyao_current_snapshot": True}
        return current

    monkeypatch.setattr(historical_job, "build_strategy_data_provider", fake_strategy_builder)
    monkeypatch.setattr(historical_job, "build_market_data_provider", lambda _mode: object())
    monkeypatch.setattr(
        historical_job,
        "build_historical_evidence_provider",
        lambda _mode: None,
    )
    monkeypatch.setattr(
        historical_job,
        "run_historical_backfill",
        lambda **kwargs: captured.update(kwargs) or "completed",
    )

    assert historical_job.run_historical_backfill_job(job.job_id) == "completed"
    assert captured["strategy_provider"] is current
