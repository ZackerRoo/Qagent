from qagent.market.localization import (
    localize_action,
    localize_caveat,
    localize_factor_flag,
    localize_strategy,
)


def test_localizes_research_display_terms_to_chinese():
    assert localize_action("watch_trigger") == "等待触发"
    assert localize_strategy("healthy_pullback") == "健康回调"
    assert localize_factor_flag("overextended") == "短线过热"
    assert localize_caveat("provider: baostock") == "数据源：baostock"
    assert localize_caveat("fixture data") == "样例数据"
