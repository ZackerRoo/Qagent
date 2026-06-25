from types import SimpleNamespace

import pandas as pd

from qagent.market.a_share_universe import (
    CN_ALL_TOKEN,
    build_a_share_universe,
    resolve_symbol_tokens,
)
from qagent.market.instruments import format_instrument_label


def test_build_a_share_universe_filters_and_ranks_candidates(monkeypatch):
    def fake_spot():
        return pd.DataFrame(
            {
                "代码": ["000001", "000002", "600000", "300001", "688001", "830001"],
                "名称": ["平安银行", "*ST测试", "浦发银行", "特锐德", "华兴源创", "北交样例"],
                "最新价": [10.2, 4.5, 7.8, 0.8, 23.5, 12.0],
                "成交额": [900_000_000, 500_000_000, 120_000_000, 200_000_000, 850_000_000, 50_000_000],
                "涨跌幅": [1.2, -1.0, 0.5, 2.0, 3.5, 1.1],
            }
        )

    monkeypatch.setattr("qagent.market.a_share_universe.ak", SimpleNamespace(stock_zh_a_spot_em=fake_spot))

    result = build_a_share_universe(limit=3, min_turnover=100_000_000)

    assert result.total_count == 6
    assert result.eligible_count == 3
    assert result.symbols == ["CN:000001", "CN:688001", "CN:600000"]
    assert result.names["000001"] == "平安银行"
    assert result.excluded_counts["st_or_delisting"] == 1
    assert result.excluded_counts["low_price"] == 1
    assert result.excluded_counts["low_turnover"] == 1


def test_resolve_symbol_tokens_expands_cn_all_and_registers_names(monkeypatch):
    def fake_spot():
        return pd.DataFrame(
            {
                "代码": ["000001", "600000"],
                "名称": ["平安银行", "浦发银行"],
                "最新价": [10.2, 7.8],
                "成交额": [900_000_000, 120_000_000],
            }
        )

    monkeypatch.setattr("qagent.market.a_share_universe.ak", SimpleNamespace(stock_zh_a_spot_em=fake_spot))

    resolved = resolve_symbol_tokens([CN_ALL_TOKEN], limit=2, min_turnover=100_000_000)

    assert resolved.symbols == ["CN:000001", "CN:600000"]
    assert resolved.data_health["universe_total"] == "2"
    assert resolved.data_health["universe_eligible"] == "2"
    assert resolved.data_health["universe_selected"] == "2"
    assert resolved.data_health["universe_source"] == "akshare_spot_em"
    assert format_instrument_label("CN:600000") == "浦发银行 600000.SH"


def test_resolve_symbol_tokens_keeps_manual_symbols_without_dynamic_metadata():
    resolved = resolve_symbol_tokens(["CN:000001", "CN:000001", "CN:600519"])

    assert resolved.symbols == ["CN:000001", "CN:600519"]
    assert resolved.data_health == {}


def test_resolve_symbol_tokens_falls_back_to_starter_when_full_universe_fails(monkeypatch):
    def fake_spot():
        raise ConnectionError("rate limited")

    monkeypatch.setattr("qagent.market.a_share_universe.ak", SimpleNamespace(stock_zh_a_spot_em=fake_spot))

    resolved = resolve_symbol_tokens([CN_ALL_TOKEN], limit=3)

    assert resolved.symbols == ["CN:000001", "CN:000063", "CN:000333"]
    assert resolved.data_health["universe"] == CN_ALL_TOKEN
    assert resolved.data_health["universe_fallback"] == "cn_liquid_starter"
    assert "rate limited" in resolved.data_health["universe_error"]
