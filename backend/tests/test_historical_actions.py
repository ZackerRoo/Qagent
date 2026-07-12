from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd

from qagent.historical_evidence.providers import BaoStockHistoricalEvidenceProvider


class CorporateActionClient:
    def stock_dividend_cninfo(self, *, symbol):
        assert symbol == "000001"
        return pd.DataFrame(
            [
                {
                    "实施方案公告日期": "2024-06-01",
                    "送股比例": 1,
                    "转增比例": 2,
                    "派息比例": 3,
                    "股权登记日": "2024-06-10",
                    "除权日": "2024-06-11",
                    "派息日": "2024-06-12",
                    "股份到账日": "2024-06-11",
                    "报告时间": "2023年报",
                }
            ]
        )

    def stock_history_dividend_detail(self, *, symbol, indicator):
        assert symbol == "000001"
        assert indicator == "配股"
        return pd.DataFrame(
            [
                {
                    "公告日期": "2024-07-01",
                    "配股方案": 2,
                    "配股价格": 7.5,
                    "除权日": "2024-07-10",
                    "股权登记日": "2024-07-09",
                    "配股上市日": "2024-07-20",
                }
            ]
        )


class IncompleteCorporateActionClient(CorporateActionClient):
    def stock_dividend_cninfo(self, *, symbol):
        return pd.DataFrame(
            [
                {
                    "实施方案公告日期": None,
                    "派息比例": 3,
                    "股权登记日": "2024-06-10",
                    "除权日": "2024-06-11",
                    "派息日": "2024-06-12",
                }
            ]
        )


def _provider(client):
    return BaoStockHistoricalEvidenceProvider(
        corporate_action_client=client,
        clock=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


def test_corporate_action_provider_maps_dividends_splits_bonus_and_rights():
    batch = _provider(CorporateActionClient()).get_corporate_actions(
        ["CN:000001", "CN:510300"],
        date(2024, 1, 1),
        date(2024, 12, 31),
    )

    stock_coverage = next(
        item for item in batch.coverage if item.instrument_id == "CN:000001"
    )
    etf_coverage = next(
        item for item in batch.coverage if item.instrument_id == "CN:510300"
    )
    by_type = {item.action_type: item for item in batch.actions}

    assert stock_coverage.status == "ready"
    assert stock_coverage.action_count == 4
    assert etf_coverage.status == "unsupported"
    assert set(by_type) == {"cash_dividend", "bonus", "split", "rights"}
    assert by_type["cash_dividend"].cash_per_share == Decimal("0.3")
    assert by_type["cash_dividend"].payable_date == date(2024, 6, 12)
    assert by_type["bonus"].share_ratio == Decimal("0.1")
    assert by_type["split"].share_ratio == Decimal("0.2")
    assert by_type["rights"].rights_ratio == Decimal("0.2")
    assert by_type["rights"].subscription_price == Decimal("7.5")
    assert batch.errors == []


def test_incomplete_action_rows_make_coverage_partial_without_fabricated_dates():
    batch = _provider(IncompleteCorporateActionClient()).get_corporate_actions(
        ["CN:000001"],
        date(2024, 1, 1),
        date(2024, 12, 31),
    )

    assert batch.coverage[0].status == "partial"
    assert batch.coverage[0].action_count == 1
    assert all(item.action_type == "rights" for item in batch.actions)
    assert any("announcement date is missing" in item for item in batch.errors)
