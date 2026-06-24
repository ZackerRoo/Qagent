from qagent.market.instruments import format_instrument_label, market_symbol


def test_formats_known_a_share_with_company_name_and_exchange_suffix():
    assert format_instrument_label("CN:000063") == "中兴通讯 000063.SZ"


def test_formats_unknown_a_share_without_internal_prefix():
    assert format_instrument_label("CN:688001") == "688001.SH"


def test_formats_us_symbol_without_internal_prefix():
    assert format_instrument_label("US:NVDA") == "NVIDIA NVDA"
    assert market_symbol("US:NVDA") == "NVDA"
