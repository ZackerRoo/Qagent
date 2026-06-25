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


def test_resolves_theme_universe_from_concept_board(monkeypatch):
    def fake_stock_board_concept_cons_em(symbol):
        assert symbol == "半导体"
        return pd.DataFrame(
            {
                "代码": ["688981", "688012", "603986"],
                "名称": ["中芯国际", "中微公司", "兆易创新"],
            }
        )

    monkeypatch.setattr(
        "qagent.market.cn_universe_tokens.ak",
        SimpleNamespace(stock_board_concept_cons_em=fake_stock_board_concept_cons_em),
    )

    result = resolve_cn_universe_token("CN:THEME:SEMICONDUCTOR", limit=2)

    assert result.symbols == ["CN:688981", "CN:688012"]
    assert result.names["688981"] == "中芯国际"
    assert result.data_health["universe"] == "CN:THEME:SEMICONDUCTOR"
    assert result.data_health["universe_label"] == "半导体芯片主题"
    assert result.data_health["universe_source"] == "akshare_stock_board_concept_cons_em"
    assert format_instrument_label("CN:603986") == "兆易创新 603986.SH"


def test_theme_token_falls_back_to_representative_constituents(monkeypatch):
    def fake_stock_board_concept_cons_em(symbol):
        raise ConnectionError("concept source down")

    monkeypatch.setattr(
        "qagent.market.cn_universe_tokens.ak",
        SimpleNamespace(stock_board_concept_cons_em=fake_stock_board_concept_cons_em),
    )

    result = resolve_cn_universe_token("CN:THEME:MEMORY", limit=3)

    assert result.symbols == ["CN:688008", "CN:603986", "CN:688525"]
    assert result.data_health["universe_label"] == "存储芯片主题"
    assert result.data_health["universe_fallback"] == "builtin_theme_representative"
    assert "concept source down" in result.data_health["universe_error"]


def test_builtin_universes_include_index_and_etf_entries():
    by_id = {universe.universe_id: universe for universe in builtin_universes()}

    assert by_id["cn_index_kcb50"].symbols == ["CN:INDEX:KCB50"]
    assert by_id["cn_index_csi300"].symbols == ["CN:INDEX:CSI300"]
    assert "CN:ETF:KCB50" in by_id["cn_etf_core"].symbols
    assert by_id["cn_etf_core"].market_scope == "CN"
    assert by_id["cn_theme_semiconductor"].symbols == ["CN:THEME:SEMICONDUCTOR"]
    assert by_id["cn_theme_memory"].symbols == ["CN:THEME:MEMORY"]
    assert by_id["cn_theme_ai_compute"].symbols == ["CN:THEME:AI_COMPUTE"]
