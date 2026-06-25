from qagent.market.instruments import format_instrument_label, market_symbol


def test_formats_known_a_share_with_company_name_and_exchange_suffix():
    assert format_instrument_label("CN:000063") == "中兴通讯 000063.SZ"


def test_formats_unknown_a_share_without_internal_prefix():
    assert format_instrument_label("CN:688999") == "688999.SH"
    assert format_instrument_label("CN:920580") == "920580.BJ"


def test_formats_us_symbol_without_internal_prefix():
    assert format_instrument_label("US:NVDA") == "NVIDIA NVDA"
    assert market_symbol("US:NVDA") == "NVDA"


def test_formats_cn_index_tokens_and_etfs():
    assert format_instrument_label("CN:INDEX:KCB50") == "科创50成分股"
    assert format_instrument_label("CN:ETF:KCB50") == "科创50ETF"
    assert format_instrument_label("CN:588000") == "科创50ETF 588000.SH"
