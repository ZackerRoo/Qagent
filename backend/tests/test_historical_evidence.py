from datetime import date
import socket

from qagent.historical_evidence.models import HistoricalEvidenceBundle
from qagent.historical_evidence.providers import BaoStockHistoricalEvidenceProvider


class FakeResult:
    def __init__(
        self,
        fields,
        rows,
        error_code="0",
        error_msg="",
        expected_timeout=None,
    ):
        self.fields = fields
        self.rows = rows
        self.error_code = error_code
        self.error_msg = error_msg
        self.expected_timeout = expected_timeout
        self._index = -1

    def next(self):
        if self.expected_timeout is not None:
            assert socket.getdefaulttimeout() == self.expected_timeout
        self._index += 1
        return self._index < len(self.rows)

    def get_row_data(self):
        return self.rows[self._index]


class FakeBaoStock:
    def __init__(self):
        self.logged_in = False
        self.logged_out = False

    def login(self):
        self.logged_in = True
        return FakeResult([], [])

    def logout(self):
        self.logged_out = True

    def query_history_k_data_plus(
        self,
        code,
        fields,
        start_date,
        end_date,
        frequency,
        adjustflag,
    ):
        assert fields == "date,code,tradestatus,pctChg,isST"
        assert frequency == "d"
        assert adjustflag == "2"
        rows = {
            "sz.000001": [
                ["2026-01-05", code, "1", "1.25", "0"],
                ["2026-01-06", code, "0", "", "0"],
            ],
            "sh.600519": [["2026-01-05", code, "1", "-0.50", "1"]],
        }[code]
        return FakeResult(
            ["date", "code", "tradestatus", "pctChg", "isST"],
            rows,
        )

    def query_stock_basic(self, code="", code_name=""):
        assert code == ""
        return FakeResult(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [
                ["sz.000001", "平安银行", "1991-04-03", "", "1", "1"],
                ["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"],
                ["sz.000002", "万科A", "1991-01-29", "2025-12-31", "1", "0"],
                ["sh.510300", "沪深300ETF", "2012-05-28", "", "5", "1"],
                ["sh.000300", "沪深300指数", "2005-04-08", "", "2", "1"],
            ],
            expected_timeout=1,
        )

    def query_stock_industry(self, code="", date=""):
        assert code == ""
        return FakeResult(
            ["updateDate", "code", "code_name", "industry", "industryClassification"],
            [
                [date, "sz.000001", "平安银行", "银行", "申万一级行业"],
                [date, "sh.600519", "贵州茅台", "食品饮料", "申万一级行业"],
            ],
        )

    def query_hs300_stocks(self, date=""):
        return FakeResult(
            ["updateDate", "code", "code_name"],
            [[date, "sz.000001", "平安银行"]],
        )

    def query_zz500_stocks(self, date=""):
        return FakeResult(
            ["updateDate", "code", "code_name"],
            [[date, "sh.600519", "贵州茅台"]],
        )

    def query_sz50_stocks(self, date=""):
        return FakeResult(["updateDate", "code", "code_name"], [])


def test_baostock_historical_evidence_provider_normalizes_all_evidence_classes():
    client = FakeBaoStock()
    provider = BaoStockHistoricalEvidenceProvider(
        client=client,
        request_timeout_seconds=1,
    )

    bundle = provider.get_evidence(
        ["CN:000001", "CN:600519"],
        date(2026, 1, 1),
        date(2026, 1, 9),
    )

    assert isinstance(bundle, HistoricalEvidenceBundle)
    assert client.logged_in is True
    assert client.logged_out is True
    assert len(bundle.tradability) == 3
    assert bundle.tradability[1].trading_status == "suspended"
    assert bundle.tradability[2].is_st is True
    assert {item.instrument_id for item in bundle.profiles} == {
        "CN:000001",
        "CN:000002",
        "CN:600519",
        "CN:510300",
    }
    assert bundle.profiles[0].name
    assert bundle.profiles[0].listing_date == date(1991, 4, 3)
    assert {item.industry for item in bundle.industries} == {"银行", "食品饮料"}
    assert {item.index_id for item in bundle.index_snapshots} == {
        "CN:000016.IDX",
        "CN:000300.IDX",
        "CN:000905.IDX",
    }
    assert len(bundle.index_memberships) == 2
    assert sum(item.status == "ready" for item in bundle.index_snapshots) == 2
    assert sum(item.status == "failed" for item in bundle.index_snapshots) == 1
    assert bundle.errors == ["index CN:000016.IDX 2026-01-09: empty membership snapshot"]
