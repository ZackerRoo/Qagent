from types import SimpleNamespace

import pandas as pd

from qagent.market.a_share_universe import resolve_symbol_tokens
from qagent.market.cn_universe_tokens import resolve_cn_universe_token
from qagent.market.instruments import format_instrument_label
from qagent.market.universes import builtin_universes


def test_resolves_star_50_index_constituents_from_akshare(monkeypatch):
    def fake_index_stock_cons_csindex(symbol):
        assert symbol == "000688"
        return pd.DataFrame(
            {
                "品种代码": ["688111", "688981"],
                "品种名称": ["金山办公", "中芯国际"],
            }
        )

    monkeypatch.setattr(
        "qagent.market.cn_universe_tokens.ak",
        SimpleNamespace(index_stock_cons_csindex=fake_index_stock_cons_csindex),
    )

    result = resolve_cn_universe_token("CN:INDEX:KCB50", limit=10)

    assert result.symbols == ["CN:688111", "CN:688981"]
    assert result.names["688111"] == "金山办公"
    assert result.data_health["universe"] == "CN:INDEX:KCB50"
    assert result.data_health["universe_label"] == "科创50成分股"
    assert result.data_health["universe_source"] == "akshare_index_stock_cons_csindex"
    assert format_instrument_label("CN:688981") == "中芯国际 688981.SH"


def test_index_token_falls_back_to_representative_constituents(monkeypatch):
    def fake_index_stock_cons_csindex(symbol):
        raise ConnectionError("index source down")

    monkeypatch.setattr(
        "qagent.market.cn_universe_tokens.ak",
        SimpleNamespace(index_stock_cons_csindex=fake_index_stock_cons_csindex),
    )

    result = resolve_cn_universe_token("CN:INDEX:KCB50", limit=3)

    assert result.symbols == ["CN:688981", "CN:688111", "CN:688012"]
    assert result.data_health["universe_fallback"] == "builtin_index_representative"
    assert "index source down" in result.data_health["universe_error"]


def test_resolve_symbol_tokens_expands_index_and_etf_tokens(monkeypatch):
    def fake_index_stock_cons_csindex(symbol):
        assert symbol == "000300"
        return pd.DataFrame(
            {
                "成分券代码": ["600519", "300750"],
                "成分券名称": ["贵州茅台", "宁德时代"],
            }
        )

    monkeypatch.setattr(
        "qagent.market.cn_universe_tokens.ak",
        SimpleNamespace(index_stock_cons_csindex=fake_index_stock_cons_csindex),
    )

    resolved = resolve_symbol_tokens(["CN:INDEX:CSI300", "CN:ETF:KCB50", "CN:000001"], limit=20)

    assert resolved.symbols == ["CN:000001", "CN:600519", "CN:300750", "CN:588000"]
    assert resolved.data_health["universe"] == "CN:INDEX:CSI300,CN:ETF:KCB50"
    assert resolved.data_health["universe_selected"] == "3"
    assert resolved.is_dynamic is True
    assert format_instrument_label("CN:588000") == "科创50ETF 588000.SH"


def test_builtin_universes_include_index_and_etf_entries():
    by_id = {universe.universe_id: universe for universe in builtin_universes()}

    assert by_id["cn_index_kcb50"].symbols == ["CN:INDEX:KCB50"]
    assert by_id["cn_index_csi300"].symbols == ["CN:INDEX:CSI300"]
    assert "CN:ETF:KCB50" in by_id["cn_etf_core"].symbols
    assert by_id["cn_etf_core"].market_scope == "CN"
