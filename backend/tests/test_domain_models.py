from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import Instrument


def test_instrument_id_for_us_stock():
    instrument = Instrument(
        market=Market.US,
        symbol="AAPL",
        exchange="NASDAQ",
        name="Apple Inc.",
        currency="USD",
        timezone="America/New_York",
        trading_calendar="XNYS",
    )
    assert instrument.instrument_id == "US:AAPL"


def test_instrument_id_for_cn_stock_keeps_leading_zeroes():
    instrument = Instrument(
        market=Market.CN,
        symbol="000001",
        exchange="SZSE",
        name="平安银行",
        currency="CNY",
        timezone="Asia/Shanghai",
        trading_calendar="XSHG_XSHE",
    )
    assert instrument.instrument_id == "CN:000001"


def test_opportunity_status_values_are_stable():
    assert OpportunityStatus.SETUP_READY.value == "setup_ready"
    assert OpportunityStatus.INVALIDATED.value == "invalidated"
